"""SmartRecruiters Posting API — https://developers.smartrecruiters.com/docs/posting-api
GET https://api.smartrecruiters.com/v1/companies/{id}/postings?limit=100&offset=N
GET https://api.smartrecruiters.com/v1/companies/{id}/postings/{postingId}   (details)
"""
from __future__ import annotations

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from


class SmartRecruitersAdapter(Adapter):
    name = "smartrecruiters"
    BASE = "https://api.smartrecruiters.com/v1/companies/{cid}/postings"

    async def fetch(self, company: dict) -> list[Job]:
        cid = company["company_id"]
        jobs, offset = [], 0
        country = company.get("country", "il")
        while True:
            url = f"{self.BASE.format(cid=cid)}?limit=100&offset={offset}"
            if country:
                url += f"&country={country}"
            data = await self.http.get_json(url)
            content = data.get("content", [])
            for p in content:
                loc = p.get("location") or {}
                location = loc.get("fullLocation") or ", ".join(
                    x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
                jobs.append(self.make_job(
                    company, self.name, p["id"], p.get("name", ""),
                    f"https://jobs.smartrecruiters.com/{cid}/{p['id']}",
                    location=location,
                    workplace=workplace_from(loc.get("remote") and "remote", loc.get("hybrid") and "hybrid"),
                    posted_at=p.get("releasedDate", ""),
                    extra={"ref": p.get("ref", ""),
                           "experience": (p.get("experienceLevel") or {}).get("label", "")},
                ))
            offset += len(content)
            if not content or offset >= data.get("totalFound", 0) or offset >= 1000:
                break
        return jobs

    async def enrich(self, job: Job, company: dict) -> Job:
        if job.description:
            return job
        ref = job.extra.get("ref") or f"{self.BASE.format(cid=company['company_id'])}/{job.job_id}"
        d = await self.http.get_json(ref)
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        job.description = "\n".join(
            f"{s.get('title', '')}\n{html_to_text(s.get('text'))}" for s in sections.values() if isinstance(s, dict))
        job.url = d.get("postingUrl") or d.get("applyUrl") or job.url
        return job
