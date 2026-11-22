"""Greenhouse Job Board API — https://developers.greenhouse.io/job-board.html
GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
"""
from __future__ import annotations

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from


class GreenhouseAdapter(Adapter):
    name = "greenhouse"
    API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"

    async def fetch(self, company: dict) -> list[Job]:
        data = await self.http.get_json(self.API.format(board=company["board"]))
        jobs = []
        for j in data.get("jobs", []):
            loc_parts = [(j.get("location") or {}).get("name", "")]
            loc_parts += [o.get("name", "") for o in j.get("offices", []) or []]
            location = " | ".join(dict.fromkeys(p for p in loc_parts if p))
            jobs.append(self.make_job(
                company, self.name, j["id"], j.get("title", ""), j.get("absolute_url", ""),
                location=location,
                workplace=workplace_from(location),
                description=html_to_text(j.get("content")),
                posted_at=j.get("first_published") or j.get("updated_at") or "",
            ))
        return jobs
