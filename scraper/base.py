"""Adapter base class and text helpers."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from core.http import HttpClient
from core.models import Job


def html_to_text(raw: str | None, limit: int | None = None) -> str:
    """HTML (possibly entity-escaped, as Greenhouse returns it) -> clean text."""
    if not raw:
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html.unescape(raw)
    text = BeautifulSoup(raw, "html.parser").get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit] if limit else text


def ms_to_iso(ms: int | float | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def workplace_from(*values: object) -> str:
    """Infer remote / hybrid / onsite from any mix of flags and strings."""
    blob = " ".join(str(v).lower() for v in values if v not in (None, "", False))
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob or "telecommut" in blob or blob.strip() == "true":
        return "remote"
    if "onsite" in blob or "on-site" in blob or "office" in blob:
        return "onsite"
    return ""


class Adapter:
    """Fetches the job list for one company. `enrich` fills the description
    for jobs that survive stage 1 (only called for a few jobs per run)."""

    name = "base"

    def __init__(self, http: HttpClient, settings: dict):
        self.http = http
        self.settings = settings

    async def fetch(self, company: dict) -> list[Job]:  # pragma: no cover - interface
        raise NotImplementedError

    async def enrich(self, job: Job, company: dict) -> Job:
        return job

    # helpers -----------------------------------------------------------------
    @staticmethod
    def make_job(company: dict, source: str, job_id: object, title: str, url: str, **kw) -> Job:
        return Job(
            company=company["name"],
            company_id=company["id"],
            source=source,
            job_id=str(job_id),
            title=(title or "").strip(),
            url=url or company.get("careers_url", ""),
            **kw,
        )
