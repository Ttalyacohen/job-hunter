"""Settings, paths and data-file loading (with environment overrides)."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
TEMPLATES_DIR = ROOT / "templates"


def _load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass
class Settings:
    raw: dict
    gemini_models: list[str]
    gemini_api_key: str | None
    telegram_token: str | None
    telegram_chat_id: str | None
    data_dir: Path = DATA_DIR
    extra: dict = field(default_factory=dict)

    # convenience accessors -------------------------------------------------
    @property
    def scan(self) -> dict:
        return self.raw["scan"]

    @property
    def pipeline(self) -> dict:
        return self.raw["pipeline"]

    @property
    def gemini(self) -> dict:
        return self.raw["gemini"]

    @property
    def relevance(self) -> dict:
        return self.raw["relevance"]

    @property
    def cv(self) -> dict:
        return self.raw["cv"]


def load_settings(path: Path | None = None) -> Settings:
    raw = _load_yaml(path or CONFIG_DIR / "settings.yaml")

    # env overrides (handy from the GitHub UI: Settings → Variables)
    raw["gemini"]["max_calls_per_run"] = _env_int("GEMINI_MAX_CALLS_PER_RUN", raw["gemini"]["max_calls_per_run"])
    raw["gemini"]["jobs_per_call"] = _env_int("GEMINI_JOBS_PER_CALL", raw["gemini"]["jobs_per_call"])
    raw["pipeline"]["first_sighting"] = os.getenv("FIRST_SIGHTING", raw["pipeline"]["first_sighting"])
    raw["pipeline"]["on_quota_exhausted"] = os.getenv("ON_QUOTA_EXHAUSTED", raw["pipeline"]["on_quota_exhausted"])

    models_env = os.getenv("GEMINI_MODELS", "").strip()
    models = [m.strip() for m in models_env.split(",") if m.strip()] or list(raw["gemini"]["models"])

    return Settings(
        raw=raw,
        gemini_models=models,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )


def load_filters(path: Path | None = None) -> dict:
    return _load_yaml(path or CONFIG_DIR / "filters.yaml")


def load_companies(path: Path | None = None) -> list[dict]:
    data = _load_yaml(path or DATA_DIR / "companies.yaml") or {}
    companies = data.get("companies", [])
    seen_ids: set[str] = set()
    for c in companies:
        c.setdefault("enabled", True)
        c.setdefault("id", _slug(c["name"]))
        if c["id"] in seen_ids:
            raise ValueError(f"Duplicate company id in companies.yaml: {c['id']}")
        seen_ids.add(c["id"])
    return companies


def load_profile(path: Path | None = None) -> dict:
    """Profile comes from the CANDIDATE_PROFILE_B64 secret if set (public repos),
    otherwise from data/profile.yaml."""
    b64 = os.getenv("CANDIDATE_PROFILE_B64", "").strip()
    if b64:
        return yaml.safe_load(base64.b64decode(b64).decode("utf-8"))
    return _load_yaml(path or DATA_DIR / "profile.yaml")


def _slug(name: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")
