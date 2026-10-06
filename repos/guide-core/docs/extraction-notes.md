# Extraction notes: what guide-core took from the three repositories

Spec: "Extract it, do not invent it." Three repositories carried their own copy of the shared machinery behind a `guide_adapter.py`: `luau-reviewer` (`luaurev/core`), `roblox-economy-balancer`
(`econbal/core`) and `asset-roblox-preflight` (`preflight/core`). The canonical source is `suite/kit/core`. This file records the diff, what was picked and why, what each repository's tests
relied on, what is new, and what is left over. Everything below was checked with `diff -r` on the files at the time of extraction (2026-10).

## 1. The diff

### Identical

* **The three vendored `core/` folders are byte-identical to each other and to `suite/kit/core`** (`diff -r -x __pycache__` is silent for all three; the same holds for the vendored cores of the five older repositories: `sdai`, `rbxlevel`, `setdress`, `rbxvfx`, `conceptai`).
  16 modules: `agentfiles`, `cli`, `commontools`, `doctor`, `embeddings`, `evals`, `feedback`, `imaging`, `manifest`, `mcpkit`, `project`, `retrieval`, `rubric`, `safety`, `style` and `__init__` (version 0.1.0).
* **The vendored core tests** (`tests/core_suite`: `test_core_library`, `test_core_loop`, `test_core_mcp_cli`, `core_support`, `conftest`: **50 tests**) are the same in all three apart from the names below.

### Differs only by name

* The package name in the core tests' imports (`luaurev.core` / `econbal.core` / `preflight.core`) and in `tests/core_suite/README.md`.
* The repositories' `Project(name, package, env_prefix, ...)` values and the environment-variable prefixes (`LUAUREV_`, `ECONBAL_`, `PREFLIGHT_`): data passed to the shared `Project`, not code.
* The tests' fixture env-var lists in `conftest.py` (which `<PREFIX>_*` variables get cleared).

### Genuinely different (all of it lives in the three adapters or in the repositories' own `domain/`)

| Concern | luau-reviewer | roblox-economy-balancer | asset-roblox-preflight |
|---|---|---|---|
| `dryrun` | `SimpleNamespace(Plan, Versioner)` | same | same |
| `scope` | `resolve_inside`, `safe_name`, `PathNotAllowed` | adds `scoped_project` / `ScopedProject` (per-place workspace `workspace/projects/<project>/<place>/`, pseudo place `_global/_global`) | `PathPolicy` wrapper over `resolve_inside`; its own `projects.ScopedProject` (workspace `projects/<project>/`) |
| `config` | style, `Project`, `LibraryStore`, `base_schema`, `DomainHooks`, `run`, `emit`, `all_tools`, `common_tools` | style, rubric (`load_rubric`, `score_rubric`) | style, rubric, `Project` |
| tool list | uses `cli.all_tools` unchanged; `server.py` drops its `RARE_TOOLS` by name when `LUAUREV_DISABLE_RARE=1` | **replaces `cli.all_tools`** (process-wide): domain tools replace common tools of the same name, the unscoped `search_library` is dropped, `ECONBAL_DISABLE_GROUPS=rare` drops tools whose description starts with `[rare]` | **patches `cli.common_tools`** (process-wide) to filter the unscoped `search_library`, `get_style_brief`, `find_past_corrections`, `record_run`, `record_decision`, `promote_run` |
| `mock` | `None` | lazy `econbal.domain.mock_luau` (lupa) | placeholder that raises `NotImplementedError` |
| `luau_safety` | `None` | lazy `econbal.domain.luau` (lint + generators) | placeholder that raises |
| registry (`projects.yaml`) | `id`, `alias`, `universe_id`, `ruleset`, `places: [{id, place_id (numeric), name, path, ruleset}]` | `project_id`, `alias`, `universe_id`, `places: [{place_id (slug), alias, studio_name, roblox_place_id, spec, bands}]` | `id`, `alias`, `place_id` (one numeric), `universe`, `profile` |

Outside the adapters, two other copies exist: the **Luau lint** (`econbal/domain/luau.py` and `rbxvfx/domain/luau.py`, different deny-lists; the vfx one also checks that every `:Destroy()` is guarded by a marker attribute)
and **four mock DataModels** (`econbal/domain/mock_luau.py` and the API-snapshot-driven `mock_roblox.py` of `rbxlevel`, `setdress` and `rbxvfx`, which differ from each other).

## 2. What was picked, and why

* **Base: `suite/kit/core`** (identical everywhere, so nothing to choose). Kept module-for-module under the same names so a migrating repository changes imports only. `safety.py` was split into `dryrun.py` (`Plan`, `Versioner`) and `scope.py` (`resolve_inside`, `safe_name`, `PathNotAllowed`); `guide_core.safety` remains as a re-export.
* **Per-place workspaces: economy balancer's `scoped_project`** (the only repository that had real scoping at workspace level). Moved into `scope.py` with the same semantics (`workspace/projects/<project>/<place>/`, `_global/_global`), and a `project_for_scope(project, Scope)` convenience. Preflight's project-level `ScopedProject` stays in preflight (a different, flatter layout that its tests and docs describe).
* **Tool groups: economy balancer's `[rare]` convention plus luau-reviewer's env var.** `ToolSpec.group` (new, trailing, optional) or a `[group]` description prefix; `<PREFIX>_DISABLE_GROUPS=rare` and the older `<PREFIX>_DISABLE_RARE=1` both work through `mcpkit.filter_groups`. Not applied inside `build_server` unless asked (`apply_groups=True`), so existing servers behave exactly as before.
* **Luau lint: the economy balancer's `lint`** as the base, `FORBIDDEN` widened to the union with the vfx list (`SaveToRoblox`) plus a few network spellings (`HttpGet`, `WebSocket`, `CreateWebStreamClient`). The vfx marker-guard check is domain-specific and stays in `rbxvfx`. Messages are unchanged (`forbidden token '...'`, `HttpService may only be used for JSONEncode`). The generators (`values_module_source`, `build_import_luau`, ...) are the economy balancer's domain code and were NOT extracted; only the validators (`validate_path`, `validate_name`, `place_guard`) and new `quote_string` / `check_long_bracket_safe` / `assert_safe` helpers are shared.
* **Mock DataModel: the economy balancer's `MockRoblox`** verbatim (error messages made generic, `available()` added). The three other mocks are driven by an API snapshot (property types, enums, Vector3/Color3) and are a different thing; they stay in their repositories and are not extracted.
* **Studio match: the economy balancer's `match_studio`**, generalised to a `PlaceEntry` (`scope.match_open_studio`) and made to carry `input_schema: schema_unverified`.
* **Registry: a new shared `projects.yaml` format** (alias, place id, universe, local file, profile paths) with a loader that accepts all three existing dialects (the keys in the table above) and normalises them. The three repositories keep their own loaders for now; adopting the shared one is a later, separate change.
* **Not extracted:** preflight's `PathPolicy` (a five-line wrapper) and `_unavailable` placeholders (per-repository decisions); luau-reviewer's layered ruleset and false-positive store (domain); the economy balancer's Luau generators.

### Bug found while extracting

The Luau validators in `econbal/domain/luau.py` use `re.match(r"^...$")`, and `$` also matches before a trailing newline, so `validate_path("ReplicatedStorage.Config\n")` and `validate_name("Config\n")` are accepted (checked by running them: both return the string).
guide-core uses `fullmatch`; a test pins it. The economy balancer's local copies still have the issue until its `domain/luau.py` is replaced by the shared module (follow-up; see section 5).

## 3. What each repository's tests relied on (and where it is kept)

All three: the 50 vendored-core tests (manifest/validation, retrieval and BM25 ranking, hashing embeddings, feedback runs/decisions/corrections/promotion, eval run/compare/check-results, `Plan`/`Versioner`/path allow-list, style and rubric, skills validation and agent-file sync, MCP server build/call, CLI commands, imaging helpers). They now run in guide-core as `tests/test_kit_*.py` against `guide_core.*`, unchanged apart from the import names, and still pass (plus 297 new tests: scope, dryrun, feedback, params, propose/gate/promote, skillgen, observe, telemetry, luau_safety, mock, mcpkit, config, evals, hygiene and the toy-tool eval tests).

* **luau-reviewer:** all shared imports go through `guide_adapter` (a test greps for violations; it now also forbids `guide_core` imports elsewhere); the adapter names `dryrun, feedback, retrieval, evals, mock, luau_safety, mcpkit, config, scope` exist with `mock is None` and `luau_safety is None`; feedback rows written with `record_run(constraints=..., tools=..., outputs=...)` and `record_decision(corrections=[{dimension, direction, note}])` (unchanged, rows only gained a `schema` key); `config.run` / `DomainHooks` / `register_cli` handler protocol (`handler(args, project) -> int`); `evals.run_suite` / `compare_reports` / `save_report` for `eval run`, `eval compare` and `eval-pr`; `agentfiles.sync(check=True)` and `validate_all_skills`; `base_schema` equality with `library/manifest.schema.json`; the README phrase `guide-core was not available` (kept as history).
* **economy balancer:** the `cli.all_tools` patch point (replacing it at import is what makes `serve` and `call` use the scoped tool list); `ECONBAL_DISABLE_GROUPS`; the per-place workspace layout (`projects/<project>/<place>/feedback/...`, `_global/_global`); `g.luau_safety.lint is luau.lint` (so the adapter keeps pointing at the repository's own copy); `hasattr(g.dryrun, "Plan")`, `g.scope.resolve_inside`, `g.config.load_style`; a stdio server with `tool_count >= 20`; `record_run` without a project refused with `missing project_id`. Removed: the test that compared its vendored `core/` with `suite/kit/core` (there is no vendored core any more).
* **preflight:** the `cli.common_tools` patch point and the exact set of scoped common tools it hides (a stdio smoke test counts 21 tools); `ga.mock.unavailable()` raising `NotImplementedError`; `PathPolicy`; `read_jsonl` re-exported to `domain/overrides.py`; a test that the package imports no `socket`/`subprocess`/network code; the README phrase `guide-core was not available`. Removed: the test that compared its vendored `core/` with the kit.

The kit tests were NOT re-expressed against the new scope-aware feedback; they pin the legacy unscoped behaviour, which is kept (`find_past_corrections` without `scope=` behaves exactly like the old `corrections_for`; the old name is an alias).

## 4. What is new in guide-core (not in any vendored copy)

`dryrun.run_plan` / `Plan.fingerprint`; `scope` (Scope, registry, Studio match, layered style); `config` (schema validation, locks, layering); `feedback` scope + derived SQLite index + executable regression cases + `recent_corrections`; `retrieval.search_items`;
`evals` strict loading, self-written/real split, Markdown reports, `compare_detailed`; `mock` and `luau_safety` as shared modules; `mcpkit` groups and catalog; and the whole learning layer (`observe`, `params`, `propose`, `gate`, `promote`, `skillgen`, `telemetry`).
Behaviour changes versus the kit that a caller can see: feedback rows gain `"schema": 1` (and `"scope"` when given); an `index.sqlite` appears next to `runs.jsonl`/`decisions.jsonl` once there is feedback (a read in a workspace with no feedback creates nothing); `ToolSpec` gains a trailing `group`; Luau validators use `fullmatch`.
The economy balancer's eval `scope-global-flag-shares-a-note-with-every-place` lists written files and now also lists the derived `index.sqlite` (the checks that task makes still pass).

## 5. Left over / not done

* The economy balancer still has its own `domain/luau.py` (lint + generators) and `domain/mock_luau.py`; the shared `luau_safety`/`mock` are supersets. Replacing them touches `domain/`, which the migration rules excluded.
* The adapters of the economy balancer and preflight still patch `guide_core.cli` at import (process-wide). A cleaner guide-core hook (for example `DomainHooks.replace_common` / `hide_common`) would remove both patches; not added, to keep the extracted API small and stable.
* The three repositories keep their own `projects.yaml` loaders; the shared loader (`scope.ProjectRegistry`) reads all three dialects but nothing was switched over.
* `match_open_studio`'s accepted key names are `schema_unverified`: no real `list_roblox_studios` capture was available.
* No real examples exist for `evals/real/` in any repository.
