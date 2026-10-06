"""The ONE place this package imports shared machinery from.

Shared machinery comes from the installed ``guide-core`` library (``pip install -e /path/to/guide-core`` first; this package depends on it). Each name the support-repo spec lists
(dryrun, feedback, retrieval, evals, mock, luau_safety, mcpkit, config, scope) is mapped here onto ``guide_core``. A test enforces that no other module in the package imports
``guide_core`` (or a vendored ``core``), so a guide-core change touches this file only.

Names that guide-core lists but this repository has no use for (``mock`` DataModel, ``luau_safety``) are explicit placeholders that raise when used.
The learning layer (``params``, ``promote``, ``skillgen``) is exposed under its own names; see ``preflight/learning_params.py``.
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from pathlib import Path

from guide_core import cli as _cli
from guide_core import commontools as _commontools
from guide_core import evals, feedback, manifest, mcpkit, retrieval, rubric, style
from guide_core import params as params  # noqa: F401
from guide_core import promote as promote  # noqa: F401
from guide_core import skillgen as skillgen  # noqa: F401
from guide_core.cli import DomainHooks, all_tools, emit, run
from guide_core.dryrun import Plan, Versioner
from guide_core.manifest import LibraryStore, base_schema, read_jsonl
from guide_core.mcpkit import ToolSpec, build_server, call_local, serve
from guide_core.project import Project
from guide_core.scope import PathNotAllowed, Scope, resolve_inside, safe_name

# --- guide-core interface names ---------------------------------------------------------------------
dryrun = types.SimpleNamespace(Plan=Plan, Versioner=Versioner)
config = types.SimpleNamespace(load_style=style.load_style, validate_style=style.validate_style, style_brief=style.style_brief, load_rubric=rubric.load_rubric,
                               score_rubric=rubric.score_rubric, Project=Project)


@dataclass(frozen=True)
class PathPolicy:
    """Read/write roots for a tool call. Thin wrapper over scope.resolve_inside."""

    roots: tuple[Path, ...]

    def resolve(self, path: str | Path) -> Path:
        return resolve_inside(path, list(self.roots))


scope = types.SimpleNamespace(PathPolicy=PathPolicy, resolve_inside=resolve_inside, safe_name=safe_name, PathNotAllowed=PathNotAllowed, Scope=Scope)


def _unavailable(name: str):
    def missing(*a, **k):
        raise NotImplementedError(f"guide-core '{name}' is not used by the asset preflight (no Luau or DataModel here)")

    return types.SimpleNamespace(unavailable=missing, note=f"{name}: not used by this repository")


mock = _unavailable("mock")
luau_safety = _unavailable("luau_safety")


# --- tool list ------------------------------------------------------------------------------------------
# guide_core.cli.all_tools() prepends the unscoped common tools (search_library, get_style_brief, record_run, ...). This repository exposes project-scoped
# versions under the same names, so the unscoped ones are filtered out here. NOTE: this patches the guide_core.cli module object, i.e. it is process-wide; fine for one
# server per process (how this runs), and a guide-core hook that makes it unnecessary is on guide-core's follow-up list.
SCOPED_COMMON = {"search_library", "get_style_brief", "find_past_corrections", "record_run", "record_decision", "promote_run"}
_orig_common_tools = _commontools.common_tools


def common_tools(project: Project, correction_dimensions=None) -> list[ToolSpec]:
    """The guide-core common tools (used by the scoped wrappers to reach shared behaviour)."""
    return _orig_common_tools(project, correction_dimensions)


def _filtered_common(project: Project, dims=None) -> list[ToolSpec]:
    return [t for t in _orig_common_tools(project, dims) if t.name not in SCOPED_COMMON]


_cli.common_tools = _filtered_common
