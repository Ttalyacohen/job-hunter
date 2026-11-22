"""Stage 2 — Gemini evaluation with batching, model fallback and quota handling.

Uses the REST endpoint directly (no SDK) so model names can change without
dependency upgrades:
  POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

from core.models import TRACKS, Evaluation, Job
from core.state import RunState
from matcher.prompt import SYSTEM_RULES, all_skills, candidate_block, jobs_block, response_schema

log = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class QuotaExhausted(Exception):
    """Every configured model is out of quota (or retired) for today."""


class NoModelsAvailable(QuotaExhausted):
    """Every configured model returned 404 / is retired."""


# ─────────────────────────────────────────────────────────────────────────────
class GeminiClient:
    def __init__(self, api_key: str, models: list[str], state: RunState, settings: dict,
                 client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.models = models
        self.state = state
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(120))
        self.calls_this_run = 0
        self._last_call = 0.0
        state.reset_unavailable(models)

    async def aclose(self) -> None:
        await self.client.aclose()

    @property
    def budget_left(self) -> int:
        return self.settings["max_calls_per_run"] - self.calls_this_run

    async def _pace(self) -> None:
        wait = self.settings.get("min_seconds_between_calls", 0) - (time.monotonic() - self._last_call)
        if wait > 0 and self._last_call:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    async def generate_json(self, system: str, user: str, schema: dict | None) -> tuple[dict, str]:
        usable = [m for m in self.models if self.state.model_usable(m)]
        if not usable:
            if all(m in self.state.data["gemini"].get("unavailable", []) for m in self.models):
                raise NoModelsAvailable("all configured Gemini models are unavailable (404)")
            raise QuotaExhausted("all Gemini models exhausted for today")

        for model in usable:
            use_schema = schema
            for attempt in range(3):
                await self._pace()
                body = {
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {
                        "temperature": self.settings.get("temperature", 0.2),
                        "responseMimeType": "application/json",
                        **({"responseSchema": use_schema} if use_schema else {}),
                    },
                }
                try:
                    resp = await self.client.post(API.format(model=model), json=body,
                                                  headers={"x-goog-api-key": self.api_key})
                except httpx.HTTPError as exc:
                    log.warning("Gemini %s network error: %s", model, exc)
                    await asyncio.sleep(3)
                    continue
                self.calls_this_run += 1
                self.state.record_call(model)

                if resp.status_code == 200:
                    try:
                        return parse_response(resp.json()), model
                    except ValueError as exc:
                        log.warning("Gemini %s returned unparseable JSON (%s); retrying", model, exc)
                        continue
                if resp.status_code == 404:
                    log.warning("Gemini model %s not found / retired — skipping it", model)
                    self.state.mark_unavailable(model)
                    break
                if resp.status_code == 429:
                    per_day, retry_s = _quota_info(resp)
                    if per_day:
                        log.warning("Gemini %s daily quota exhausted", model)
                        self.state.mark_exhausted(model)
                        break
                    if attempt == 0 and retry_s <= 60:
                        log.info("Gemini %s per-minute limit; sleeping %ss", model, retry_s)
                        await asyncio.sleep(retry_s)
                        continue
                    break    # try next model; don't mark exhausted (it was per-minute)
                if resp.status_code == 400 and use_schema is not None and "schema" in resp.text.lower():
                    log.warning("Gemini %s rejected responseSchema; retrying with JSON mode only", model)
                    use_schema = None
                    continue
                if resp.status_code >= 500:
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                if resp.status_code in (401, 403):
                    raise RuntimeError(f"Gemini auth error {resp.status_code}: check GEMINI_API_KEY — {resp.text[:200]}")
                log.warning("Gemini %s HTTP %s: %s", model, resp.status_code, resp.text[:300])
                break
        if all(not self.state.model_usable(m) for m in self.models):
            if all(m in self.state.data["gemini"].get("unavailable", []) for m in self.models):
                raise NoModelsAvailable("all configured Gemini models are unavailable (404)")
            raise QuotaExhausted("all Gemini models exhausted for today")
        raise RuntimeError("Gemini call failed on every model (transient); will retry next run")


def _quota_info(resp: httpx.Response) -> tuple[bool, float]:
    """(is_daily_quota, retry_seconds) from a 429 body."""
    per_day, retry = False, 30.0
    try:
        err = resp.json().get("error", {})
        for d in err.get("details", []):
            for v in d.get("violations", []) or []:
                if "PerDay" in (v.get("quotaId", "") + v.get("quotaMetric", "")):
                    per_day = True
            if d.get("retryDelay"):
                retry = float(str(d["retryDelay"]).rstrip("s") or 30)
        if "per day" in err.get("message", "").lower() or "PerDay" in err.get("message", ""):
            per_day = True
    except (ValueError, AttributeError):
        pass
    return per_day, retry


def parse_response(payload: dict) -> dict:
    cands = payload.get("candidates") or []
    if not cands:
        raise ValueError(f"no candidates (promptFeedback={payload.get('promptFeedback')})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError(str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────────
_AI_WORDS = re.compile(r"machine learning|\bml\b|\bai\b|deep learning|computer vision|\bnlp\b|\bllm|data scien|"
                       r"algorithm|research|neural|vision|perception", re.I)
_SYS_WORDS = re.compile(r"embedded|firmware|c\+\+|\bc\b|kernel|linux|network|cyber|security|low[- ]level|"
                        r"driver|systems?\b|bsp|rtos", re.I)


def heuristic_track(job: Job) -> str:
    title = job.title
    if _AI_WORDS.search(title):
        return "AI_ML_RESEARCH"
    if _SYS_WORDS.search(title):
        return "SYSTEMS_LOWLEVEL_CYBER"
    blob = f"{title}\n{job.description[:1500]}"
    ai, sy = len(_AI_WORDS.findall(blob)), len(_SYS_WORDS.findall(blob))
    if ai > sy and ai >= 2:
        return "AI_ML_RESEARCH"
    if sy > ai and sy >= 2:
        return "SYSTEMS_LOWLEVEL_CYBER"
    return "BACKEND_FULLSTACK"


def unscreened_evaluation(job: Job) -> Evaluation:
    return Evaluation(job_key=job.key, match_score=0, track=heuristic_track(job), is_relevant=True,
                      role_type="UNKNOWN", summary_bullets=[], tailoring_plan={}, screened=False,
                      model="keyword-fallback")


class Evaluator:
    def __init__(self, client: GeminiClient, profile: dict, gemini_settings: dict, relevance: dict):
        self.client = client
        self.profile = profile
        self.settings = gemini_settings
        self.min_score = relevance["min_score"]
        self.max_years = relevance["max_years_required"]
        self.system = SYSTEM_RULES.format(min_score=self.min_score, max_years=self.max_years) \
            + "\n\n" + candidate_block(profile)
        self.schema = response_schema(profile)

    async def evaluate(self, jobs: list[Job]) -> dict[str, Evaluation]:
        """Evaluate one batch. Raises QuotaExhausted when nothing is left today."""
        refs = [(f"J{i + 1}", j) for i, j in enumerate(jobs)]
        user = jobs_block(refs, self.settings.get("max_description_chars", 3500))
        data, model = await self.client.generate_json(self.system, user, self.schema)
        by_ref = {ref: job for ref, job in refs}
        results: dict[str, Evaluation] = {}
        for raw in (data.get("evaluations") or []):
            job = by_ref.get(str(raw.get("job_ref", "")).strip())
            if job is None:
                continue
            results[job.key] = self._validate(job, raw, model)
        missing = [j.title for j in jobs if j.key not in results]
        if missing:
            log.warning("Gemini omitted %d job(s) in batch: %s", len(missing), missing)
        return results

    def _validate(self, job: Job, raw: dict, model: str) -> Evaluation:
        """Defense in depth: re-apply every hard rule in code."""
        score = _as_int(raw.get("match_score"), 0)
        years = raw.get("years_required")
        years = _as_int(years, None) if years not in (None, "", "null") else None
        role = str(raw.get("role_type", "OTHER")).upper()
        track = raw.get("track") if raw.get("track") in TRACKS else heuristic_track(job)
        reasons = []
        if not raw.get("is_relevant"):
            reasons.append(raw.get("rejection_reason") or "model marked not relevant")
        if role not in ("SOFTWARE_DEV", "RESEARCH"):
            reasons.append(f"role type {role}")
        if score < self.min_score:
            reasons.append(f"score {score} < {self.min_score}")
        if years is not None and years > self.max_years:
            reasons.append(f"requires {years}+ years")
        if raw.get("location_ok") is False:
            reasons.append("location not workable from the North")
        bullets = [str(b).strip() for b in (raw.get("summary_bullets") or []) if str(b).strip()][:3]
        return Evaluation(
            job_key=job.key, match_score=max(0, min(score, 10)), track=track, is_relevant=not reasons,
            role_type=role, years_required=years, summary_bullets=bullets,
            tailoring_plan=self._clean_plan(raw.get("tailoring_plan") or {}),
            rejection_reason="; ".join(reasons), model=model,
        )

    def _clean_plan(self, plan: dict) -> dict:
        p = self.profile
        proj_ids = [x["id"] for x in p["projects"]]
        course_ids = [c["id"] for c in p["courses"]]
        skills = set(all_skills(p))
        return {
            "subtitle_id": plan.get("subtitle_id") if plan.get("subtitle_id") in p["subtitles"] else None,
            "domain_line_id": plan.get("domain_line_id")
            if plan.get("domain_line_id") in p["profile"]["domain_lines"] else None,
            "project_order": _dedupe([x for x in plan.get("project_order") or [] if x in proj_ids]),
            "course_ids": _dedupe([x for x in plan.get("course_ids") or [] if x in course_ids]),
            "focus_skills": _dedupe([x for x in plan.get("focus_skills") or [] if x in skills])[:6],
            "keywords": [str(k)[:40] for k in (plan.get("keywords") or [])][:6],
        }


def _as_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _dedupe(seq):
    return list(dict.fromkeys(seq))
