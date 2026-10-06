"""The ONE module through which this package reaches shared machinery (a test greps the package to enforce it).

``guide-core`` was not available when this repository was built, so this adapter re-exports the equivalents from the vendored ``econbal.core``
(a copy of the suite kit) under the interface names the build instructions list. Swapping in the real ``guide-core`` means editing THIS FILE only.

    dryrun       Plan, Versioner                        (dry-run plan objects and copy-on-write versions)
    feedback     runs, decisions, corrections, promotion
    retrieval    hybrid search and BM25
    evals        task loading, runner, compare
    mock         the Luau DataModel mock (lupa)         (no equivalent in the vendored core: domain/mock_luau.py)
    luau_safety  lint for generated Luau                (no equivalent in the vendored core: domain/luau.py)
    mcpkit       ToolSpec, build_server, call_local, serve
    config       style loading and range checks, rubric
    scope        path allow-list, safe names, Project, and per-place scoping (``scoped_project``)

Two things the vendored core does not do, handled here so ``core/`` stays untouched:

* scoping: ``scoped_project`` returns a Project whose private workspace (feedback, versions, output, library) lives under
  ``workspace/projects/<project_id>/<place_id>/``; the pseudo place ``_global/_global`` holds records the user marks ``global``.
* tool list: ``all_tools`` lets a domain tool replace a common tool of the same name (the scoped feedback tools), drops the unscoped
  ``search_library``, and honours ``ECONBAL_DISABLE_GROUPS=rare`` to leave out tools whose description starts with ``[rare]``.
  It is installed into ``core.cli`` at import so ``serve`` and ``call`` use it.
"""

from __future__ import annotations

import dataclasses
import importlib
from types import SimpleNamespace
from typing import Any

from .core import agentfiles, cli, commontools, evals, feedback, manifest, mcpkit, retrieval, rubric, safety, style as _style
from .core.cli import DomainHooks, emit
from .core.mcpkit import ToolSpec, call_local
from .core.project import Project

GLOBAL = "_global"
UNSCOPED_COMMON = {"search_library"}

dryrun = SimpleNamespace(Plan=safety.Plan, Versioner=safety.Versioner)
config = SimpleNamespace(load_style=_style.load_style, style_brief=_style.style_brief, check_ranges=_style.check_ranges, validate_style=_style.validate_style,
                         load_rubric=rubric.load_rubric, score_rubric=rubric.score_rubric)


class ScopedProject(Project):
    """A Project whose private workspace is one place's folder."""

    @property
    def workspace(self):
        return Project.workspace.fget(self) / "projects" / self.scope_project / self.scope_place


def scoped_project(project: Project, project_id: str, place_id: str) -> Project:
    sp = ScopedProject(**{f.name: getattr(project, f.name) for f in dataclasses.fields(Project)})
    keep = lambda v: v if v == GLOBAL else safety.safe_name(v)  # '_global' starts with an underscore that safe_name would strip, and a user project may be called 'global'
    object.__setattr__(sp, "scope_project", keep(project_id))
    object.__setattr__(sp, "scope_place", keep(place_id))
    return sp


scope = SimpleNamespace(resolve_inside=safety.resolve_inside, safe_name=safety.safe_name, Project=Project, scoped_project=scoped_project, GLOBAL=GLOBAL)


def __getattr__(name: str) -> Any:  # lazy, so importing the adapter never imports domain modules that import the adapter
    if name == "mock":
        return importlib.import_module("econbal.domain.mock_luau")
    if name == "luau_safety":
        return importlib.import_module("econbal.domain.luau")
    raise AttributeError(name)


def all_tools(project: Project, hooks: DomainHooks) -> list[ToolSpec]:
    dims = hooks.correction_dimensions(project) if hooks.correction_dimensions else None
    domain = hooks.tools(project)
    names = {t.name for t in domain}
    common = [t for t in commontools.common_tools(project, dims) if t.name not in names and t.name not in UNSCOPED_COMMON]
    tools = common + domain
    disabled = {g.strip() for g in (project.env("DISABLE_GROUPS") or "").split(",") if g.strip()}
    if disabled:
        tools = [t for t in tools if not any(t.description.startswith(f"[{g}]") for g in disabled)]
    return tools


cli.all_tools = all_tools  # core.cli.run() looks this name up at call time
