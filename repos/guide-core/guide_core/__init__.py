"""guide-core: shared machinery for the Roblox guide repositories.

Public API (stable within a major version; see CHANGELOG.md and docs/api.md):

    dryrun       Plan, Versioner, run_plan (plan -> explicit apply -> journal)
    scope        Scope, ProjectRegistry, resolve/require_scope, match_open_studio, scoped_project, layered style
    config       YAML load, schema validation, locked values, layered config
    feedback     record_run, record_decision, find_past_corrections, promote_run (JSONL + SQLite index)
    retrieval    tag + text hybrid search, optional embeddings
    evals        task format, runner, compare, JSON + Markdown report, self-written vs real split
    mock         mock Roblox DataModel (optional dependency: lupa)
    luau_safety  the ONE Luau lint, plus injection-safe embedding helpers
    mcpkit       ToolSpec (read-only / write labels, groups), stdio server builder
    observe / params / propose / gate / promote / skillgen / telemetry   the learning layer

Nothing in this package changes a model's weights, publishes anything, or talks to Studio.
"""

__version__ = "0.1.0"

#: Version of the on-disk formats written by this package (feedback JSONL rows, SQLite index, params store,
#: knowledge store, observation log). Bumped only with a migration note in CHANGELOG.md.
SCHEMA_VERSION = 1
