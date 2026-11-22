"""Lever Postings API — https://github.com/lever/postings-api
GET https://api.lever.co/v0/postings/{site}?mode=json   (EU: api.eu.lever.co)
"""
from __future__ import annotations

from core.models import Job
from scraper.base import Adapter, html_to_text, ms_to_iso, workplace_from


class LeverAdapter(Adapter):
    name = "lever"

    async def fetch(self, company: dict) -> list[Job]:
        host = "api.eu.lever.co" if company.get("region") == "eu" else "api.lever.co"
        data = await self.http.get_json(f"https://{host}/v0/postings/{company['site']}?mode=json")
        jobs = []
        for j in data or []:
            cats = j.get("categories") or {}
            locs = [cats.get("location", "")] + list(cats.get("allLocations") or [])
            location = " | ".join(dict.fromkeys(l for l in locs if l))
            desc_parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
            for lst in j.get("lists") or []:
                desc_parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
            desc_parts.append(j.get("additionalPlain") or "")
            jobs.append(self.make_job(
                company, self.name, j["id"], j.get("text", ""), j.get("hostedUrl") or j.get("applyUrl", ""),
                location=location,
                workplace=workplace_from(j.get("workplaceType"), location),
                description="\n".join(p for p in desc_parts if p).strip(),
                posted_at=ms_to_iso(j.get("createdAt")),
                extra={"team": cats.get("team", ""), "commitment": cats.get("commitment", "")},
            ))
        return jobs
