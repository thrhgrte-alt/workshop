"""The ONE module through which this package reaches shared machinery (a test greps the package to enforce it).

Shared machinery comes from the installed ``guide-core`` library (``pip install -e /path/to/guide-core`` first; this package depends on it). This adapter exposes it under the
interface names the build instructions list, so a guide-core change touches THIS FILE only.

    dryrun       guide_core.dryrun      Plan, Versioner, run_plan (dry-run plan objects, copy-on-write versions, explicit apply)
    feedback     guide_core.feedback    runs, decisions, corrections, promotion
    retrieval    guide_core.retrieval   hybrid search and BM25
    evals        guide_core.evals       task loading, runner, compare
    mock         the Luau DataModel mock (lupa)         (domain/mock_luau.py: this repository's own copy; guide_core.mock is the shared one)
    luau_safety  lint for generated Luau                (domain/luau.py: this repository's own copy; guide_core.luau_safety is the shared one)
    mcpkit       guide_core.mcpkit      ToolSpec, build_server, call_local, serve, filter_groups
    config       style loading and range checks, rubric
    scope        guide_core.scope       path allow-list, safe names, Project, and per-place scoping (``scoped_project``)
    params, promote, skillgen           the learning layer (see econbal/learning_params.py)

Two things are handled here on top of guide-core:

* tool list: ``all_tools`` lets a domain tool replace a common tool of the same name (the scoped feedback tools), drops the unscoped ``search_library``, and honours
  ``ECONBAL_DISABLE_GROUPS=rare`` (via ``guide_core.mcpkit.filter_groups``) to leave out tools whose description starts with ``[rare]``. It is installed into ``cli`` at import so
  ``serve`` and ``call`` use it.
* per-place workspaces: ``scope.scoped_project`` (guide-core) returns a Project whose private workspace (feedback, versions, output, library) lives under
  ``workspace/projects/<project_id>/<place_id>/``; the pseudo place ``_global/_global`` holds records the user marks ``global``.

Not yet shared: this repository still carries its own Luau lint (``domain/luau.py``) and mock DataModel (``domain/mock_luau.py``); ``guide_core.luau_safety`` and
``guide_core.mock`` are supersets extracted from them. Replacing the local copies is a follow-up that touches ``domain/``, outside this migration.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

from guide_core import agentfiles, cli, commontools, evals, feedback, manifest, mcpkit, retrieval, rubric
from guide_core import dryrun as dryrun  # noqa: F401
from guide_core import params as params  # noqa: F401
from guide_core import promote as promote  # noqa: F401
from guide_core import scope as _scope
from guide_core import skillgen as skillgen  # noqa: F401
from guide_core import style as _style
from guide_core.cli import DomainHooks, emit
from guide_core.mcpkit import ToolSpec, call_local
from guide_core.project import Project

GLOBAL = _scope.GLOBAL
UNSCOPED_COMMON = {"search_library"}

config = SimpleNamespace(load_style=_style.load_style, style_brief=_style.style_brief, check_ranges=_style.check_ranges, validate_style=_style.validate_style,
                         load_rubric=rubric.load_rubric, score_rubric=rubric.score_rubric)

scoped_project = _scope.scoped_project
scope = SimpleNamespace(resolve_inside=_scope.resolve_inside, safe_name=_scope.safe_name, Project=Project, scoped_project=scoped_project, GLOBAL=GLOBAL,
                        Scope=_scope.Scope, ScopeError=_scope.ScopeError)


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
    return mcpkit.filter_groups(project, common + domain)


cli.all_tools = all_tools  # guide_core.cli.run() looks this name up at call time
