"""Roblox level design AI assistant."""

import os
from pathlib import Path

from .core.project import Project

KINDS = ('level', 'reference_image', 'reference_map')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set RBXLEVEL_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("RBXLEVEL_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="roblox-level-design-ai", package="rbxlevel", env_prefix="RBXLEVEL", root=root,
                   domain="roblox-level-design", asset_kinds=KINDS)
