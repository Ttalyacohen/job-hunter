"""Workday CXS API (what *.myworkdayjobs.com career sites call from the browser).
POST https://{host}/wday/cxs/{tenant}/{site}/jobs   {"appliedFacets":{...},"limit":20,"offset":N,"searchText":"..."}
GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}          (details)

Location filtering: the first response lists the site's facets (country / city
lists with ids). We pick the location facet values matching `location_terms`
(default ["Israel"]) and page through only those postings. That is far fewer
requests than a free-text search and avoids 429 rate limiting on big tenants
such as NVIDIA. If no location facet matches, we fall back to `search_texts`.
"""
from __future__ import annotations

import asyncio
import logging
import re

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from

log = logging.getLogger(__name__)

MULTI_LOC_RE = re.compile(r"^\s*\d+\s+Locations?\s*$", re.I)


def find_location_facet(facets: list, terms: list[str]) -> tuple[str, list[str]] | None:
    """Return (facetParameter, [ids]) for location facet values matching any term.
    Workday nests facets (e.g. locationMainGroup -> locationHierarchy1); we walk
    them recursively and pick the parameter whose matches cover the most jobs."""
    terms_l = [t.lower() for t in terms]
    matches: dict[str, dict] = {}

    def walk(param: str, values: list) -> None:
        for v in values or []:
            if isinstance(v, dict) and v.get("values") and v.get("facetParameter"):
                walk(v["facetParameter"], v["values"])
                continue
            desc = str((v or {}).get("descriptor", "")).lower()
            if "location" in param.lower() or "country" in param.lower():
                if v.get("id") and any(t in desc for t in terms_l):
                    m = matches.setdefault(param, {"ids": [], "count": 0})
                    m["ids"].append(v["id"])
                    m["count"] += int(v.get("count") or 0)

    for f in facets or []:
        walk(f.get("facetParameter", ""), f.get("values") or [])
    if not matches:
        return None
    param, best = max(matches.items(), key=lambda kv: kv[1]["count"])
    return param, best["ids"]


class WorkdayAdapter(Adapter):
    name = "workday"

    def _base(self, c: dict) -> str:
        return f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"

    async def _page(self, company: dict, facets: dict, text: str, offset: int) -> dict:
        payload = {"appliedFacets": facets, "limit": self.settings.get("workday_page_size", 20),
                   "offset": offset, "searchText": text}
        return await self.http.post_json(f"{self._base(company)}/jobs", payload)

    async def _collect(self, company: dict, facets: dict, text: str, found: dict, first: dict | None) -> None:
        max_pages = self.settings.get("workday_max_pages", 25)
        delay = self.settings.get("workday_page_delay_s", 0.8)
        offset, total, data = 0, None, first
        for page in range(max_pages):
            if data is None:
                if page:
                    await asyncio.sleep(delay)     # be polite: avoids 429 on large tenants
                data = await self._page(company, facets, text, offset)
            postings = data.get("jobPostings") or []
            if total is None:
                total = data.get("total", 0)
            for p in postings:
                path = p.get("externalPath", "")
                if not path or path in found:
                    continue
                location = p.get("locationsText", "")
                found[path] = self.make_job(
                    company, self.name, (p.get("bulletFields") or [path])[0], p.get("title", ""),
                    f"https://{company['host']}/{company['site']}{path}",
                    location=location,
                    workplace=workplace_from(p.get("remoteType")),
                    posted_at=p.get("postedOn", ""),     # e.g. "Posted Today"
                    extra={"path": path, "multi_location": bool(MULTI_LOC_RE.match(location))},
                )
            offset += len(postings)
            data = None
            if not postings or offset >= (total or 0):
                break

    async def fetch(self, company: dict) -> list[Job]:
        found: dict[str, Job] = {}
        terms = company.get("location_terms") or ["Israel"]
        first = await self._page(company, {}, "", 0)
        facet = find_location_facet(first.get("facets") or [], terms)
        await asyncio.sleep(self.settings.get("workday_page_delay_s", 0.8))
        if facet:
            param, ids = facet
            log.debug("%s: Workday location facet %s=%s", company["name"], param, ids)
            await self._collect(company, {param: ids}, "", found, None)
        else:
            for text in company.get("search_texts") or terms:
                await self._collect(company, {}, text, found, None)
        return list(found.values())

    async def enrich(self, job: Job, company: dict) -> Job:
        if job.description:
            return job
        d = await self.http.get_json(f"{self._base(company)}{job.extra['path']}")
        info = d.get("jobPostingInfo") or {}
        locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
        if any(locs):
            job.location = " | ".join(dict.fromkeys(l for l in locs if l))
            job.extra["multi_location"] = False
        job.description = html_to_text(info.get("jobDescription"))
        job.workplace = job.workplace or workplace_from(info.get("remoteType"), info.get("timeType"))
        job.url = info.get("externalUrl") or job.url
        job.posted_at = info.get("startDate") or job.posted_at
        return job
