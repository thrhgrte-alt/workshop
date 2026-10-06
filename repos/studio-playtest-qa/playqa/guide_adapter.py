"""The ONE module through which this package reaches shared machinery (a test greps the package to enforce it).

Shared machinery comes from the installed ``guide-core`` library (``pip install -e /path/to/guide-core`` first; this package depends on it). This adapter exposes it under the
interface names the support-repo spec lists, so a guide-core change touches THIS FILE only.

    dryrun       guide_core.dryrun       Plan, Versioner, run_plan (dry-run plan objects, copy-on-write versions, explicit apply)
    feedback     guide_core.feedback     scoped runs, decisions, find_past_corrections, promote_run
    retrieval    guide_core.retrieval    hybrid search (used through the library store)
    evals        guide_core.evals        task loading, runner, compare, split self-written / real
    mock         guide_core.mock         the lupa mock DataModel; ``playqa.domain.mockworld`` extends it with players, remotes, pathfinding and planted faults
    luau_safety  guide_core.luau_safety  the ONE Luau lint and injection-safe embedding helpers
    mcpkit       guide_core.mcpkit       ToolSpec, build_server, call_local, serve, filter_groups
    config       style loading, ranges, manifest/library store, agent-file helpers, CLI scaffolding
    scope        guide_core.scope        Scope, require_scope, ProjectRegistry (projects.yaml), match_open_studio, scoped workspaces
    params, promote, skillgen, observe, propose, gate, telemetry   the learning layer (see playqa/learning_params.py)
"""

from __future__ import annotations

from types import SimpleNamespace

from guide_core import agentfiles as agentfiles  # noqa: F401
from guide_core import cli as _cli
from guide_core import commontools as _commontools
from guide_core import config as _config
from guide_core import dryrun as dryrun  # noqa: F401  (re-exported by name)
from guide_core import evals as evals  # noqa: F401
from guide_core import feedback as feedback  # noqa: F401
from guide_core import gate as gate  # noqa: F401
from guide_core import luau_safety as luau_safety  # noqa: F401
from guide_core import manifest as manifest  # noqa: F401
from guide_core import mcpkit as mcpkit  # noqa: F401
from guide_core import mock as mock  # noqa: F401
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

config = SimpleNamespace(Project=_project.Project, load_style=_style.load_style, validate_style=_style.validate_style, style_brief=_style.style_brief,
                         check_ranges=_style.check_ranges, load_yaml=_config.load_yaml, validate=_config.validate, LibraryStore=manifest.LibraryStore,
                         base_schema=manifest.base_schema, agentfiles=agentfiles, DomainHooks=_cli.DomainHooks, run=_cli.run, emit=_cli.emit,
                         all_tools=_cli.all_tools, common_tools=_commontools.common_tools)

ToolSpec = mcpkit.ToolSpec
Project = config.Project
Scope = scope.Scope
ScopeError = scope.ScopeError
DomainHooks = config.DomainHooks

# Names this repository replaces: the guide-core "common" tools are unscoped (no project_id, no dry_run), so this repository ships its own scoped versions and drops the
# unscoped library search. ``<PLAYQA>_DISABLE_GROUPS=rare`` leaves out the tools in the ``rare`` group.
UNSCOPED_COMMON = {"search_library", "get_style_brief"}


def all_tools(project: Project, hooks: DomainHooks) -> list[ToolSpec]:
    domain = hooks.tools(project)
    names = {t.name for t in domain}
    dims = hooks.correction_dimensions(project) if hooks.correction_dimensions else None
    common = [t for t in config.common_tools(project, dims) if t.name not in names and t.name not in UNSCOPED_COMMON]
    return mcpkit.filter_groups(project, common + domain)


_cli.all_tools = all_tools  # guide_core.cli.run() looks this name up at call time, so `serve` and `call` use this list
