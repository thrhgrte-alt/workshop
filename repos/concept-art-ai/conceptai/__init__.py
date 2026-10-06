"""Environment and prop concept-art AI assistant."""

import os
from pathlib import Path

from .core.project import Project

KINDS = ('concept', 'reference_image', 'direction')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set CONCEPTAI_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("CONCEPTAI_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="concept-art-ai", package="conceptai", env_prefix="CONCEPTAI", root=root,
                   domain="concept-art", asset_kinds=KINDS)
