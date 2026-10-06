#!/usr/bin/env python3
"""Regenerate CLAUDE.md, GEMINI.md, .github/copilot-instructions.md and the skill mirrors.

Edit AGENTS.md and skills/ (the canonical sources), then run this. Use --check in CI.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rbxlevel.core.cli import run
from rbxlevel import project
from rbxlevel.hooks import HOOKS

sys.exit(run(project(), HOOKS, ["sync-agent-files", *sys.argv[1:]]))
