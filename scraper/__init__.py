"""Multi-source job scraper."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from core.http import HttpClient
from core.models import Job
from scraper.amazon import AmazonAdapter
from scraper.base import Adapter
from scraper.comeet import ComeetAdapter
from scraper.eightfold import EightfoldAdapter
from scraper.generic import GenericAdapter
from scraper.greenhouse import GreenhouseAdapter
from scraper.lever import LeverAdapter
from scraper.smartrecruiters import SmartRecruitersAdapter
from scraper.workable import WorkableAdapter
from scraper.workday import WorkdayAdapter

log = logging.getLogger(__name__)

ADAPTERS: dict[str, type[Adapter]] = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "comeet": ComeetAdapter,
    "smartrecruiters": SmartRecruitersAdapter,
    "workable": WorkableAdapter,
    "workday": WorkdayAdapter,
    "amazon": AmazonAdapter,
    "eightfold": EightfoldAdapter,
    "generic": GenericAdapter,
    "unknown": GenericAdapter,      # until discover_ats.py fills in the real ATS
}


@dataclass
class ScanResult:
    jobs: list[Job] = field(default_factory=list)
    stats: dict[str, dict] = field(default_factory=dict)   # company_id -> {count, error, seconds}
    failed_companies: set[str] = field(default_factory=set)


class Scraper:
    def __init__(self, http: HttpClient, scan_settings: dict):
        self.http = http
        self.settings = scan_settings
        self._adapters = {name: cls(http, scan_settings) for name, cls in ADAPTERS.items()}

    def adapter_for(self, company: dict) -> Adapter:
        ats = company.get("ats", "unknown")
        if ats not in self._adapters:
            raise ValueError(f"{company['name']}: unsupported ats '{ats}'")
        if ats == "unknown" and not company.get("careers_url"):
            raise ValueError(f"{company['name']}: ats unknown and no careers_url")
        return self._adapters[ats]

    async def _scan_one(self, company: dict, result: ScanResult) -> None:
        start = time.monotonic()
        cid = company["id"]
        try:
            adapter = self.adapter_for(company)
            jobs = await asyncio.wait_for(adapter.fetch(company), timeout=self.settings.get("company_timeout_s", 60))
            result.jobs.extend(jobs)
            result.stats[cid] = {"name": company["name"], "ats": company.get("ats"), "count": len(jobs),
                                 "error": "", "seconds": round(time.monotonic() - start, 1)}
        except Exception as exc:  # one broken board must never stop the run
            result.failed_companies.add(cid)
            msg = f"{type(exc).__name__}: {str(exc)[:160]}"
            result.stats[cid] = {"name": company["name"], "ats": company.get("ats"), "count": 0,
                                 "error": msg, "seconds": round(time.monotonic() - start, 1)}
            log.warning("scan failed for %s (%s): %s", company["name"], company.get("ats"), msg)

    async def scan(self, companies: list[dict]) -> ScanResult:
        result = ScanResult()
        await asyncio.gather(*(self._scan_one(c, result) for c in companies if c.get("enabled", True)))
        # de-duplicate (same posting listed twice on one board)
        unique: dict[str, Job] = {}
        for j in result.jobs:
            unique.setdefault(j.key, j)
        result.jobs = list(unique.values())
        return result

    async def enrich(self, job: Job, company: dict) -> Job:
        try:
            return await asyncio.wait_for(self.adapter_for(company).enrich(job, company), timeout=30)
        except Exception as exc:
            log.warning("enrich failed for %s / %s: %s", job.company, job.title, exc)
            return job
