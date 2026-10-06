"""Roblox Studio VFX AI assistant."""

import os
from pathlib import Path

from .core.project import Project

KINDS = ('effect', 'reference_image', 'reference_clip')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set RBXVFX_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("RBXVFX_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="roblox-vfx-ai", package="rbxvfx", env_prefix="RBXVFX", root=root,
                   domain="roblox-vfx", asset_kinds=KINDS)
