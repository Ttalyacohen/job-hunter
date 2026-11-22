"""Workday CXS API (what *.myworkdayjobs.com career sites call from the browser).
POST https://{host}/wday/cxs/{tenant}/{site}/jobs   {"limit":20,"offset":N,"searchText":"..."}
GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}          (details)
"""
from __future__ import annotations

import re

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from

MULTI_LOC_RE = re.compile(r"^\s*\d+\s+Locations?\s*$", re.I)


class WorkdayAdapter(Adapter):
    name = "workday"

    def _base(self, c: dict) -> str:
        return f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"

    async def fetch(self, company: dict) -> list[Job]:
        page_size = self.settings.get("workday_page_size", 20)
        max_pages = self.settings.get("workday_max_pages", 25)
        found: dict[str, Job] = {}
        for text in company.get("search_texts") or [""]:
            offset, total = 0, None
            for _ in range(max_pages):
                payload = {"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": text}
                data = await self.http.post_json(f"{self._base(company)}/jobs", payload)
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
                if not postings or offset >= (total or 0):
                    break
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
