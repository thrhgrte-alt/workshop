"""Modular asset placement and set dressing AI assistant."""

import os
from pathlib import Path

from .core.project import Project

KINDS = ('module', 'scene', 'reference_image')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set SETDRESS_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("SETDRESS_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="modular-set-dressing-ai", package="setdress", env_prefix="SETDRESS", root=root,
                   domain="set-dressing", asset_kinds=KINDS)
