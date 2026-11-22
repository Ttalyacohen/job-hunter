#!/usr/bin/env python3
"""Print data/profile.yaml as base64 for the CANDIDATE_PROFILE_B64 GitHub secret.

Use this when the repository is PUBLIC, so your phone / email never sit in git:
  python scripts/encode_profile.py | gh secret set CANDIDATE_PROFILE_B64
"""
import base64
import sys
from pathlib import Path

import yaml

path = Path(__file__).resolve().parent.parent / "data" / "profile.yaml"
raw = path.read_bytes()
yaml.safe_load(raw)          # fail early on a YAML typo
sys.stdout.write(base64.b64encode(raw).decode())
