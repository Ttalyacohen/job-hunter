import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_filters, load_profile, load_settings  # noqa: E402


@pytest.fixture
def settings():
    s = load_settings()
    s.raw["scan"]["jitter_s"] = [0, 0]
    s.raw["scan"]["max_retries"] = 0
    s.raw["gemini"]["min_seconds_between_calls"] = 0
    s.gemini_models = ["gemini-2.5-flash", "gemini-3.5-flash-lite"]
    return s


@pytest.fixture
def filters():
    return load_filters()


@pytest.fixture
def profile():
    return load_profile()
