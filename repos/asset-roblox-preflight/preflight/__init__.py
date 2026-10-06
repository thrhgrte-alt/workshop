"""Blender-to-Roblox asset preflight."""

import os
from pathlib import Path

from .guide_adapter import Project

KINDS = ('export_case', 'good_export', 'bad_export')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set PREFLIGHT_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("PREFLIGHT_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="asset-roblox-preflight", package="preflight", env_prefix="PREFLIGHT", root=root,
                   domain="asset-preflight", asset_kinds=KINDS)
