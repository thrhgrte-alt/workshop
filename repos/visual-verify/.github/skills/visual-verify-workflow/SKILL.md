---
name: visual-verify-workflow
description: Measures renders, screenshots and textures with Pillow and NumPy instead of judging them by eye - silhouette IoU and offsets against a reference, dominant colours and palette distance, value structure, edge density, tiling seams and repetition, PBR channel ranges and normal-map length, before/after differences - and reports every number with the limit used and whether it passed. Use when the user asks whether an image matches a reference, a palette or a profile, whether a texture tiles or its PBR maps are in range, or what changed between two images, and when saving what they accept or reject so thresholds can be tuned to their taste.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .) and guide-core. Tools come from the MCP server "visual-verify" or `python -m visualverify call`. Screenshots from Studio come from the hub's screen_capture saved to disk; nothing here connects to Studio or Blender, and no image is ever uploaded.
metadata:
  version: "0.1.0"
  domain: visual-verify
---

# Visual verify workflow

Follow in order. Detail is in `references/`; open one only when needed.

## 1. Scope first
- Ask which **project** (and place) the image belongs to if you do not know: every tool needs `project_id` from `projects.yaml` and refuses without it. Never guess.
- `get_style_brief` (the thresholds in force; they are PLACEHOLDERS until the user's profile or decisions replace them) and `find_past_corrections` with the request text: what this user already accepted or rejected counts as requirements.

## 2. Get the image on disk
A PNG/JPEG/WebP/BMP/TGA/TIFF inside a folder the project may read (`projects.yaml` `image_roots`). A Studio screenshot: run the hub's `screen_capture`, save the full result as `samples/hub/screen_capture.<label>.json`,
then `ingest_capture` (dry run, then `dry_run=false`) and use the `image_path` it returns. That parser is `schema_unverified`: if it refuses, report the key names it lists.

## 3. Measure
- One image: `measure_image` (add `profile` or `target_palette` for a target). Against a reference: `compare_to_reference`, or `silhouette_iou` / `palette_distance` for one aspect.
- A tile: `check_tiling` (seam ratio and repetition, at full resolution). PBR maps: `check_pbr_ranges` with whichever of albedo, roughness, metalness, normal you have.
- Before and after: `diff_images` for numbers, `render_diff_image` (dry run first) for a picture.
- Read the one-line `summary`, then `findings` (failed checks, ranked, each with value, operator and limit), then `passed`, `skipped` (could not be measured) and `warnings` (mask or alpha caveats).

## 4. Report
Give the numbers WITH their limits and pass or fail. Say what was NOT measured (`not_measured`: taste, anatomy, perspective, and the tool's own list). Never say an image looks good, looks right or is fine.
A mask warning means the silhouette is unreliable: say so. A `skipped` check is not a pass. If `learned_thresholds` appears, say a limit is the user's own learned value, not the default.

## 5. Teach it
`save_target_profile` from a reference the user approves (dry run first; numbers only, the image is not copied). `record_run` with `measure={"tool": ..., "args": {...}}` (it re-measures, so nothing is retyped), then
`record_decision` with the user's words and a correction dimension (silhouette, palette, value, edges, tiling, texture, diff, other). Accepting a result that failed a check, or rejecting one that passed, is evidence the limit is
off. You never change a threshold: the user runs `learn propose`, `learn gate`, then `learn promote --approved-by NAME --confirm` (and can `learn rollback`).

## Hard rules
- Never upload, paste or copy a user's image; never read outside the allowed folders; never connect to Studio or Blender from here; never publish.
- Same input gives the same output: if a number changed, the image or a threshold changed.
