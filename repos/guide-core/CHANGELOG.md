# Changelog

Versioning: `MAJOR.MINOR.PATCH`. A change to a name listed in `docs/api.md`, or to an on-disk format (`SCHEMA_VERSION`), that can break
a caller needs a MAJOR bump (while 0.x: a MINOR bump) and a "Migration notes" entry here.

## 0.1.0 - 2026-10-06

First release. Extracted from the shared kit that `luau-reviewer`, `roblox-economy-balancer` and `asset-roblox-preflight` vendored
(see `docs/extraction-notes.md`), plus the modules the support-repo spec asks for.

Added: `dryrun`, `scope`, `config`, `observe`, `params`, `propose`, `gate`, `promote`, `skillgen`, `telemetry`, `mock`, `luau_safety`;
new functions in `feedback` (scope, SQLite index, regression cases), `evals` (split, markdown, strict loading), `retrieval.search_items`,
`mcpkit` (groups, catalog).

Behaviour kept from the vendored kit (the three repositories' tests rely on it): `Plan.to_dict`, `Versioner`, `record_run/record_decision`
rows and `corrections_for` ranking (plain BM25), `ToolSpec` positional fields, `cli.run` / `cli.all_tools` / `cli.common_tools` patch points,
`evals.compare_reports`, `LibraryStore`.

Differences from the vendored kit that a caller may notice:

- feedback rows gain `"schema": 1` and, when a scope is passed, a `"scope"` object; a derived `index.sqlite` appears next to the JSONL files.
- `ToolSpec` gains a trailing optional `group` field.
- `luau_safety` validators use `fullmatch` (the older copies used `match` + `$`, which accepted a trailing newline).

Also in 0.1.0 (added after the first commit, before any release): a read in a workspace with no feedback creates no files; `skillgen.export_skill(corrections=...)`, `skillgen.brief`, a `resolve=` hook for the
`export-skill` command; `evals` re-exports the rubric functions; `scope.match_open_studio` results carry `input_schema: schema_unverified`.

### Migration notes

- From a vendored `<pkg>/core`: replace `from .core import X` with `from guide_core import X` (same module names). `core.safety` is now `guide_core.dryrun`
  (Plan, Versioner) and `guide_core.scope` (resolve_inside, safe_name, PathNotAllowed); `guide_core.safety` remains as an alias. See `docs/migrating-older-repos.md`.
