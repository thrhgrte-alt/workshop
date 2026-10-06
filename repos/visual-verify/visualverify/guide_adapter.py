"""The ONE module through which this package reaches shared machinery (a test greps the package to enforce it).

Shared machinery comes from the installed ``guide-core`` library (``pip install -e /path/to/guide-core`` first; this package depends on it). The names below
follow the interface list of the support-repo spec, so a guide-core change touches THIS FILE only.

    dryrun       guide_core.dryrun      Plan, Versioner, run_plan (every write returns a plan; apply is explicit and journaled)
    feedback     guide_core.feedback    runs, decisions (scoped), find_past_corrections, promote_run
    retrieval    guide_core.retrieval   hybrid search over the example library
    evals        guide_core.evals       task loading (self-written / real split), runner, compare, reports
    mcpkit       guide_core.mcpkit      ToolSpec, build_server, call_local, serve, filter_groups
    config       style loading, rubric-free; Project, LibraryStore, base_schema, agent files, CLI scaffolding
    scope        guide_core.scope       Scope, require_scope, ProjectRegistry (projects.yaml), resolve_inside, project_for_scope
    params, observe, propose, gate, promote, skillgen, telemetry    the learning layer (see visualverify/learning_params.py)
    mock         None: no Luau is generated or run here
    luau_safety  None: this repository emits no Luau (it reads pixels and writes JSON and PNG only)
"""

from __future__ import annotations

from types import SimpleNamespace

from guide_core import agentfiles as _agentfiles
from guide_core import cli as _cli
from guide_core import commontools as _commontools
from guide_core import dryrun as dryrun  # noqa: F401  (re-exported by name)
from guide_core import evals as evals  # noqa: F401
from guide_core import feedback as feedback  # noqa: F401
from guide_core import gate as gate  # noqa: F401
from guide_core import manifest as _manifest
from guide_core import mcpkit as mcpkit  # noqa: F401
from guide_core import observe as observe  # noqa: F401
from guide_core import params as params  # noqa: F401
from guide_core import project as _project
from guide_core import promote as promote  # noqa: F401
from guide_core import propose as propose  # noqa: F401
from guide_core import retrieval as retrieval  # noqa: F401
from guide_core import scope as scope  # noqa: F401
from guide_core import skillgen as skillgen  # noqa: F401
from guide_core import style as _style
from guide_core import telemetry as telemetry  # noqa: F401
from guide_core.cli import DomainHooks, emit  # noqa: F401
from guide_core.mcpkit import ToolSpec, call_local  # noqa: F401
from guide_core.project import Project  # noqa: F401

config = SimpleNamespace(Project=_project.Project, load_style=_style.load_style, validate_style=_style.validate_style, style_brief=_style.style_brief,
                         check_ranges=_style.check_ranges, LibraryStore=_manifest.LibraryStore, base_schema=_manifest.base_schema, agentfiles=_agentfiles,
                         DomainHooks=_cli.DomainHooks, run=_cli.run, emit=_cli.emit, common_tools=_commontools.common_tools)
mock = None
luau_safety = None
cli_run = _cli.run  # the shared command line entry point (python -m visualverify ...)

UNSCOPED_COMMON = {"search_library"}  # the unscoped library search is dropped: every tool here takes project_id


def all_tools(project: Project, hooks: DomainHooks) -> list[ToolSpec]:
    """Domain tools replace common tools of the same name (the scoped feedback tools); the unscoped ``search_library`` is dropped; disabled groups are left out
    (``VISUALVERIFY_DISABLE_GROUPS=rare``). Installed into ``guide_core.cli`` at import so ``serve`` and ``call`` use it."""
    dims = hooks.correction_dimensions(project) if hooks.correction_dimensions else None
    domain = hooks.tools(project)
    names = {t.name for t in domain}
    common = [t for t in _commontools.common_tools(project, dims) if t.name not in names and t.name not in UNSCOPED_COMMON]
    return mcpkit.filter_groups(project, common + domain)


_cli.all_tools = all_tools  # guide_core.cli.run() looks this name up at call time
