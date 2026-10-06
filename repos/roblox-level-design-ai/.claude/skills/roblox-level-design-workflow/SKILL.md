---
name: roblox-level-design-workflow
description: Turns a Roblox level design brief into a structured level spec, analyses it (connectivity, routes, loops, choke points, sightlines, landmarks, pacing, fairness, spawn safety), builds a walkable blockout, preserves locked layout constraints, and records feedback. Use when the user asks for a level, map, arena, dungeon, obby layout, blockout or greybox, wants an existing layout checked or revised, or says parts of the layout are locked or must be preserved.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "roblox-level-design-ai" or `python -m rbxlevel call`. Building in Studio needs Roblox Studio with its built-in MCP server enabled and connected to the same agent; everything else works without Studio. Optional simulation needs lupa.
metadata:
  version: "0.1.0"
  domain: roblox-level-design
---

# Roblox level design workflow

Follow in order. Detail is in `references/`; open a reference only when needed.

## 1. Understand the brief and its locks
- `get_style_brief`; `find_past_corrections` with the brief text (treat results as requirements).
- Write down: purpose, player count/teams, size, must-have routes, landmarks, and **everything locked** (rooms, entrances,
  boundaries, scale, camera angles). Anything unclear goes into `open_questions` in the spec; ask the user only if a missing
  choice would change the layout materially.

## 2. Retrieve
- `search_level_library`; read the top 2-3 hits and **all returned avoid examples**. `list_level_templates` and pick the closest.

## 3. Create the spec
- `create_level_spec` (template + parameters, or your own spec per `references/spec-format.md`) with `dry_run=true`. Fix every
  finding. Put locks in `locked`. When the spec is valid, call it again with `dry_run=false`: that saves **revision 1, the baseline for locks**.
- If the brief locks existing geometry, encode it exactly (rects, floors, connections) before changing anything else.

## 4. Analyse and revise
- `evaluate_level`. Errors first, then warnings. Use `analyze_routes_and_loops`, `check_sightlines`, `validate_connectivity` to dig in.
- Revise the spec, `save_level_revision` (dry run shows the diff and blocks lock violations), re-evaluate. Change one thing at a time.

## 5. Build
- `build_blockout(dry_run=true)`: part counts, walkability, assumptions. Then `simulate_blockout`, then `build_blockout(dry_run=false)`.
- Forward the Luau to Studio's `execute_luau` (`datamodel_type: "Edit"`, `studio_id` from `list_roblox_studios`; read the schema for the code
  argument). `parse_studio_report`; quote errors verbatim. Then `inspect_blockout` (script, then compare) to catch drift or hand edits.

## 6. Review from the player's point of view
- `capture_review_views`; call Studio's `screen_capture` for each view: `route_*` first (in order), then `landmark_*`, then rooms, then `overview`.
- Look at the captures yourself. Ask: can a newcomer tell where to go? Is a landmark in view? Do doors line up into long lanes?
- `evaluate_level` with `manual_scores` only for what you actually saw; leave the rest unscored for the user.

## 7. Report and record
Report: what was built, the route/loop/fairness/sightline numbers, locks checked, assumptions, open questions, capture paths, and one proposed next change.
`record_run`; `record_decision` with the user's words and a correction dimension (scale, routes, sightlines, landmarks, pacing, fairness, verticality, spawns,
readability, density). `promote_run` with `confirm=false`; `confirm=true` only after the user says yes.

## Hard rules
- Never publish, save or overwrite a live place; never touch instances you did not create. Never loosen the lint.
- Never change locked geometry. Never claim "done" without a walkability result and a Studio report (or say Studio was not used).
- Never judge a layout from the plan view alone. Never present subjective scores as measurements.

References: `references/spec-format.md`, `references/workflow.md`, `references/reading-the-metrics.md`, `references/studio-mcp.md`.
