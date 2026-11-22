"""Generic careers-page scraper (httpx + BeautifulSoup).

Used for `ats: generic` and for `ats: unknown` until discover_ats.py finds the
real ATS. Collects (a) schema.org JobPosting JSON-LD and (b) job-looking links.
Stage 1 then drops anything whose link text is not a target job title, so nav
links are harmless. JavaScript-rendered boards will return nothing — run
discover_ats.py for those.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from

log = logging.getLogger(__name__)

JOBISH_HREF = re.compile(
    r"job|career|position|opening|vacanc|requisition|apply|/p/|comeet|greenhouse|lever\.co|workable|smartrecruiters|"
    r"myworkdayjobs|משרה|דרושים", re.I)
ATS_HINT = re.compile(r"comeet\.co|greenhouse\.io|lever\.co|workable\.com|smartrecruiters\.com|myworkdayjobs\.com", re.I)


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                if item.get("@type") == "JobPosting":
                    yield item
                stack.extend(v for k, v in item.items() if k in ("@graph", "itemListElement", "item"))


def _jsonld_location(item: dict) -> str:
    locs = item.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    out = []
    for l in locs:
        addr = (l or {}).get("address") or {}
        if isinstance(addr, dict):
            out.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"),
                                             addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str)
                                             else (addr.get("addressCountry") or {}).get("name")] if x))
    return " | ".join(o for o in out if o)


class GenericAdapter(Adapter):
    name = "generic"

    async def fetch(self, company: dict) -> list[Job]:
        url = company["careers_url"]
        resp = await self.http.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        default_loc = company.get("default_location", "")
        pattern = re.compile(company["link_pattern"]) if company.get("link_pattern") else None
        jobs: dict[str, Job] = {}

        for item in _iter_jsonld(soup):
            job_url = item.get("url") or url
            jobs[job_url] = self.make_job(
                company, self.name, _hash(job_url), item.get("title", ""), job_url,
                location=_jsonld_location(item) or default_loc,
                workplace=workplace_from(item.get("jobLocationType")),
                description=html_to_text(item.get("description")),
                posted_at=item.get("datePosted", ""),
            )

        base_host = urlparse(url).netloc
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"]).split("#")[0]
            text = " ".join(a.get_text(" ").split())
            if not (4 <= len(text) <= 140) or href in jobs or href.rstrip("/") == url.rstrip("/"):
                continue
            if pattern and not pattern.search(href):
                continue
            if not pattern and not JOBISH_HREF.search(href):
                continue
            if urlparse(href).scheme not in ("http", "https"):
                continue
            jobs[href] = self.make_job(company, self.name, _hash(href), text, href, location=default_loc,
                                       extra={"same_host": urlparse(href).netloc == base_host})

        if not jobs and ATS_HINT.search(resp.text):
            log.info("%s: page embeds a known ATS — run scripts/discover_ats.py for reliable results",
                     company["name"])
        return list(jobs.values())

    async def enrich(self, job: Job, company: dict) -> Job:
        if job.description:
            return job
        resp = await self.http.get(job.url, headers={"Accept": "text/html,application/xhtml+xml"})
        if resp.status_code >= 400:
            return job
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in _iter_jsonld(soup):
            job.description = html_to_text(item.get("description"))
            job.location = _jsonld_location(item) or job.location
            break
        if not job.description:
            for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
                t.decompose()
            main = soup.find("main") or soup.find("article") or soup.body or soup
            job.description = html_to_text(str(main), limit=8000)
        return job
