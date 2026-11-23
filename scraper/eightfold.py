"""Eightfold AI career sites (Qualcomm, Microsoft, ...).

The careers SPA loads jobs from a JSON endpoint on the same host. Two API
generations exist; we try the newer one first and fall back:
  GET https://{host}/api/pcsx/search?domain={domain}&query=&location={loc}&start=N&sort_by=timestamp
  GET https://{host}/api/apply/v2/jobs?domain={domain}&location={loc}&start=N&num=10&sort_by=timestamp
Details (description) for a single job:
  GET https://{host}/api/apply/v2/jobs/{id}?domain={domain}
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from

log = logging.getLogger(__name__)


def _ts(v) -> str:
    try:
        v = float(v)
        if v > 1e12:          # milliseconds
            v /= 1000
        return datetime.fromtimestamp(v, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return str(v or "")


class EightfoldAdapter(Adapter):
    name = "eightfold"
    PAGE = 10

    async def _search(self, c: dict, loc: str, start: int) -> tuple[list, int]:
        host, domain, q = c["host"], c["domain"], quote(loc)
        if c.get("_api") != "v2":
            try:
                d = await self.http.get_json(f"https://{host}/api/pcsx/search?domain={domain}&query="
                                             f"&location={q}&start={start}&sort_by=timestamp")
                data = d.get("data") or d
                if isinstance(data.get("positions"), list):
                    c["_api"] = "pcsx"
                    return data["positions"], int(data.get("count") or 0)
            except Exception as exc:
                if c.get("_api") == "pcsx":
                    raise
                log.debug("%s: pcsx API unavailable (%s), trying v2", c["name"], exc)
        d = await self.http.get_json(f"https://{host}/api/apply/v2/jobs?domain={domain}&location={q}"
                                     f"&start={start}&num={self.PAGE}&sort_by=timestamp")
        c["_api"] = "v2"
        return d.get("positions") or [], int(d.get("count") or 0)

    def _to_job(self, c: dict, p: dict) -> Job:
        jid = p.get("id") or p.get("ats_job_id") or p.get("display_job_id")
        locs = p.get("locations") or []
        if isinstance(locs, list):
            locs = [l if isinstance(l, str) else (l or {}).get("name", "") for l in locs]
        location = " | ".join(dict.fromkeys(x for x in [p.get("location", "")] + list(locs) if x))
        url = p.get("canonicalPositionUrl") or p.get("positionUrl") or f"/careers/job/{jid}"
        if url.startswith("/"):
            url = f"https://{c['host']}{url}"
        return self.make_job(
            c, self.name, jid, p.get("name") or p.get("posting_name") or "", url,
            location=location,
            workplace=workplace_from(p.get("work_location_option"), p.get("workLocationOption")),
            description=html_to_text(p.get("job_description")),
            posted_at=_ts(p.get("t_create") or p.get("postedTs") or p.get("t_update")),
        )

    async def fetch(self, company: dict) -> list[Job]:
        found: dict[str, Job] = {}
        max_pages = company.get("max_pages", 15)
        for loc in company.get("locations") or ["Israel"]:
            start = 0
            for _ in range(max_pages):
                positions, count = await self._search(company, loc, start)
                for p in positions:
                    job = self._to_job(company, p)
                    found.setdefault(job.job_id, job)
                start += len(positions)
                if not positions or start >= count:
                    break
        return list(found.values())

    async def enrich(self, job: Job, company: dict) -> Job:
        if job.description:
            return job
        try:
            d = await self.http.get_json(f"https://{company['host']}/api/apply/v2/jobs/{job.job_id}"
                                         f"?domain={company['domain']}")
            job.description = html_to_text(d.get("job_description"))
            job.location = d.get("location") or job.location
        except Exception as exc:
            log.debug("eightfold detail failed for %s: %s", job.title, exc)
        return job
