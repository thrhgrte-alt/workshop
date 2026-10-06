---
name: concept-art-workflow
description: Produces concept art for Roblox-style environments and props - proposes several meaningfully different visual directions, builds separate environment or prop prompts, generates through a user-configured image adapter, records every run, measures palette, value structure, silhouette, composition and novelty, and scores with a rubric whose subjective half is left to a person. Also validates LoRA/PEFT datasets and prepares training configs without training. Use when the user asks for concept art, mood or look-dev images, environment or prop design options, reference-based direction, or wants a style dataset checked.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "concept-art-ai" or `python -m conceptai call`. Real image generation needs a generator the user wraps with CONCEPTAI_IMAGE_COMMAND; everything else works without one. Never produces meshes, Substance graphs or Roblox scenes.
metadata:
  version: "0.1.0"
  domain: concept-art
---

# Concept art workflow

Follow in order. Detail is in `references/`; open a reference only when needed. **Every image is concept art only.**

## 1. Understand the brief
- `get_style_brief`; `find_past_corrections` with the request text (treat results as requirements).
- Decide **environment or prop** (separate workflows). Write the subject, what must stay fixed (`locked.axes`, `locked.palette`, `locked.composition.focal_box`), things to avoid, and the references.
  Ask only if a missing answer would change the directions materially.

## 2. Retrieve and extract
- `search_concept_library` with the subject kind; read the top hits **and every avoid-example**.
- If the user gave reference images (owned or licensed): `extract_reference_traits`; use its suggested fragment as a locked palette only if the user agrees.

## 3. Directions
- `propose_directions` (3-5). They must differ on >= 3 axes; if the tool warns, tell the user which locks cause it. Describe each in one line in plain words, not as axis names.

## 4. Prompts, then a dry run
- `build_prompt` for the chosen directions, then `generate_concepts(dry_run=true)`: show the prompts, the adapter and the expected image count. `list_image_adapters` says what is usable.
- No generator configured? Say so, give the prompts, and stop. Do not substitute the placeholder renderer for real output.

## 5. Generate and look
- `generate_concepts(dry_run=false, adapter=...)`. Open every output image and look at it.
- `compare_outputs` (are the directions visibly different?), `analyze_image` / `evaluate_concept` per image, `check_novelty`, and `check_composition_lock` when composition was locked.
- Treat flags as questions to check against what you see, not verdicts.

## 6. Report
Per direction: one line on what it is, what the measurements say (and their limits), what you see, and what it leaves open for a modeller (back sides, exact proportions, scale). Say clearly
that these are concept images. Offer a next step: revise a direction, regenerate with a different seed, or stop.

## 7. Feedback and curation
- `rate_concept` **only** with the user's words and a correction dimension (palette, silhouette, composition, lighting, materials, mood, readability, scale, detail_level, novelty, brief_adherence).
- `evaluate_concept` with `manual_scores` only for what the user (or you, clearly labelled as a model judgment after viewing) actually scored; the rest stays unscored.
- `promote_concept` with `confirm=false` makes a candidate; `confirm=true` only after the user accepts. Generated images are always stored as `ai_generated`.

## LoRA / PEFT (only when asked)
`lora_validate_dataset` -> fix every error with the user -> `lora_make_config` (dry run, then write) -> **the user** runs a trainer -> generate the validation plan's images through the adapter ->
`lora_check_overfit` and the rubric, then a human review. Never claim training happened here.

## Hard rules
- Never present an image as a production-ready mesh, graph or Roblox scene. Never publish, upload or send private images anywhere.
- Never invent a provider or model; the declared model is whatever the user configured, unverified.
- Never use an image without a stated licence/owner as a reference or training item. Never present metrics or scores as taste.

References: `references/workflow.md`, `references/directions.md`, `references/rubric.md`, `references/adapters.md`, `references/lora-dataset.md`.
