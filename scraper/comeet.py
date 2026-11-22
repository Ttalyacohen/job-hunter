"""Comeet (now Spark Hire Recruit) careers API.

Mode 1 (preferred) — needs company UID + token:
  GET https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true
Mode 2 (no token) — parse the public hosted page, which embeds the positions JSON:
  GET https://www.comeet.com/jobs/{slug}/{uid}   ->  COMPANY_POSITIONS_DATA = [...]
`scripts/discover_ats.py` finds slug / uid / token from a company's careers page.
"""
from __future__ import annotations

import json
import re

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from

POSITIONS_RE = re.compile(r"COMPANY_POSITIONS_DATA\s*=\s*(\[.*?\])\s*;\s*\n", re.S)


class ComeetAdapter(Adapter):
    name = "comeet"
    API = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true"

    async def fetch(self, company: dict) -> list[Job]:
        if company.get("token"):
            positions = await self.http.get_json(self.API.format(uid=company["uid"], token=company["token"]))
        else:
            resp = await self.http.get(f"https://www.comeet.com/jobs/{company['slug']}/{company['uid']}")
            resp.raise_for_status()
            m = POSITIONS_RE.search(resp.text)
            positions = json.loads(m.group(1)) if m else []
        return [self._to_job(company, p) for p in positions or []]

    def _to_job(self, company: dict, p: dict) -> Job:
        loc = p.get("location") or {}
        location = ", ".join(x for x in [loc.get("name"), loc.get("city"), loc.get("country")] if x)
        details = sorted(p.get("details") or [], key=lambda d: d.get("order", 0))
        description = "\n".join(f"{d.get('name', '')}\n{html_to_text(d.get('value'))}" for d in details)
        url = (p.get("url_active_page") or p.get("url_comeet_hosted_page")
               or p.get("position_url") or company.get("careers_url", ""))
        return self.make_job(
            company, self.name, p.get("uid") or p.get("id"), p.get("name", ""), url,
            location=location,
            workplace=workplace_from(p.get("workplace_type"), loc.get("is_remote") and "remote"),
            description=description,
            posted_at=p.get("time_updated") or "",
            extra={"experience_level": p.get("experience_level", "")},
        )
