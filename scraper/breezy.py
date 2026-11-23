"""Breezy HR public job feed.
GET https://{company}.breezy.hr/json   -> list of positions (no description)
Descriptions are fetched from the position page only for stage-1 survivors.
"""
from __future__ import annotations

from core.models import Job
from scraper.base import Adapter, workplace_from
from scraper.generic import GenericAdapter


class BreezyAdapter(Adapter):
    name = "breezy"

    async def fetch(self, company: dict) -> list[Job]:
        data = await self.http.get_json(f"https://{company['subdomain']}.breezy.hr/json")
        jobs = []
        for p in data or []:
            loc = p.get("location") or {}
            country = loc.get("country") or {}
            country = country.get("name", "") if isinstance(country, dict) else str(country)
            location = loc.get("name") or ", ".join(x for x in [loc.get("city"), country] if x)
            url = p.get("url") or f"https://{company['subdomain']}.breezy.hr/p/{p.get('friendly_id', p.get('id'))}"
            jobs.append(self.make_job(
                company, self.name, p.get("id") or p.get("friendly_id"), p.get("name", ""), url,
                location=location,
                workplace=workplace_from(loc.get("is_remote") and "remote"),
                posted_at=p.get("published_date", ""),
            ))
        return jobs

    async def enrich(self, job: Job, company: dict) -> Job:
        return await GenericAdapter(self.http, self.settings).enrich(job, company)
