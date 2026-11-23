"""Stage 1 — fast rule-based pre-filter (zero API cost)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.models import Job

_NORM_STRIP = re.compile(r"[\s'’`\"״׳\-\.\(\),/|_]+")


def normalize_location(s: str) -> str:
    return _NORM_STRIP.sub("", (s or "").lower())


def _keyword_regex(keyword: str) -> str:
    parts = [re.escape(p) for p in keyword.strip().split()]
    body = r"[\s\-_/]*".join(parts)
    # whole-word: not preceded/followed by a letter or digit (works for Hebrew too)
    return rf"(?<![^\W_]){body}(?![^\W_])"


def _compile(keywords: list[str], case_sensitive: bool) -> re.Pattern | None:
    if not keywords:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile("|".join(_keyword_regex(k) for k in keywords), flags)


@dataclass
class RuleResult:
    ok: bool
    reason: str = ""


HYBRID_EVIDENCE = re.compile(
    r"hybrid|remote|work(ing)? from home|\bwfh\b|flexible (work|location)|days? (a|per) week (in|at|from) (the )?(office|home)"
    r"|\d\s*days? (from|at) home|היברידי|היברידית|מהבית|עבודה מרחוק|מרחוק", re.I)


class RuleFilter:
    def __init__(self, filters: dict, country_only_israel_counts: bool = False, center_hybrid: bool = False):
        t = filters["title"]
        self.exclude = _compile(t.get("exclude", []), case_sensitive=False)
        self.exclude_cs = _compile(t.get("exclude_case_sensitive", []), case_sensitive=True)
        self.target = _compile(t.get("target", []), case_sensitive=False)
        loc = filters["location"]
        self.north = [normalize_location(x) for x in loc["north"]]
        self.israel = [normalize_location(x) for x in loc["israel"]]
        self.il_codes = {c.lower() for c in loc.get("israel_codes", [])}
        self.remote = [normalize_location(x) for x in loc["remote_or_hybrid"]]
        self.country_only_default = country_only_israel_counts
        self.center_hybrid = center_hybrid
        self.center = [normalize_location(x) for x in loc.get("center_near_train", [])]

    # ---- title ---------------------------------------------------------------
    def check_title(self, title: str) -> RuleResult:
        for rx in (self.exclude, self.exclude_cs):
            if rx and (m := rx.search(title)):
                return RuleResult(False, f"excluded title keyword '{m.group(0)}'")
        if self.target and self.target.search(title):
            return RuleResult(True)
        return RuleResult(False, "no target title keyword")

    # ---- location ------------------------------------------------------------
    def _is_israel(self, raw: str, norm: str) -> bool:
        if any(k in norm for k in self.israel):
            return True
        tokens = set(re.split(r"[^a-z]+", raw.lower()))
        return bool(tokens & self.il_codes)

    def check_location(self, job: Job, company: dict | None = None, strict: bool = False) -> RuleResult:
        """strict=True after the description was fetched: a centre-of-Israel job must then
        show hybrid / remote evidence in its text, otherwise it is dropped."""
        raw = job.location or ""
        norm = normalize_location(raw)
        if not norm:
            return RuleResult(False, "no location")
        if any(k in norm for k in self.north):
            return RuleResult(True, "north")
        is_il = self._is_israel(raw, norm)
        remote = job.workplace in ("remote", "hybrid") or any(k in norm for k in self.remote)
        if is_il and remote:
            return RuleResult(True, "remote/hybrid in Israel")
        country_only_ok = (company or {}).get("country_only_ok", self.country_only_default)
        if is_il and country_only_ok and norm in self.israel:
            return RuleResult(True, "country-only Israel")
        if self.center_hybrid and any(k in norm for k in self.center):
            job.extra["center"] = True
            if HYBRID_EVIDENCE.search(f"{job.workplace} {raw} {job.description}"):
                return RuleResult(True, "centre near train, hybrid/remote stated")
            if strict or job.description:
                return RuleResult(False, f"centre, no hybrid/remote mentioned: {raw[:60]}")
            return RuleResult(True, "centre near train, hybrid to be confirmed from description")
        if is_il:
            return RuleResult(False, f"Israel but not North/remote: {raw[:60]}")
        return RuleResult(False, f"not Israel: {raw[:60]}")

    def needs_location_detail(self, job: Job) -> bool:
        """Workday shows '3 Locations' in the list view; we must fetch details."""
        return bool(job.extra.get("multi_location"))

    def check(self, job: Job, company: dict | None = None, strict: bool = False) -> RuleResult:
        t = self.check_title(job.title)
        if not t.ok:
            return t
        if self.needs_location_detail(job) and not strict:
            return RuleResult(True, "location pending detail fetch")
        return self.check_location(job, company, strict)
