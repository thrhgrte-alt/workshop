---
name: modular-set-dressing-workflow
description: Dresses a scene with a modular asset kit - places approved modules, snaps chairs to tables and props to surfaces, runs seeded rule-driven dressing, checks collisions, path and sightline clearance and composition, preserves locked pieces, and exports safe Luau for Roblox Studio. Use when the user asks to set dress, furnish, populate, decorate or arrange a room or region with kit pieces, to check or fix an existing arrangement, or says some pieces must not move.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "modular-set-dressing-ai" or `python -m setdress call`. Placing in Studio needs Roblox Studio with its built-in MCP server enabled and connected to the same agent; everything else works without Studio. Optional simulation needs lupa.
metadata:
  version: "0.1.0"
  domain: modular-set-dressing
---

# Modular set dressing workflow

Follow in order. Detail is in `references/`; open a reference only when needed.

## 1. Understand the brief and its locks
- `get_style_brief`; `find_past_corrections` with the brief text (treat results as requirements).
- Write down: the room/region and its size, the functional paths and sightlines that must stay open, the focal point, the mood, and **everything locked**.
  Unclear choices go in open questions; ask only when the answer would change the arrangement materially.

## 2. Know the kit
- `kit_report` (note unresolved metadata: those values are assumptions) and `search_kit` for what you need. **Only approved modules.** If the kit lacks something, say so and propose
  adding it to the kit; do not substitute silently.
- `search_scene_library`; read the top 2-3 hits and **all returned avoid examples**.

## 3. Set up the scene
- Write the scene (region, exclusions, paths, corridors, focal points, locked instances) per `references/scene-format.md`. Existing pieces that must stay go in as instances with `locked: true`.
- `inspect_scene`; fix every error. Save the first version (a write with `dry_run=false`): it becomes the lock baseline.

## 4. Place, one idea at a time
- Hero piece first at the focal point (`place_module`), then supports with `snap_to_socket` (chairs to tables, items to surfaces), then `dress_region` for the small accents with a seed.
- Every write is a dry run first: read `findings` and `explain`, then repeat with `dry_run=false`. A refusal names the cause; fix the cause. `undo_last_change` reverses the last save.
- Group props with purpose; vary neighbouring modules; leave open space.

## 5. Check
- `check_collisions_and_clearance`, `check_locked_constraints`, then `validate_composition`. Errors first, warnings next. Change one thing at a time.
- `render_scene_map` is a diagnostic, not a judgment.

## 6. Export and look
- `export_scene(dry_run=true)`, `simulate_scene`, then `export_scene(dry_run=false, target_path=...)`. Forward the Luau to Studio's `execute_luau` (`datamodel_type: "Edit"`,
  `studio_id` from `list_roblox_studios`; read the schema for the code argument). `parse_studio_report`; quote errors verbatim. `inspect_placed` (script, then compare) detects drift.
- Capture the scene with Studio's `screen_capture` and look at it. `validate_composition` with `manual_scores` only for what you actually saw; leave the rest for the user.

## 7. Report and record
Report: what was placed and why, the numbers (coverage, repetition, free space), locks checked, unresolved kit metadata, assumptions, open questions, and one proposed next change.
`record_run`; `record_decision` with the user's words and a correction dimension (density, grouping, hierarchy, repetition, color, focal_point, clearance, scale, orientation, negative_space).
`promote_run` with `confirm=false`; `confirm=true` only after the user says yes.

## Hard rules
- Never publish, save or overwrite a live place; never touch instances you did not create. Never loosen the lint.
- Never move or remove locked pieces. Never place a module outside the approved kit.
- Never claim a scene exists in Studio without a Studio report (or say Studio was not used). Never present subjective scores as measurements.

References: `references/scene-format.md`, `references/kit-format.md`, `references/workflow.md`, `references/studio-mcp.md`.
