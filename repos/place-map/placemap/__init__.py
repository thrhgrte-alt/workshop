"""Roblox place map and index."""

import os
from pathlib import Path

from .guide_adapter import Project

KINDS = ('snapshot', 'landmark', 'script_summary')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set PLACEMAP_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("PLACEMAP_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="place-map", package="placemap", env_prefix="PLACEMAP", root=root,
                   domain="place-map", asset_kinds=KINDS)
