"""Shared data structures."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Job:
    company: str            # display name, e.g. "NVIDIA"
    company_id: str         # slug from companies.yaml
    source: str             # ats name: greenhouse, lever, ...
    job_id: str             # id unique within the source board
    title: str
    url: str
    location: str = ""
    workplace: str = ""     # "remote" | "hybrid" | "onsite" | ""
    description: str = ""   # plain text (may be filled later by enrich())
    posted_at: str = ""     # ISO-8601 when known
    extra: dict[str, Any] = field(default_factory=dict)   # adapter-private data

    @property
    def key(self) -> str:
        return f"{self.source}:{self.company_id}:{self.job_id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**d)


@dataclass
class Evaluation:
    job_key: str
    match_score: int
    track: str                       # BACKEND_FULLSTACK | AI_ML_RESEARCH | SYSTEMS_LOWLEVEL_CYBER
    is_relevant: bool
    role_type: str = "OTHER"
    years_required: int | None = None
    summary_bullets: list[str] = field(default_factory=list)
    tailoring_plan: dict[str, Any] = field(default_factory=dict)
    rejection_reason: str = ""
    model: str = ""
    screened: bool = True            # False when produced by the keyword fallback

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Evaluation":
        return cls(**d)


TRACKS = ("BACKEND_FULLSTACK", "AI_ML_RESEARCH", "SYSTEMS_LOWLEVEL_CYBER")
