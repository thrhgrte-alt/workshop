"""Visual measurement and verification: numbers about a render, screenshot or texture (Pillow + NumPy only, no model calls)."""

import os
from pathlib import Path

from .guide_adapter import Project

KINDS = ('target_profile', 'reference_image', 'measurement_case')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set VISUALVERIFY_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("VISUALVERIFY_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="visual-verify", package="visualverify", env_prefix="VISUALVERIFY", root=root,
                   domain="visual-verify", asset_kinds=KINDS)
