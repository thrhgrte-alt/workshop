---
name: roblox-economy-balancer-workflow
description: Balances a Roblox game's economy and progression for one named place - simulates ore values, tool tiers, vendor prices, upgrade costs, rebirths and gamepass boosts with a seeded simulator, finds grind walls, cliffs, runaway currency, dominant or dead upgrades and pay-to-win boosts, proposes the smallest fix without touching locked values, and emits reviewable Luau. Use when the user asks to check, tune, balance or rebalance pacing, prices, upgrade costs, rebirth or boosts, to import real values from Studio, or reports that a tier felt too slow or too fast.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "roblox-economy-balancer" or `python -m econbal call`. Reading or changing values in a real game needs Roblox Studio with its built-in MCP server (execute_luau) connected to the same agent; analysis works without Studio. The Luau mock needs lupa.
metadata:
  version: "0.2.0"
  domain: game-economy
---

# Economy and progression balancing workflow

Follow in order. Detail is in `references/`; open one only when needed. **The simulator does all the arithmetic: copy every number from a tool result.**

## 1. Fix the scope
- Ask which **project and place** if not stated. Every tool needs `project_id` and `place_id`; a refusal lists the known places. Never guess, never carry one place's numbers to another.
- `get_style_brief` (this place's bands) and `find_past_corrections` with the request. Treat past corrections as requirements.
- Say plainly: the bands are **placeholders** unless the user supplied them (the tool results say so), and archetype assumptions are assumptions.

## 2. Get the numbers into a spec (only if the place has no `economy.yaml`)
- `emit_import_luau` with `studios` = the output of Studio MCP's `list_roblox_studios` (it refuses if the open place is not the named one). Run the script with `execute_luau` (Edit). Give the printed JSON to `normalise_import`.
- Read every item of `assumptions` and `unresolved` to the user; fill the gaps with them. Review, then `validate_economy_spec`, then `save_spec_version` (`dry_run=false` after the user agrees). Format: `references/spec-format.md`, `references/import-format.md`.

## 3. Check
- `validate_economy_spec`, then `check_economy`. One verdict and ranked findings. Work **one finding at a time**, errors first.
- Drill down only where needed: `time_to_upgrade_table` (pacing vs bands), `flow_report` (inflation, content running out), `find_dominant_strategies`, `find_dead_options`, `monetisation_check`. Ask for `detail=true` only when the short answer is not enough.
- `sensitivity` shows which single number moves which tiers; use it to explain a trade-off, not to guess a fix.

## 4. Rebalance
- `propose_rebalance(dry_run=true)` for the finding. It returns the smallest 1-2 value changes, before/after timing for the steps that move, the locked values it skipped, and any new finding it avoided. Explain the trade-off in the user's terms; quote the tool's numbers.
- If it finds nothing: report why (locked values, nothing safe on its grid) and ask the user what they would change. Do not work around a lock.
- On a yes: `propose_rebalance(dry_run=false)` writes a NEW spec file; `save_spec_version`; re-run `check_economy` and `compare_specs` (a_ and b_ place ids may differ) to show the effect.

## 5. Hand values to the game (never overwrite)
- `export_values_luau(dry_run=true)` with `studios`; read the plan; then `dry_run=false` and `verify_on_mock=true`. The Luau creates ONE NEW config ModuleScript, embeds the place and refuses to run in another. Forward it to `execute_luau` (Edit) only in the matching place. If Studio refuses to write `Source`, paste `module_source` by hand. Wiring the new values into the game is the user's job.

## 6. Learn
- A playtest note ("tier 4 felt too slow") is `record_run` + `record_decision` with `corrections: [{dimension: pacing, tier: 4, felt: too_slow, note: ...}]` in the user's words. An accepted or rejected rebalance is a run whose `constraints.rebalance.changes` lists the paths, then accept/reject with the reason.
- `suggest_band_adjustments` before changing bands or archetypes. Suggestions are not applied; the user edits `bands.yaml` or the spec. Mark `is_global` only if the user says it applies to all places.

## 7. Report
One line verdict, the findings you worked on (with the tool's numbers), what changed (paths, before, after), what was not checked, open questions. Always state the project and place, that bands/archetypes are assumptions or placeholders, and that the simulator predicts relative pacing, not retention or revenue.

## Hard rules
- Never calculate numbers yourself. Never present a placeholder band or an assumed archetype as the user's design.
- Never change a locked value; never apply a change across places; never publish, save a place or overwrite a config.
- Never claim anything ran in Studio without a Studio report. Never reuse a `studios` list from an earlier session.

References: `references/workflow.md`, `references/spec-format.md`, `references/model.md`, `references/import-format.md`, `references/findings.md`.
