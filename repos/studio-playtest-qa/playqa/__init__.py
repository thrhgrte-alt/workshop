"""Roblox Studio playtest QA."""

import os
from pathlib import Path

from .guide_adapter import Project

KINDS = ('check_result', 'baseline', 'failure_case')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set PLAYQA_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("PLAYQA_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="studio-playtest-qa", package="playqa", env_prefix="PLAYQA", root=root,
                   domain="playtest-qa", asset_kinds=KINDS)
