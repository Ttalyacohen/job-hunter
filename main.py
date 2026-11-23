#!/usr/bin/env python3
"""24/7 job hunter: scrape -> rule filter -> Gemini screen -> tailored CV -> Telegram.

Usage:
  python main.py                    # normal run (what GitHub Actions executes)
  python main.py --dry-run          # no Telegram, no state writes; PDFs go to output/
  python main.py --check-sources    # scan every board and print a health table
  python main.py --render-samples   # render one sample CV per track into output/samples/
  python main.py --test-telegram    # send a test alert + sample CV to your chat
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config import DATA_DIR, ROOT, load_companies, load_filters, load_profile, load_settings
from core.http import HttpClient
from core.models import Evaluation, Job
from core.state import BOOTSTRAP, FILTERED, NOTIFIED, REJECTED, RunState, SeenJobs
from cv_generator import CVGenerator
from matcher.gemini import Evaluator, GeminiClient, NoModelsAvailable, QuotaExhausted, unscreened_evaluation
from matcher.rules import RuleFilter
from notifier.telegram_bot import TelegramError, TelegramNotifier, format_alert
from scraper import Scraper

log = logging.getLogger("job_hunter")

OUTBOX = "o"          # seen-status: alert pending re-delivery
GAVE_UP = "x"         # seen-status: delivery failed permanently
MAX_OUTBOX_ATTEMPTS = 5
MAX_ALERTS_PER_RUN = 15


# ─────────────────────────────────────────────────────────────────────────────
def recency_key(job: Job) -> float:
    """Sort key: newer first. Understands ISO dates and Workday 'Posted 3 Days Ago'."""
    s = (job.posted_at or "").strip()
    now = datetime.now(timezone.utc)
    if not s:
        return 0.0
    low = s.lower()
    if "today" in low or "just posted" in low:
        return now.timestamp()
    if "yesterday" in low:
        return (now - timedelta(days=1)).timestamp()
    m = re.search(r"(\d+)\+?\s*days?\s*ago", low)
    if m:
        return (now - timedelta(days=int(m.group(1)))).timestamp()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return 0.0


DUPLICATE = "d"         # seen-status: same company + same title already alerted recently
DUPLICATE_WINDOW_DAYS = 30


def title_signature(job: Job) -> str:
    """Same role re-posted for another city (e.g. Mobileye Jerusalem / Ramat Gan) -> same signature."""
    return f"{job.company_id}|{re.sub(r'[^a-z0-9א-ת]+', ' ', job.title.lower()).strip()}"


def write_step_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
class Pipeline:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.settings = load_settings()
        self.filters = load_filters()
        self.companies = load_companies()
        if args.companies:
            wanted = {c.strip().lower() for c in args.companies.split(",")}
            self.companies = [c for c in self.companies if c["name"].lower() in wanted or c["id"] in wanted]
        self.company_by_id = {c["id"]: c for c in self.companies}
        self.profile = load_profile()
        self.seen = SeenJobs(DATA_DIR / "seen_jobs.json")
        self.state = RunState(DATA_DIR / "state.json")
        self.rules = RuleFilter(self.filters, self.settings.pipeline["country_only_israel_counts"],
                                self.settings.pipeline.get("center_hybrid", False))
        self.cv = CVGenerator(self.profile, self.settings.cv)
        self.out_dir = ROOT / self.settings.cv.get("output_dir", "output")
        self.notifier: TelegramNotifier | None = None
        if not args.dry_run and self.settings.telegram_token and self.settings.telegram_chat_id:
            self.notifier = TelegramNotifier(self.settings.telegram_token, self.settings.telegram_chat_id)
        self.counts = {"scraped": 0, "new": 0, "filtered": 0, "candidates": 0, "evaluated": 0,
                       "relevant": 0, "rejected": 0, "notified": 0, "queued": 0, "bootstrap": 0}
        self.alerts_sent = 0

    # ---- delivery ------------------------------------------------------------
    async def deliver(self, job: Job, ev: Evaluation) -> bool:
        pdf = None
        try:
            pdf = self.cv.generate(job, ev, self.out_dir)
        except Exception as exc:   # still alert without the attachment
            log.exception("CV generation failed for %s / %s: %s", job.company, job.title, exc)

        if self.args.dry_run or self.notifier is None:
            print("\n" + "─" * 70 + "\n" + re.sub(r"<[^>]+>", "", format_alert(job, ev)))
            print(f"   CV: {pdf}")
            if not self.args.dry_run:
                log.warning("Telegram secrets missing — alert printed only")
            return not self.args.dry_run   # in dry-run, never mark as seen

        try:
            await self.notifier.send_job(job, ev, pdf)
            self.seen.mark(job.key, NOTIFIED, job.title)
            self.state.data.setdefault("sent_titles", {})[title_signature(job)] = \
                datetime.now(timezone.utc).date().isoformat()
            self.counts["notified"] += 1
            return True
        except TelegramError as exc:
            log.error("Telegram delivery failed (%s) — queued for retry", exc)
            self.state.outbox.append({"job": job.to_dict(), "evaluation": ev.to_dict(), "attempts": 1})
            self.seen.mark(job.key, OUTBOX, job.title)
            return False

    async def flush_outbox(self) -> None:
        if not self.notifier or not self.state.outbox:
            return
        remaining = []
        for item in self.state.outbox:
            job, ev = Job.from_dict(item["job"]), Evaluation.from_dict(item["evaluation"])
            try:
                pdf = self.cv.generate(job, ev, self.out_dir)
                await self.notifier.send_job(job, ev, pdf)
                self.seen.mark(job.key, NOTIFIED, job.title)
                self.counts["notified"] += 1
            except Exception as exc:
                item["attempts"] += 1
                if item["attempts"] >= MAX_OUTBOX_ATTEMPTS:
                    log.error("Giving up on alert for %s / %s: %s", job.company, job.title, exc)
                    self.seen.mark(job.key, GAVE_UP, job.title)
                else:
                    remaining.append(item)
        self.state.outbox[:] = remaining

    async def ops_alert(self, name: str, text: str) -> None:
        log.error(text)
        if self.notifier and self.state.should_alert(name):
            try:
                await self.notifier.send_message(f"⚙️ <b>Job bot</b>: {text}")
                self.state.alerted(name)
            except TelegramError:
                pass

    # ---- main run ------------------------------------------------------------
    async def run(self) -> int:
        t0 = time.monotonic()
        scan = self.settings.scan
        async with HttpClient(scan["concurrency"], scan["request_timeout_s"], tuple(scan["jitter_s"]),
                              scan["max_retries"]) as http:
            scraper = Scraper(http, scan)
            result = await scraper.scan(self.companies)
            t_scan = time.monotonic() - t0
            self.counts["scraped"] = len(result.jobs)
            log.info("Scanned %d companies in %.1fs: %d postings (%d boards failed)",
                     len(result.stats), t_scan, len(result.jobs), len(result.failed_companies))

            if self.args.check_sources:
                self.print_sources(result.stats)
                return 0

            await self.flush_outbox()

            # ---- stage 1 ---------------------------------------------------------
            first_sighting = self.settings.pipeline["first_sighting"]
            candidates: list[Job] = []
            for job in result.jobs:
                if job.key in self.seen:
                    self.seen.touch(job.key)
                    continue
                self.counts["new"] += 1
                company = self.company_by_id.get(job.company_id, {})
                verdict = self.rules.check(job, company)
                if not verdict.ok:
                    self.seen.mark(job.key, FILTERED, job.title)
                    self.counts["filtered"] += 1
                    log.debug("stage1 drop: %s / %s — %s", job.company, job.title, verdict.reason)
                    continue
                if first_sighting == "silent" and self.state.company_is_new(job.company_id):
                    self.seen.mark(job.key, BOOTSTRAP, job.title)
                    self.counts["bootstrap"] += 1
                    continue
                candidates.append(job)

            for cid, st in result.stats.items():
                if not st["error"]:
                    self.state.mark_company_seen(cid)
            # keep jobs of temporarily broken boards from being pruned
            failed_prefixes = tuple(f":{cid}:" for cid in result.failed_companies)
            if failed_prefixes:
                for key in list(self.seen.jobs):
                    if any(p in key for p in failed_prefixes):
                        self.seen.touch(key)

            candidates.sort(key=recency_key, reverse=True)
            backlog = max(0, len(candidates) - self.settings.pipeline["max_candidates_per_run"])
            candidates = candidates[: self.settings.pipeline["max_candidates_per_run"]]

            # ---- enrich (descriptions / multi-location details) -------------------
            enriched = await asyncio.gather(*(scraper.enrich(j, self.company_by_id[j.company_id])
                                              for j in candidates))
            final: list[Job] = []
            for job in enriched:
                if job.extra.get("enrich_failed"):
                    backlog += 1          # never screen a job without its description; retry next run
                    continue
                company = self.company_by_id.get(job.company_id, {})
                verdict = self.rules.check(job, company, strict=True)
                if not verdict.ok:
                    self.seen.mark(job.key, FILTERED, job.title)
                    self.counts["filtered"] += 1
                    continue
                final.append(job)
            self.counts["candidates"] = len(final)
            log.info("Stage 1: %d new postings -> %d candidates (%d more waiting)",
                     self.counts["new"], len(final), backlog)

        # ---- stage 2 + delivery ---------------------------------------------------
        await self.stage2(final)

        if not self.args.dry_run:
            pruned = self.seen.prune(self.settings.pipeline["prune_after_days"])
            changed = self.seen.save() | self.state.save()
            log.info("State %s (pruned %d old entries)", "updated" if changed else "unchanged", pruned)

        self.counts["queued"] += backlog
        summary = (f"scraped={self.counts['scraped']} new={self.counts['new']} filtered={self.counts['filtered']} "
                   f"candidates={self.counts['candidates']} evaluated={self.counts['evaluated']} "
                   f"relevant={self.counts['relevant']} notified={self.counts['notified']} "
                   f"queued={self.counts['queued']} time={time.monotonic() - t0:.1f}s")
        log.info("Done: %s", summary)
        write_step_summary(["### Job scan", "", f"`{summary}`", "",
                            *[f"- ⚠️ {s['name']} ({s['ats']}): {s['error']}"
                              for s in result.stats.values() if s["error"]]])
        if self.notifier:
            await self.notifier.aclose()
        return 0

    def drop_duplicates(self, jobs: list[Job]) -> list[Job]:
        sent = self.state.data.setdefault("sent_titles", {})
        cutoff = (datetime.now(timezone.utc) - timedelta(days=DUPLICATE_WINDOW_DAYS)).date().isoformat()
        for sig in [k for k, d in sent.items() if d < cutoff]:
            del sent[sig]
        keep = []
        for job in jobs:
            if title_signature(job) in sent:
                if not self.args.dry_run:
                    self.seen.mark(job.key, DUPLICATE, job.title)
                log.info("duplicate of an alert already sent: %s / %s (%s)", job.company, job.title, job.location)
            else:
                keep.append(job)
        return keep

    async def stage2(self, jobs: list[Job]) -> None:
        jobs = self.drop_duplicates(jobs)
        if not jobs:
            return
        mode = self.settings.pipeline["on_quota_exhausted"]
        pending = list(jobs)

        if self.settings.gemini_api_key and not self.args.no_gemini:
            client = GeminiClient(self.settings.gemini_api_key, self.settings.gemini_models,
                                  self.state, self.settings.gemini)
            evaluator = Evaluator(client, self.profile, self.settings.gemini, self.settings.relevance)
            size = max(1, self.settings.gemini["jobs_per_call"])
            exhausted = False
            try:
                while pending and client.budget_left > 0 and self.alerts_sent < MAX_ALERTS_PER_RUN:
                    batch, pending = pending[:size], pending[size:]
                    try:
                        results = await evaluator.evaluate(batch)
                    except NoModelsAvailable as exc:
                        pending = batch + pending
                        exhausted = True
                        await self.ops_alert("no_models", f"{exc}. Update GEMINI_MODELS (see README).")
                        break
                    except QuotaExhausted as exc:
                        pending = batch + pending
                        exhausted = True
                        log.warning("%s — %d candidates stay queued", exc, len(pending))
                        break
                    except RuntimeError as exc:
                        pending = batch + pending
                        log.error("Gemini error: %s", exc)
                        break
                    for job in batch:
                        ev = results.get(job.key)
                        if ev is None:          # omitted by the model: retry next run
                            continue
                        self.counts["evaluated"] += 1
                        if ev.is_relevant:
                            self.counts["relevant"] += 1
                            await self.deliver(job, ev)
                            self.alerts_sent += 1
                        else:
                            self.counts["rejected"] += 1
                            if not self.args.dry_run:
                                self.seen.mark(job.key, REJECTED, job.title)
                            log.info("rejected (%s/10): %s / %s — %s", ev.match_score, job.company,
                                     job.title, ev.rejection_reason)
            finally:
                await client.aclose()
            if not (exhausted and mode == "notify_unscreened"):
                self.counts["queued"] += len(pending)
                return
        elif not self.args.no_gemini:
            log.warning("GEMINI_API_KEY not set — sending unscreened alerts")

        # keyword fallback (no key, --no-gemini, or quota exhausted + notify_unscreened)
        for job in pending:
            if self.alerts_sent >= MAX_ALERTS_PER_RUN:
                self.counts["queued"] += 1
                continue
            await self.deliver(job, unscreened_evaluation(job))
            self.alerts_sent += 1

    # ---- utilities -----------------------------------------------------------
    @staticmethod
    def print_sources(stats: dict) -> None:
        rows = sorted(stats.values(), key=lambda s: (bool(s["error"]), -s["count"], s["name"]))
        print(f"\n{'Company':<24}{'ATS':<16}{'Jobs':>6}{'Secs':>7}  Status")
        print("-" * 90)
        for s in rows:
            status = s["error"] or ("OK" if s["count"] else "OK but 0 jobs — check identifiers / JS-rendered page")
            print(f"{s['name'][:23]:<24}{str(s['ats'])[:15]:<16}{s['count']:>6}{s['seconds']:>7}  {status[:80]}")
        ok = sum(1 for s in rows if not s["error"] and s["count"])
        print(f"\n{ok}/{len(rows)} boards returned jobs.")


# ─────────────────────────────────────────────────────────────────────────────
SAMPLE_PLANS = {
    "AI_ML_RESEARCH": {"keywords": ["PyTorch", "LLMs", "computer vision"],
                       "focus_skills": ["PyTorch", "HuggingFace", "Transformers"]},
    "BACKEND_FULLSTACK": {"subtitle_id": "backend", "domain_line_id": "backend",
                          "project_order": ["prompt_injection", "real_estate", "thesis", "network_traffic", "radcom"],
                          "course_ids": ["oop", "ds", "os", "networks", "sysprog", "final_project"],
                          "keywords": ["FastAPI", "PostgreSQL", "Java"],
                          "focus_skills": ["Python", "Java", "FastAPI", "PostgreSQL", "Redis"]},
    "SYSTEMS_LOWLEVEL_CYBER": {"keywords": ["Wireshark", "TCP", "Linux"],
                               "focus_skills": ["C", "Linux", "TCP/IP", "Wireshark"]},
}


def render_samples() -> list[Path]:
    settings, profile = load_settings(), load_profile()
    gen = CVGenerator(profile, settings.cv)
    out = ROOT / "output" / "samples"
    paths = []
    for track, plan in SAMPLE_PLANS.items():
        p = gen.render_pdf(gen.plan(track, plan), out / f"{track.lower()}.pdf")
        paths.append(p)
        print(f"{track:<24} -> {p}")
    return paths


async def test_telegram() -> int:
    settings = load_settings()
    if not (settings.telegram_token and settings.telegram_chat_id):
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        return 2
    pdf = render_samples()[0]
    job = Job(company="Test Company", company_id="test", source="test", job_id="1",
              title="Junior ML Engineer (test alert)", url="https://example.com",
              location="Haifa, Israel", workplace="hybrid")
    ev = Evaluation(job_key=job.key, match_score=9, track="AI_ML_RESEARCH", is_relevant=True,
                    summary_bullets=["Test bullet: stack / research focus.",
                                     "Test bullet: responsibilities.",
                                     "Test bullet: why it fits."])
    bot = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
    try:
        await bot.send_job(job, ev, pdf)
        print("Sent ✔")
        return 0
    finally:
        await bot.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="no Telegram, no state writes")
    ap.add_argument("--no-gemini", action="store_true", help="skip stage 2 (keyword track only)")
    ap.add_argument("--check-sources", action="store_true", help="scan all boards and print a health table")
    ap.add_argument("--render-samples", action="store_true", help="render one sample CV per track")
    ap.add_argument("--test-telegram", action="store_true", help="send a test alert to your chat")
    ap.add_argument("--companies", help="comma-separated company names/ids to scan (debugging)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.ERROR)

    if args.render_samples:
        render_samples()
        return 0
    if args.test_telegram:
        return asyncio.run(test_telegram())
    return asyncio.run(Pipeline(args).run())


if __name__ == "__main__":
    sys.exit(main())
