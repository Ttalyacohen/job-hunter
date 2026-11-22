"""Persistent state committed back to the repo by GitHub Actions.

data/seen_jobs.json  — every job key we have already handled (deduplication)
data/state.json      — Gemini quota bookkeeping, notification outbox, etc.

Files are only rewritten when their content actually changes, so most runs
produce no commit at all.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")   # Gemini daily quotas reset at midnight PT

# seen-job status codes
FILTERED = "f"      # dropped by stage-1 rules
REJECTED = "r"      # Gemini said not relevant
NOTIFIED = "n"      # alert + CV sent
BOOTSTRAP = "b"     # existed on first scan (first_sighting: silent)


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def today_pacific() -> str:
    return datetime.now(PACIFIC).date().isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null") or default
    except json.JSONDecodeError:
        return default


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


class SeenJobs:
    def __init__(self, path: Path):
        self.path = path
        raw = _read_json(path, {})
        self.jobs: dict[str, dict] = raw.get("jobs", {}) if isinstance(raw, dict) else {}
        self._original = _dump({"version": 1, "jobs": self.jobs})

    def __contains__(self, key: str) -> bool:
        return key in self.jobs

    def touch(self, key: str) -> None:
        """Record that the job is still listed today (used for pruning)."""
        entry = self.jobs.get(key)
        if entry is not None:
            entry["l"] = today_utc()

    def mark(self, key: str, status: str, title: str = "") -> None:
        today = today_utc()
        entry = self.jobs.setdefault(key, {"f": today})
        entry.update({"s": status, "l": today})
        if title:
            entry["t"] = title[:80]

    def status(self, key: str) -> str | None:
        e = self.jobs.get(key)
        return e.get("s") if e else None

    def prune(self, older_than_days: int) -> int:
        cutoff = (date.fromisoformat(today_utc()) - timedelta(days=older_than_days)).isoformat()
        stale = [k for k, v in self.jobs.items() if v.get("l", v.get("f", "")) < cutoff]
        for k in stale:
            del self.jobs[k]
        return len(stale)

    def save(self) -> bool:
        new = _dump({"version": 1, "jobs": self.jobs})
        if new == self._original:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(new, encoding="utf-8")
        self._original = new
        return True


class RunState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = _read_json(path, {})
        self.data.setdefault("gemini", {})
        self.data.setdefault("companies_seen", [])
        self.data.setdefault("outbox", [])
        self.data.setdefault("alerts", {})
        self._original = _dump(self.data)

    # ---- companies ---------------------------------------------------------
    def company_is_new(self, company_id: str) -> bool:
        return company_id not in self.data["companies_seen"]

    def mark_company_seen(self, company_id: str) -> None:
        if company_id not in self.data["companies_seen"]:
            self.data["companies_seen"].append(company_id)
            self.data["companies_seen"].sort()

    # ---- gemini quota ------------------------------------------------------
    def _g(self) -> dict:
        g = self.data["gemini"]
        today = today_pacific()
        if g.get("date") != today:          # new Pacific day -> reset counters
            g["date"] = today
            g["calls"] = {}
            g["exhausted"] = []
        g.setdefault("calls", {})
        g.setdefault("exhausted", [])
        g.setdefault("unavailable", [])
        return g

    def model_usable(self, model: str) -> bool:
        g = self._g()
        return model not in g["exhausted"] and model not in g["unavailable"]

    def record_call(self, model: str) -> None:
        g = self._g()
        g["calls"][model] = g["calls"].get(model, 0) + 1

    def mark_exhausted(self, model: str) -> None:
        g = self._g()
        if model not in g["exhausted"]:
            g["exhausted"].append(model)

    def mark_unavailable(self, model: str) -> None:
        g = self._g()
        if model not in g["unavailable"]:
            g["unavailable"].append(model)

    def reset_unavailable(self, configured: list[str]) -> None:
        """Forget 'unavailable' marks for models no longer configured (or if the
        list was edited) so a fixed config takes effect immediately."""
        g = self._g()
        g["unavailable"] = [m for m in g["unavailable"] if m in configured]

    # ---- outbox (alerts whose Telegram delivery failed) ----------------------
    @property
    def outbox(self) -> list[dict]:
        return self.data["outbox"]

    # ---- one-per-day operational alerts --------------------------------------
    def should_alert(self, name: str) -> bool:
        return self.data["alerts"].get(name) != today_utc()

    def alerted(self, name: str) -> None:
        self.data["alerts"][name] = today_utc()

    def save(self) -> bool:
        new = _dump(self.data)
        if new == self._original:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(new, encoding="utf-8")
        self._original = new
        return True
