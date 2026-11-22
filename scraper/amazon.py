"""amazon.jobs search JSON (covers AWS / Annapurna Labs Haifa).
GET https://www.amazon.jobs/en/search.json?loc_query=Haifa%2C%20Israel&result_limit=100&offset=N&sort=recent
"""
from __future__ import annotations

from urllib.parse import quote

from core.models import Job
from scraper.base import Adapter, html_to_text, workplace_from


class AmazonAdapter(Adapter):
    name = "amazon"
    API = "https://www.amazon.jobs/en/search.json?base_query=&loc_query={loc}&result_limit=100&offset={off}&sort=recent"

    async def fetch(self, company: dict) -> list[Job]:
        found: dict[str, Job] = {}
        for loc in company.get("loc_queries") or ["Israel"]:
            offset = 0
            for _ in range(5):
                data = await self.http.get_json(self.API.format(loc=quote(loc), off=offset))
                rows = data.get("jobs") or []
                for j in rows:
                    jid = str(j.get("id_icims") or j.get("id") or j.get("job_path"))
                    if jid in found:
                        continue
                    desc = "\n".join(html_to_text(j.get(k)) for k in
                                     ("description", "basic_qualifications", "preferred_qualifications"))
                    location = j.get("normalized_location") or j.get("location", "")
                    found[jid] = self.make_job(
                        company, self.name, jid, j.get("title", ""),
                        f"https://www.amazon.jobs{j.get('job_path', '')}",
                        location=location,
                        workplace=workplace_from(location),
                        description=desc.strip(),
                        posted_at=j.get("posted_date", ""),
                        extra={"team": j.get("company_name", "")},
                    )
                offset += len(rows)
                if len(rows) < 100 or offset >= data.get("hits", 0):
                    break
        return list(found.values())
