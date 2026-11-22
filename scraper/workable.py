"""Workable public widget API.
GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
"""
from __future__ import annotations

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from


class WorkableAdapter(Adapter):
    name = "workable"
    API = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"

    async def fetch(self, company: dict) -> list[Job]:
        data = await self.http.get_json(self.API.format(account=company["account"]))
        jobs = []
        for j in data.get("jobs", []):
            locs = []
            for l in j.get("locations") or []:
                if not l.get("hidden"):
                    locs.append(", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x))
            if not locs:
                locs.append(", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x))
            location = " | ".join(dict.fromkeys(l for l in locs if l))
            jobs.append(self.make_job(
                company, self.name, j.get("shortcode") or j.get("code"), j.get("title", ""),
                j.get("url") or j.get("shortlink") or j.get("application_url", ""),
                location=location,
                workplace=workplace_from(j.get("telecommuting") and "remote", j.get("workplace")),
                description=html_to_text(j.get("description")),
                posted_at=j.get("published_on") or j.get("created_at") or "",
                extra={"experience": j.get("experience", "")},
            ))
        return jobs
