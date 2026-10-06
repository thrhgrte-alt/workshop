"""The ONE place this package imports shared machinery from.

``guide-core`` (the shared library that should provide dryrun, feedback, retrieval, evals, mock, luau_safety, mcpkit, config and scope) was NOT
available when this repository was built, so each of those names is mapped here onto the equivalent code in the vendored ``preflight.core``.
Swapping in the real guide-core means editing this file only. A test enforces that no other module in the package imports ``preflight.core``.

Names that guide-core lists but this repository has no use for (``mock`` DataModel, ``luau_safety``) are explicit placeholders that raise when used.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from pathlib import Path

from .core import cli as _cli
from .core import commontools as _commontools
from .core import evals, feedback, manifest, mcpkit, retrieval, rubric, safety, style
from .core.cli import DomainHooks, all_tools, emit, run
from .core.manifest import LibraryStore, base_schema, read_jsonl
from .core.mcpkit import ToolSpec, build_server, call_local, serve
from .core.project import Project
from .core.safety import PathNotAllowed, Plan, Versioner, resolve_inside, safe_name

# --- guide-core interface names ---------------------------------------------------------------------
dryrun = types.SimpleNamespace(Plan=Plan, Versioner=Versioner)
config = types.SimpleNamespace(load_style=style.load_style, validate_style=style.validate_style, style_brief=style.style_brief, load_rubric=rubric.load_rubric,
                               score_rubric=rubric.score_rubric, Project=Project)


@dataclass(frozen=True)
class PathPolicy:
    """Read/write roots for a tool call. Thin wrapper over safety.resolve_inside."""

    roots: tuple[Path, ...]

    def resolve(self, path: str | Path) -> Path:
        return resolve_inside(path, list(self.roots))


scope = types.SimpleNamespace(PathPolicy=PathPolicy, resolve_inside=resolve_inside, safe_name=safe_name, PathNotAllowed=PathNotAllowed)


def _unavailable(name: str):
    def missing(*a, **k):
        raise NotImplementedError(f"guide-core '{name}' is not used by the asset preflight (no Luau or DataModel here)")

    return types.SimpleNamespace(unavailable=missing, note=f"{name}: not used by this repository")


mock = _unavailable("mock")
luau_safety = _unavailable("luau_safety")


# --- workaround for a limit of the vendored core ------------------------------------------------------
# core.cli.all_tools() prepends the unscoped common tools (search_library, get_style_brief, record_run, ...). This repository exposes project-scoped
# versions under the same names, so the unscoped ones are filtered out here. Core files are not edited.
SCOPED_COMMON = {"search_library", "get_style_brief", "find_past_corrections", "record_run", "record_decision", "promote_run"}
_orig_common_tools = _commontools.common_tools


def common_tools(project: Project, correction_dimensions=None) -> list[ToolSpec]:
    """The vendored common tools (used by the scoped wrappers to reach core behaviour)."""
    return _orig_common_tools(project, correction_dimensions)


def _filtered_common(project: Project, dims=None) -> list[ToolSpec]:
    return [t for t in _orig_common_tools(project, dims) if t.name not in SCOPED_COMMON]


_cli.common_tools = _filtered_common
