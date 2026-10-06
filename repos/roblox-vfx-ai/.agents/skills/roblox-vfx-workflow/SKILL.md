---
name: roblox-vfx-workflow
description: Creates, checks and refines Roblox Studio visual effects (particle bursts, auras, beams, trails, glows) in the user's style using retrieved reference effects, validated effect recipes, generated Luau, budget and readability estimates, previews, and recorded feedback. Use when the user asks for a Roblox effect, spell, hit, aura, beam, trail or VFX, wants an effect changed (bigger, brighter, cheaper, clearer), or wants an existing effect checked for performance or readability.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "roblox-vfx-ai" or `python -m rbxvfx call`. Running the generated Luau needs Roblox Studio with its built-in MCP server enabled and connected to the same agent; everything else works without Studio. Optional simulation needs the lupa package.
metadata:
  version: "0.1.0"
  domain: roblox-vfx
---

# Roblox VFX workflow

Follow in order. Detail is in `references/`; open a reference only when needed.

## 1. Orient
- `get_style_brief` (focus on 1-3 words from the request: `timing`, `colour`, `glow`).
- `find_past_corrections` with the request text. Treat results as requirements.
- Ask which **platform** matters (default `mobile`, the strictest) and the typical **gameplay distance** if the
  user has not said; defaults are 30 studs.

## 2. Retrieve
- `search_effect_library` (use `effect_kind`: burst, loop, beam, trail, aura...). Read the top 2-3 hits and **all
  returned avoid examples**; say which traits you are borrowing and which you are avoiding.
- `list_effect_recipes`. Pick the closest recipe. If none fits, say so; propose a new recipe YAML plus an eval task
  instead of inventing instances or properties.

## 3. Plan with parameters
Translate words into parameter values with `references/parameter-guide.md`. Change one or two parameters at a
time. Colours come from the style palette unless the brief says otherwise. Textures are the user's own
`rbxassetid://` ids; never invent an id.

## 4. Check before touching Studio
- `create_effect_from_recipe(dry_run=true)` (rejects invalid parameters, targets and trees).
- `check_performance_budget` and `evaluate_effect` for the chosen platform. Fix every error; explain warnings.
- `simulate_effect` to run the generated Luau on the mock DataModel (catches wrong property names/types).

## 5. Build in Studio
- `create_effect_from_recipe(dry_run=false)` returns the Luau and a Studio handoff.
- Send the Luau to Studio's MCP server tool `execute_luau` with `datamodel_type: "Edit"` and the `studio_id` from
  `list_roblox_studios`. Read the tool schema for the code argument name. Then `parse_studio_report` on the result;
  on errors, quote them verbatim and call `get_console_output`.
- Use `target_path` for a Folder you and the user agreed on. The script never touches anything it did not create.

## 6. Look
- `preview_effect(dry_run=false)`: run its Luau (bursts fire once), then capture each returned camera with Studio's
  `screen_capture`. Trails need a moving part; say so. Optionally `start_stop_play` for a playtest.
- Look at the captures yourself. `evaluate_effect` with `manual_scores` only for things you actually saw; leave
  `reads_in_motion` and `matches_style` unscored until the user has judged.
- `inspect_vfx` can read back what exists in Studio and validate it with the same checker.

## 7. Report and record
Report references used, recipe and parameters (before -> after), estimated cost vs budget (mark as estimates),
findings, what you could not verify, and the capture paths. Offer one concrete next adjustment.
`record_run`; after the user responds `record_decision` with their words and corrections (dimension from the style:
timing, size, colour, glow, density, silhouette, performance, readability, layering). Offer `promote_run`
(`confirm=false`), and set `confirm=true` only after the user says yes.

## Hard rules
- Never publish, save or overwrite a live place from generated code; never modify instances you did not create.
- Never invent classes, properties, enum members, or asset ids. Never loosen `luau.lint`.
- Never present estimates as measurements, or an unreviewed result as accepted.

References: `references/workflow.md`, `references/parameter-guide.md`, `references/studio-mcp.md`, `references/readability-and-budget.md`.
