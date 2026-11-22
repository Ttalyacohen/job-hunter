#!/usr/bin/env python3
"""Auto-detect which ATS a company uses and print / apply the companies.yaml entry.

Detects Greenhouse, Lever, Comeet, SmartRecruiters, Workable and Workday by
(1) scanning the careers page (and its iframes) for ATS URLs / widget config, then
(2) probing public ATS APIs with slug guesses derived from the company name.
Every hit is validated by actually fetching the job list.

Examples
  python scripts/discover_ats.py --name "Hailo" --url https://hailo.ai/company-overview/careers/
  python scripts/discover_ats.py --name "Some Startup"          # slug probing only
  python scripts/discover_ats.py --all                          # every ats: unknown/generic entry
  python scripts/discover_ats.py --all --apply                  # ...and rewrite data/companies.yaml
  python scripts/discover_ats.py --from-file new_companies.txt --append   # "Name, https://careers-url" per line
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup  # noqa: E402

from core.config import DATA_DIR, _slug  # noqa: E402
from core.http import HttpClient  # noqa: E402
from scraper import ADAPTERS  # noqa: E402

UID = r"[0-9A-Za-z]{2}\.[0-9A-Za-z]{3,4}"
PATTERNS = {
    "greenhouse": [
        re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
        re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)"),
        re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ],
    "lever": [re.compile(r"jobs\.(eu\.)?lever\.co/([A-Za-z0-9_.-]+)")],
    "comeet_api": [re.compile(rf"careers-api/2\.0/company/({UID})/positions\?token=([0-9A-Za-z]+)")],
    "comeet_page": [re.compile(rf"comeet\.(?:com|co)/jobs/([A-Za-z0-9_.-]+)/({UID})")],
    "comeet_widget_uid": [re.compile(rf"company[-_]?uid[\"']?\s*[:=]\s*[\"']({UID})", re.I)],
    "comeet_widget_token": [re.compile(r"[\"']?token[\"']?\s*[:=]\s*[\"']([0-9A-Za-z]{16,})[\"']", re.I)],
    "smartrecruiters": [re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)")],
    "workable": [re.compile(r"apply\.workable\.com/(?:api/v\d/(?:widget/)?accounts/)?([A-Za-z0-9_-]+)")],
    "workday": [re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")],
}
IGNORE_SLUGS = {"embed", "v1", "js", "api", "jobs", "job_board", "en", "careers", "apply", "www", "static", "widget"}


def slug_guesses(name: str) -> list[str]:
    base = name.lower().replace(".com", "").replace("&", "and")
    words = re.findall(r"[a-z0-9]+", base)
    guesses = ["".join(words), "-".join(words), words[0] if words else ""]
    return [g for g in dict.fromkeys(guesses) if g]


async def fetch_text(http: HttpClient, url: str) -> str:
    try:
        r = await http.get(url, headers={"Accept": "text/html,*/*"})
        return r.text if r.status_code < 400 else ""
    except Exception:
        return ""


async def candidates_from_page(http: HttpClient, url: str) -> list[dict]:
    html = await fetch_text(http, url)
    if not html:
        return []
    blobs = [html]
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["iframe", "script"], src=True)[:15]:
        src = urljoin(url, tag["src"])
        if re.search(r"comeet|greenhouse|lever|workable|smartrecruiters|myworkdayjobs|career|job", src, re.I):
            blobs.append(src)
            if tag.name == "iframe":
                blobs.append(await fetch_text(http, src))
    text = "\n".join(blobs)
    found: list[dict] = []
    for m in PATTERNS["comeet_api"][0].finditer(text):
        found.append({"ats": "comeet", "uid": m.group(1), "token": m.group(2)})
    uid = next((m.group(1) for m in PATTERNS["comeet_widget_uid"][0].finditer(text)), None)
    tok = next((m.group(1) for m in PATTERNS["comeet_widget_token"][0].finditer(text)), None)
    if uid and tok:
        found.append({"ats": "comeet", "uid": uid, "token": tok})
    for m in PATTERNS["comeet_page"][0].finditer(text):
        found.append({"ats": "comeet", "slug": m.group(1), "uid": m.group(2)})
    for rx in PATTERNS["greenhouse"]:
        for m in rx.finditer(text):
            if m.group(1).lower() not in IGNORE_SLUGS:
                found.append({"ats": "greenhouse", "board": m.group(1)})
    for m in PATTERNS["lever"][0].finditer(text):
        if m.group(2).lower() not in IGNORE_SLUGS:
            found.append({"ats": "lever", "site": m.group(2), **({"region": "eu"} if m.group(1) else {})})
    for m in PATTERNS["smartrecruiters"][0].finditer(text):
        if m.group(1).lower() not in IGNORE_SLUGS:
            found.append({"ats": "smartrecruiters", "company_id": m.group(1)})
    for m in PATTERNS["workable"][0].finditer(text):
        if m.group(1).lower() not in IGNORE_SLUGS:
            found.append({"ats": "workable", "account": m.group(1)})
    for m in PATTERNS["workday"][0].finditer(text):
        if m.group(3).lower() not in IGNORE_SLUGS | {"wday"}:
            found.append({"ats": "workday", "host": f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com",
                          "tenant": m.group(1), "site": m.group(3), "search_texts": ["Israel"]})
    # de-dup preserving order
    uniq, seen = [], set()
    for f in found:
        k = tuple(sorted(f.items(), key=lambda kv: kv[0]).__repr__())
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def probe_candidates(name: str) -> list[dict]:
    out = []
    for s in slug_guesses(name):
        out += [{"ats": "greenhouse", "board": s}, {"ats": "lever", "site": s},
                {"ats": "lever", "site": s, "region": "eu"}, {"ats": "workable", "account": s},
                {"ats": "smartrecruiters", "company_id": s}]
    return out


async def validate(http: HttpClient, name: str, cand: dict) -> int | None:
    """Return job count if the candidate config works, else None."""
    company = {"name": name, "id": _slug(name), **cand}
    adapter = ADAPTERS[cand["ats"]](http, {"workday_max_pages": 2})
    try:
        jobs = await asyncio.wait_for(adapter.fetch(company), timeout=40)
    except Exception:
        return None
    if cand["ats"] == "smartrecruiters" and not jobs:
        return None       # SmartRecruiters returns 200 + empty list for unknown ids
    return len(jobs)


async def discover(http: HttpClient, name: str, url: str | None) -> tuple[dict | None, int]:
    cands = (await candidates_from_page(http, url)) if url else []
    tried = set()
    for cand in cands + probe_candidates(name):
        key = repr(sorted(cand.items()))
        if key in tried:
            continue
        tried.add(key)
        n = await validate(http, name, cand)
        if n is not None and (n > 0 or cand in cands):
            return cand, n
    return None, 0


def to_yaml(name: str, cand: dict, url: str | None, n: int) -> str:
    lines = [f"  - name: {name}"]
    for k, v in cand.items():
        lines.append(f"    {k}: {v if not isinstance(v, list) else '[' + ', '.join(v) + ']'}")
    if url:
        lines.append(f"    careers_url: {url}")
    lines.append(f"    verified: {'true' if n > 0 else 'false'}   # {n} jobs found")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name")
    ap.add_argument("--url")
    ap.add_argument("--all", action="store_true", help="process every ats: unknown / generic entry")
    ap.add_argument("--from-file", help="text file with 'Company Name, https://careers-url' per line")
    ap.add_argument("--apply", action="store_true", help="rewrite matching entries in companies.yaml")
    ap.add_argument("--append", action="store_true", help="append newly found companies to companies.yaml")
    args = ap.parse_args()

    targets: list[tuple[str, str | None]] = []
    if args.name:
        targets.append((args.name, args.url))
    if args.from_file:
        for line in Path(args.from_file).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                name, _, url = line.partition(",")
                targets.append((name.strip(), url.strip() or None))
    if args.all:
        import yaml
        data = yaml.safe_load((DATA_DIR / "companies.yaml").read_text(encoding="utf-8"))
        targets += [(c["name"], c.get("careers_url")) for c in data["companies"]
                    if c.get("ats") in ("unknown", "generic")]
    if not targets:
        ap.error("give --name, --from-file or --all")

    results: dict[str, tuple[dict, str | None, int]] = {}
    async with HttpClient(concurrency=8, timeout=20, jitter=(0.1, 0.5), max_retries=1) as http:
        async def one(name, url):
            cand, n = await discover(http, name, url)
            status = f"{cand['ats']} ({n} jobs)" if cand else "not detected"
            print(f"{name:<28} {status}", flush=True)
            if cand:
                results[name] = (cand, url, n)
        await asyncio.gather(*(one(n, u) for n, u in targets))

    if results:
        print("\n# ---- companies.yaml snippets ----")
        for name, (cand, url, n) in results.items():
            print(to_yaml(name, cand, url, n))

    if (args.apply or args.append) and results:
        from ruamel.yaml import YAML
        ry = YAML()
        ry.preserve_quotes = True
        path = DATA_DIR / "companies.yaml"
        doc = ry.load(path.read_text(encoding="utf-8"))
        by_name = {c["name"]: c for c in doc["companies"]}
        changed = 0
        for name, (cand, url, n) in results.items():
            entry = by_name.get(name)
            if entry is None and args.append:
                entry = {"name": name}
                doc["companies"].append(entry)
            if entry is None:
                continue
            for k in ("board", "site", "region", "uid", "token", "slug", "company_id", "account",
                      "host", "tenant", "search_texts"):
                if k in entry and k not in cand:
                    del entry[k]
            entry.update(cand)
            if url:
                entry["careers_url"] = url
            entry["verified"] = n > 0
            changed += 1
        with open(path, "w", encoding="utf-8") as fh:
            ry.dump(doc, fh)
        print(f"\nUpdated {changed} entr{'y' if changed == 1 else 'ies'} in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
