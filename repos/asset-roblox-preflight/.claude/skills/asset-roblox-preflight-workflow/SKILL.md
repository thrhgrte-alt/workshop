---
name: asset-roblox-preflight-workflow
description: Checks a Blender export (GLB, glTF, OBJ, or FBX with a hub summary) against a Roblox-oriented profile before upload and reports problems with measured value, limit and a one-line fix - mesh, transforms, names, UVs, textures, rig including the one-bone spin setup, collision and upload risk. Use when the user asks to preflight, validate, lint or check an export, asks whether an asset is ready to upload, wants a Blender fix script, or says a rule may be broken for an asset type.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "asset-roblox-preflight" or `python -m preflight call`. Never connects to Blender, Studio or Roblox; the hub (hub__*) runs generated Blender scripts and roblox_upload_plan.
metadata:
  version: "0.1.0"
  domain: asset-preflight
---

# Asset preflight workflow

Follow in order. Detail is in `references/`; open one only when needed.

## 1. Resolve scope
- `list_projects`; every tool needs a `project_id`. If the user did not say which game, ask. Never guess. `place_id` is optional (recorded for the upload hand-off).
- Profile: `prop`, `tool`, `character_accessory` or `terrain_piece`. Ask if unclear.
- `get_style_brief` and `find_past_corrections` with the request text (treat corrections as requirements).

## 2. Look before judging
- `inspect_export`: what is in the file and what cannot be measured. FBX is header-only: ask for a GLB or a hub summary (`references/summary-schema.md`).

## 3. Check what was asked, no more
- Narrow request: `check_mesh`, `check_uvs`, `check_textures`, `check_rig`, `check_names`. "Is it ready": `preflight_report`.
- Read the one-line summary, then findings top-down (errors first). Use `explain_finding` for a rule's explanation, effective limits or one finding's details.
- Say every time: limits are DEFAULTS to verify, not official Roblox limits; list what could not be checked.

## 4. Fix
- `apply_safe_fix` (dry run first). It emits a Blender Python script for `safe: true` fixes only (renames, apply scale/rotation). Hand it to the hub to run on a COPY of the .blend; re-export; `preflight_report` again; `compare_versions`.
- Everything else is a manual fix: give the finding's one-line fix. Never edit the export yourself.

## 5. Overrides and learning
- User says a rule may be broken ("this prop may be 30k tris"): `record_override` (dry run, then save) with their words, scoped to the project and asset type. Global only if they say it applies to every game.
- After repeats the tools offer promotion into the project's profile; show the patch, apply only with a yes, then rerun the evals.
- `record_run`, `record_decision` with the user's words; `promote_run` with `confirm=false` unless told otherwise.

## 6. Hand off
- `ready_to_upload` is true only when no errors remain on the full report. Then hand the file to `roblox_upload_plan` through the hub. **Never publish or upload from here**, and never say Roblox will accept it.

## Hard rules
- No guessing a project; no widening scope; no claiming FBX geometry was checked; no editing a file in place; no presenting default limits as official.

References: `references/summary-schema.md`, `references/rules-and-profiles.md`, `references/spin-setup.md`.
