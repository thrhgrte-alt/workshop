"""Luau script reviewer."""

import os
from pathlib import Path

from .guide_adapter import Project

KINDS = ('script_case', 'good_script', 'bad_script')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set LUAUREV_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("LUAUREV_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="luau-reviewer", package="luaurev", env_prefix="LUAUREV", root=root,
                   domain="luau-review", asset_kinds=KINDS)
