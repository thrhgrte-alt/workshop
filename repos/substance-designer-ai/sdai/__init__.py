"""Substance 3D Designer AI assistant."""

import os
from pathlib import Path

from .core.project import Project

KINDS = ('material', 'graph_recipe', 'reference_image')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set SDAI_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("SDAI_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="substance-designer-ai", package="sdai", env_prefix="SDAI", root=root,
                   domain="designer", asset_kinds=KINDS)
