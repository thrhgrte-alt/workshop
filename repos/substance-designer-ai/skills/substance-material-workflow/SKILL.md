---
name: substance-material-workflow
description: Builds and revises Substance 3D Designer materials and node graphs in the user's own style, using retrieved reference examples, validated graph recipes, generated Designer scripts, rendered or exported previews, and a measured rubric. Use when the user asks for a material, texture, tileable surface, .sbs graph, or asks to change the look of an existing material (chunkier, softer bevels, cooler palette, less noise).
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "substance-designer-ai" or from `python -m sdai call`. Running generated scripts needs Substance 3D Designer; search, validation, script generation, map measurement and feedback work without it.
metadata:
  version: "0.1.0"
  domain: substance-designer
---

# Substance material workflow

Follow these steps in order. Each step names the tool to call. Detail lives in `references/`; open a
reference only when you need it.

## 1. Orient (always)
- Call `inspect_environment`. Note whether the node catalog has been **verified on this machine**
  (`catalog.probe_clean`). If it has not, say so to the user once and continue; every build is then
  provisional until the probe has been run inside Designer (see `references/designer-api-notes.md`).
- Call `get_style_brief` with 1-3 focus words from the request (for example `palette`, `bevel`, `detail`).

## 2. Recall what the user already told us
- Call `find_past_corrections` with the request text. Treat returned corrections as requirements.
- Call `search_material_library` (use `material_type`, `tags`, `palette` when the request implies them).
  Read the top 2-4 positive hits and **all returned negative ("avoid") hits**. Note *why* each was retrieved.

## 3. Plan
- Call `list_recipes`. Choose the closest recipe. Translate the user's words into parameter values using
  `references/parameter-guide.md`. Stay inside the declared ranges.
- If no recipe fits, stop and tell the user. Propose a new recipe YAML (nodes from the catalog only) and an
  eval task. Do not improvise node graphs or invent node/parameter ids.

## 4. Validate, then build
- `validate_graph_spec` (fix every error; mention warnings, especially `unverified_node`).
- `create_graph_from_recipe` with `dry_run=true`, show the plan summary, then again with `dry_run=false`.
- Run the written script **inside Designer** (you cannot do this from the tools). If you cannot run it,
  give the user the script path and result path and wait. Then call `read_build_result`. Quote errors verbatim.

## 5. Look and measure
- Get previews: `render_preview` (dry run first) or ask the user to export maps from Designer.
- Call `compare_material_to_rubric` with the exported maps. Look at the preview yourself. Give manual
  scores only for what you actually saw; leave the rest unscored. Never report a score as objective truth.

## 6. Report
Say: which references informed the result, the recipe and parameters, measured findings (errors first),
what you could not verify, and the preview path. Offer one concrete next adjustment.

## 7. Record
- `record_run` with the request, retrieved ids, recipe, outputs and preview.
- After the user responds, `record_decision` with their words and structured corrections
  (`dimension` from `style/style.yaml`, for example `bevel_roundness`).
- If they accept and want it kept as a reference, call `promote_run` with `confirm=false`, then ask before
  repeating with `confirm=true`.

## Hard rules
- Never invent node ids, parameter ids, or API calls. Never write outside the allowed directories.
- Never claim success without a build result (`status: ok`) and a preview or measurements.
- Never promote an unreviewed output into the style library.
- Keep context small: summarise hits instead of pasting them.

References: `references/workflow.md` (worked example), `references/parameter-guide.md`,
`references/designer-api-notes.md`, `references/rubric-guide.md`.
