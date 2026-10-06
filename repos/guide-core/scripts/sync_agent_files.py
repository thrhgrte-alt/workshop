#!/usr/bin/env python3
"""Regenerate CLAUDE.md, GEMINI.md, .github/copilot-instructions.md and the skill mirrors from AGENTS.md and skills/ (``--check`` only verifies)."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from guide_core import agentfiles  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    diffs = agentfiles.sync(ROOT, check=a.check)
    print(json.dumps({"check": a.check, "differences": diffs}, indent=2))
    sys.exit(1 if (a.check and diffs) else 0)
