"""Backward-compatible names from the vendored kit (``core.safety``).

New code imports :mod:`guide_core.dryrun` (Plan, Versioner) and :mod:`guide_core.scope` (resolve_inside, safe_name,
PathNotAllowed). This module only re-exports them, so code written against the vendored copies keeps working.
"""

from .dryrun import Plan, Versioner  # noqa: F401
from .scope import PathNotAllowed, resolve_inside, safe_name  # noqa: F401

__all__ = ["PathNotAllowed", "Plan", "Versioner", "resolve_inside", "safe_name"]
