"""Roblox economy and progression balancer."""

import os
from pathlib import Path

from .guide_adapter import scope

Project = scope.Project

KINDS = ('economy_spec', 'playtest_note', 'rebalance')


def project(root: str | Path | None = None) -> Project:
    """The repository context. Set ECONBAL_ROOT to use a checkout from another working directory."""
    root = Path(root or os.environ.get("ECONBAL_ROOT") or Path(__file__).resolve().parents[1])
    return Project(name="roblox-economy-balancer", package="econbal", env_prefix="ECONBAL", root=root,
                   domain="game-economy", asset_kinds=KINDS)
