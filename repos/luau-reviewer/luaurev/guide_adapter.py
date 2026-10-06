"""The ONE place this package touches shared machinery.

Shared machinery comes from the installed ``guide-core`` library (``pip install -e /path/to/guide-core`` first; this package depends on it). This module
maps the names the support-repo spec lists (dryrun, feedback, retrieval, evals, mock, luau_safety, mcpkit, config, scope) onto ``guide_core`` modules.
Every other module imports shared code ONLY from here (a test greps the package to enforce it), so a guide-core change touches this file and nothing else.

Mapping:
  dryrun      -> guide_core.dryrun                 Plan, Versioner, run_plan
  feedback    -> guide_core.feedback               runs, decisions, find_past_corrections (alias corrections_for), promote_run
  retrieval   -> guide_core.retrieval              hybrid search over the example library
  evals       -> guide_core.evals                  run_suite, compare_reports, load_tasks, save_report
  mcpkit      -> guide_core.mcpkit                 ToolSpec, build_server, call_local, serve
  config      -> Project, load_style, style_brief, check_ranges, manifest + agent-file helpers, CLI scaffolding (DomainHooks, run, emit)
  scope       -> guide_core.scope                  resolve_inside, safe_name, PathNotAllowed (this repo's project/place registry is luaurev.domain.projects)
  params / skillgen / promote ... -> guide_core.params, guide_core.skillgen, guide_core.promote (the learning layer; see luaurev/learning_params.py)
  mock        -> None: this repo does not need a mock DataModel (it reads code, it does not run it)
  luau_safety -> None: this repo generates no Luau; it emits diff text only
"""

from __future__ import annotations

from types import SimpleNamespace

from guide_core import agentfiles as _agentfiles
from guide_core import cli as _cli
from guide_core import commontools as _commontools
from guide_core import dryrun as dryrun  # noqa: F401  (re-exported by name)
from guide_core import evals as evals  # noqa: F401
from guide_core import feedback as feedback  # noqa: F401
from guide_core import manifest as _manifest
from guide_core import mcpkit as mcpkit  # noqa: F401
from guide_core import params as params  # noqa: F401
from guide_core import project as _project
from guide_core import promote as promote  # noqa: F401
from guide_core import retrieval as retrieval  # noqa: F401
from guide_core import scope as scope  # noqa: F401
from guide_core import skillgen as skillgen  # noqa: F401
from guide_core import style as _style

config = SimpleNamespace(Project=_project.Project, load_style=_style.load_style, style_brief=_style.style_brief, check_ranges=_style.check_ranges,
                         LibraryStore=_manifest.LibraryStore, base_schema=_manifest.base_schema, agentfiles=_agentfiles,
                         DomainHooks=_cli.DomainHooks, run=_cli.run, emit=_cli.emit, all_tools=_cli.all_tools, common_tools=_commontools.common_tools)
mock = None
luau_safety = None

ToolSpec = mcpkit.ToolSpec
Project = config.Project
