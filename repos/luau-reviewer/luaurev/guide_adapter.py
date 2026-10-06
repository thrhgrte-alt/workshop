"""The ONE place this package touches shared machinery.

`guide-core` (the planned shared library) was not available and its spec file was not provided, so this module maps the names that spec lists
(dryrun, feedback, retrieval, evals, mock, luau_safety, mcpkit, config, scope) onto the shared kit vendored in ``luaurev/core``. Every other module
imports shared code ONLY from here (a test greps the package to enforce it). Swapping in the real guide-core means editing this file and nothing else.

Mapping (what exists in the vendored core, what does not):
  dryrun      -> Plan, Versioner                    (core/safety.py)
  feedback    -> core/feedback.py                    runs, decisions, corrections, promote_run
  retrieval   -> core/retrieval.py                   hybrid search over the example library
  evals       -> core/evals.py                       run_suite, compare_reports, load_tasks, save_report
  mcpkit      -> ToolSpec, build_server, call_local, serve
  config      -> Project, load_style/style_brief/check_ranges, manifest + agent-file helpers, CLI scaffolding (DomainHooks, run, emit)
  scope       -> resolve_inside, safe_name, PathNotAllowed
  mock        -> None: the vendored core has no mock DataModel (this repo does not need one: it reads code, it does not run it)
  luau_safety -> None: the vendored core has no Luau safety lint (this repo generates no Luau; it emits diff text only)
"""

from __future__ import annotations

from types import SimpleNamespace

from .core import agentfiles as _agentfiles
from .core import cli as _cli
from .core import commontools as _commontools
from .core import evals as evals  # noqa: F401  (re-exported by name)
from .core import feedback as feedback  # noqa: F401
from .core import manifest as _manifest
from .core import mcpkit as mcpkit  # noqa: F401
from .core import project as _project
from .core import retrieval as retrieval  # noqa: F401
from .core import safety as _safety
from .core import style as _style

dryrun = SimpleNamespace(Plan=_safety.Plan, Versioner=_safety.Versioner)
scope = SimpleNamespace(resolve_inside=_safety.resolve_inside, safe_name=_safety.safe_name, PathNotAllowed=_safety.PathNotAllowed)
config = SimpleNamespace(Project=_project.Project, load_style=_style.load_style, style_brief=_style.style_brief, check_ranges=_style.check_ranges,
                         LibraryStore=_manifest.LibraryStore, base_schema=_manifest.base_schema, agentfiles=_agentfiles,
                         DomainHooks=_cli.DomainHooks, run=_cli.run, emit=_cli.emit, all_tools=_cli.all_tools, common_tools=_commontools.common_tools)
mock = None
luau_safety = None

ToolSpec = mcpkit.ToolSpec
Project = config.Project
