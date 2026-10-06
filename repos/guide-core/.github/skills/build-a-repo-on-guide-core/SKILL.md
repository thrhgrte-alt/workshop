---
name: build-a-repo-on-guide-core
description: Use when building or migrating a Roblox guide repository (a CLI plus stdio MCP server that checks, plans or generates things for a Roblox place) so that it uses the shared guide-core library instead of re-implementing scope, dry-run, feedback, evals, params or skill export.
---
# Building a repo on guide-core

Install first: `pip install -e /path/to/guide-core` (add `--break-system-packages` on a system Python). Public API: `docs/api.md` in guide-core. Do not re-implement anything listed there.

1. **Skeleton.** `Project(name, package, env_prefix, root, domain)`. Put every guide_core import in ONE file, `<pkg>/guide_adapter.py`, so a later guide-core change touches one place.
2. **Scope first.** Every tool takes `project_id` (+ `place_id`). `scope.require_scope(project_id, place_id, registry=ProjectRegistry.load("projects.yaml"))`; a `ScopeError` is a refusal to return, not to work around.
   A tool that needs Studio gets the hub's `list_roblox_studios` output as an argument and calls `scope.match_open_studio(registry.place(sc), studios)`.
3. **Writes are plans.** Return `dryrun.Plan`; apply only via `dryrun.run_plan(plan, applier, apply=True, journal=...)`. Default `dry_run=True`.
4. **Luau you emit** goes through `luau_safety.validate_path/validate_name/quote_string` and `luau_safety.assert_safe`; test it on `mock.MockRoblox` (needs `lupa`).
5. **Tools.** `mcpkit.ToolSpec(name, fn, description, read_only=..., group="rare")`; annotate `-> dict[str, Any]`; keep descriptions short; one-line verdict first, findings capped at 10;
   wrap with `Telemetry.instrument` and put a size budget in the evals.
6. **Learning layer, in order.** `feedback.record_run/record_decision(..., scope=sc)` -> `observe.RunLog` signals -> `propose.find_patterns/make_proposals` ->
   `gate.run_gate` (self-written + `evals/real/` + past accepted corrections) -> a person approves -> `promote.promote_proposal(..., approved_by=name, confirm=True)`.
   Register every threshold/weight/band/confidence as a `params.ParamSpec` (same default as before); read it with `ParamStore.value(name, sc)`; never hard-code it.
7. **Skill export.** `skillgen.add_export_skill_command(sub, project, kwargs_for)` gives the CLI an `export-skill` command (dry run unless `--write`).
8. **Evals.** >= 30 tasks, known-bad and known-good, a mutation per behaviour so a broken tool fails them, `evals/real/` (empty + README), report self-written and real separately.
9. **README must say** what was verified on fixtures, what was never run against real Studio/Blender/Luau tools, which numbers are placeholders, and that learning improves only as far as the corrections and evals supplied.

Never: auto-promote, auto-apply, delete learned items, connect to Studio, publish, or upload private data.
