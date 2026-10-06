# guide-core public API (0.1.x)

Everything listed here is the stable surface. A test (`tests/test_hygiene.py`) fails if a name in this file stops existing, so the doc
and the code cannot drift. Anything not listed (underscore names, helpers) may change in a minor release. Breaking a listed name needs
a major version bump and a note in `CHANGELOG.md` ("Migration notes").

Version: `guide_core.__version__` (package) and `guide_core.SCHEMA_VERSION` (on-disk formats: feedback rows and index, params store,
knowledge store, observation log). A reader that meets another schema version refuses to guess.

Install: `pip install -e /path/to/guide-core` (extras: `[mock]` for lupa, `[imaging]`, `[embeddings]`, `[dev]`). Python 3.10+.

## Building a repository on guide-core (the short version)

1. `project = Project(name=..., package=..., env_prefix=..., root=..., domain=...)` (`guide_core.project`) - where data lives; `workspace/` is private and git-ignored.
2. `registry = scope.ProjectRegistry.load("projects.yaml")`; every tool starts with `sc = scope.require_scope(project_id, place_id, registry=registry)` and refuses on `ScopeError`.
3. Writes return `dryrun.Plan`s; apply only through `dryrun.run_plan(plan, applier, apply=True, journal=...)`.
4. Luau you generate goes through `luau_safety.validate_path / validate_name / quote_string / assert_safe`; test it on `mock.MockRoblox`.
5. Declare tools with `mcpkit.ToolSpec(name, fn, description, read_only=..., group=...)`; wrap with `telemetry.Telemetry.instrument`.
6. Record runs with `feedback.record_run(..., scope=sc)`, verdicts with `feedback.record_decision`, read with `feedback.find_past_corrections(..., scope=sc)`.
7. Register thresholds/weights/bands/confidences as `params.ParamSpec` in one `ParamStore`; read them with `store.value(name, sc)`.
8. Evals: `evals.load_split("evals/tasks", "evals/real")`, `evals.run_split`, `evals.compare_detailed`, `evals.render_markdown`.
9. Improvement loop: `observe.RunLog` -> `propose.find_patterns/make_proposals` -> `gate.run_gate` -> `promote.promote_proposal` (a person approves) -> `promote.monitor_change`.
10. `skillgen.export_skill` / `skillgen.add_export_skill_command` for the skill export.

## Modules and names

### `guide_core.project`
`Project` (frozen dataclass: name, package, env_prefix, root, domain, asset_kinds; `.env(key)`, `.workspace`, `.feedback_dir`, `.versions_dir`, `.output_dir`, `.style_file`, `.allowed_roots`).

### `guide_core.dryrun`
`Plan(summary, steps, warnings)` with `.add(op, target, detail)`, `.to_dict(dry_run)`, `.fingerprint()`; `Versioner(project)` (`.save/.load/.history/.restore`); `run_plan(plan, applier, *, apply=False, journal=None, expect=None)`; `text_diff(before, after, name)`.

### `guide_core.scope`
`Scope(project_id, place_id=None, is_global=False)` (`.key`, `.label`, `.covers(other)`, `.to_dict()`, `Scope.make_global()`); `ScopeError`; `require_scope(project_id, place_id, *, need_place, registry, is_global)`; `scope_from_dict`;
`ProjectRegistry` (`.load(path)`, `.from_dict`, `.resolve(project_id, place_id)`, `.project(id)`, `.place(scope)`, `.profile_path(scope, key)`, `.project_ids()`), `ProjectEntry`, `PlaceEntry`, `load_registry`;
`match_open_studio(place_entry, studios, *, label)`; `scoped_project(project, project_id, place_id)`, `project_for_scope(project, scope)`, `GLOBAL`;
`overlay_style(base, override)`, `load_layered_style(project, scope)`; `resolve_inside(path, roots)`, `safe_name`, `PathNotAllowed`.

### `guide_core.config`
`load_yaml`, `validate(data, schema)`, `load_config(path, schema=None) -> Config` (`.get`, `.is_locked`, `.unverified`), `layer(base, *overrides, strict=True) -> Resolved` (`.get`, `.source_of`, `.rejected`), `ConfigError`, `LockedValueError`; re-exports `load_style`, `validate_style`, `style_brief`, `check_ranges`, `load_rubric`, `validate_rubric`, `score_rubric`.

### `guide_core.feedback`
`record_run`, `record_decision`, `find_past_corrections` (alias `corrections_for`), `promote_run`, `get_run`, `list_runs`, `decisions_for`, `accepted_regression_cases`, `accepted_decisions`, `recent_corrections`, `rebuild_index`, `index_info`, `SCHEMA`, `DECISIONS`.

### `guide_core.retrieval`
`search(store, query, ...)` (library assets), `search_items(items, query, tags=..., embedder=..., vectors=...)` (any dict records), `bm25_scores`, `tokens`, `color_distance`, `palette_similarity`. Embeddings live in `guide_core.embeddings` (a dependency-free hashing embedder always; sentence-transformers/CLIP via the `[embeddings]` extra).

### `guide_core.evals`
`load_tasks(dir, strict=False)`, `load_split(tasks_dir, real_dir)`, `validate_task`, `load_rubric`, `validate_rubric`, `score_rubric`, `run_suite`, `run_split`, `check_results_dir`, `evaluate_task`, `compare_reports`, `compare_detailed`, `save_report`, `save_markdown`, `render_markdown`, `GENERIC_CHECKS`, `dig`.

### `guide_core.mock`
`MockRoblox` (`.run(code)`, `.ensure_path`, `.add`, `.set_place`, `.info`, `.printed`), `available()`. Needs `lupa`.

### `guide_core.luau_safety`
`lint(code, *, allow, extra_forbidden)`, `check`, `assert_safe`, `FORBIDDEN`, `validate_path`, `validate_name`, `quote_string`, `check_long_bracket_safe`, `place_guard`.

### `guide_core.mcpkit`
`ToolSpec(name, fn, description, read_only, destructive, idempotent, expensive, group)` (`.label`, `.effective_group`), `build_server(project, specs, instructions, *, apply_groups)`, `serve`, `call_local`, `disabled_groups(project)`, `filter_groups(project, specs)`, `tool_catalog(specs)`.

### `guide_core.observe`
`RunLog(path)` (`.log_run`, `.log_action`, `.rows`, `.runs`), `redact`.

### `guide_core.params`
`ParamSpec`, `ParamStore` (`.register`, `.resolve`, `.value`, `.update`, `.nudge`, `.rollback`, `.history`, `.snapshot`, `.view`, `.events`, `.knowledge_version`), `Resolution`, `ParamView`, `ParamError`, `LockedParameter`, `StepTooLarge`, `OutOfRange`.

### `guide_core.propose`
`find_patterns`, `make_proposals`, `ProposalStore` (`.add`, `.get`, `.all`, `.set_status`), `STATUSES`. No function applies a proposal.

### `guide_core.gate`
`run_gate(proposal, suites, solver_for, checks, *, root)`, `gate_and_record`, `correction_tasks`, `GateResult`.

### `guide_core.promote`
`KnowledgeStore` (`.learn`, `.confirm`, `.contradict`, `.mark_non_transferable`, `.global_candidates`, `.candidate_report`, `.promote_global`, `.demote_global`, `.retrievable`, `.archive_stale`, `.unarchive`, `.contradictions`, `.changelog`, `.version`), `promote_proposal`, `reject_proposal`, `monitor_change`, `abstract_signature`, `PromotionError`.

### `guide_core.skillgen`
`export_skill(...)` (optional `corrections=` for repositories whose feedback rows carry no scope), `brief(result)`, `add_export_skill_command(sub, project, kwargs_for, resolve=None)`, `MARKER`, `MAX_SKILL_LINES`.

### `guide_core.telemetry`
`Telemetry(path)` (`.record`, `.wrap`, `.instrument`, `.summary`, `.rows`), `check_budgets`, `output_chars`.

### Vendored-kit modules (kept so existing repositories migrate by changing imports only)
`guide_core.manifest` (`LibraryStore`, `base_schema`, `read_jsonl`, `write_jsonl_atomic`, `validate_asset`, `load_schema`), `guide_core.style`, `guide_core.rubric`, `guide_core.agentfiles` (`validate_skill`, `validate_all_skills`, `sync`),
`guide_core.cli` (`DomainHooks`, `run`, `emit`, `all_tools`, `build_parser`), `guide_core.commontools` (`common_tools`), `guide_core.doctor`, `guide_core.imaging`, `guide_core.embeddings` (`HashingEmbedder`, `load_embedder`, `build_index`),
`guide_core.safety` (deprecated alias module: `Plan`, `Versioner`, `resolve_inside`, `safe_name`, `PathNotAllowed`; new code uses `dryrun` and `scope`).
