# concept-art-ai

A concept-art assistant for **Roblox-style environments and props**. It turns a brief (and licensed reference images you point it at) into several *meaningfully different*
visual directions, builds separate environment and prop prompts, calls an image generator **you** configure through a replaceable adapter, records every run (model, prompt,
references, settings, seeds, outputs), measures the pixels, scores the result with a rubric whose subjective half is left to a person, and learns only from feedback you
explicitly store and curate. An optional, separate workflow validates a LoRA/PEFT dataset and prepares a training config. Built so a capable AI agent (Claude Code, Codex CLI,
Gemini CLI, Copilot, Cursor, ...) can run the whole loop, as an MCP server and a CLI.

**A generated image is concept art only.** It is not a mesh, not a Substance graph, not a finished Roblox scene, and this repository never presents it as one: every result carries
`status: concept_only`, prompts that claim production readiness are refused, and library entries made from generated images are flagged `ai_generated` + `concept_only`.

**What it is not:** it does not train or change any model (the LoRA tools prepare and validate; they never train), it ships **no hosted image-provider integration**, and it uploads nothing.
A model reasons over what is in its context (style brief, retrieved references, tool results); improvement comes from the data you curate.

> **Honest status.** Built and tested without any image-generation model. The directions, prompts, adapters, run records, measurements, rubric, curation gate and LoRA
> validation are all exercised by tests (including a real subprocess standing in for a generator). The tracked images in `examples/` are **synthetic fixtures** drawn by a small
> renderer so the measurement code has deterministic positive and negative cases: they are not generated art and say nothing about model quality. **No real generator has been run.**
> The `command` adapter is the integration point: wrap your own generator in a small script. Pixel metrics are measurements, not taste.

## Capability matrix

| Capability | Without a generator | Needs you | Status |
|---|---|---|---|
| Directions that differ on >= 3 axes (environment and prop axes), locked axes/palette, seeded | yes | | tested; checked against weighted-Hamming distance and colour theory |
| Environment vs prop prompt structures, negatives from style + brief, production-ready claims refused | yes | | tested |
| Reference trait extraction (palette, value range, saturation, edges) | yes (Pillow/numpy) | licensed reference images | tested |
| Image adapters: `dryrun`, `placeholder` (synthetic fixture), `command` (no shell, prompt by file) | yes | `command` needs your generator script | tested incl. real subprocess, timeout, symlink refusal |
| Run records: model (declared, unverified), prompts, references, settings, seeds, outputs + sha256, feedback | yes | | tested |
| Image measurements: value structure, edges, palette adherence, silhouette, focal contrast, edge centroid | yes | | tested on constructed images |
| Novelty vs the curated library, diversity within a run, composition lock | yes | | tested; empty library = unknown, not 1.0 |
| Rubric: environment and prop; hybrid criteria take the lower of metric and person | yes | a person for the subjective criteria | tested |
| Feedback saved only when explicitly stored; curation gate before the library | yes | | tested |
| LoRA/PEFT: dataset validation, licence policy, config, validation plan, overfit check | yes | a trainer + base model you may fine-tune | tested; **never trains** |
| Actual image generation | no | your model / provider / key | **unverified** |
| CLIP-style embedding search | optional extra | `pip install -e ".[embeddings]"` | untested here |
| MCP server (stdio) | yes | an MCP-capable client | tested in-memory and over real stdio |

## Quick start

```bash
git clone <this repo> && cd concept-art-ai
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+, Pillow + numpy included
python -m conceptai doctor
python -m conceptai directions examples/briefs/lighthouse_env.yaml -n 4          # four directions, differing on >= 3 axes
python -m conceptai prompt examples/briefs/lantern_prop.yaml --index 1           # the prop prompt for direction 1
python -m conceptai analyze examples/positive/env_lighthouse_golden.png          # pixel measurements and flags
python -m conceptai eval run --label mine && python -m conceptai eval compare baseline mine
python -m pytest
```
Nothing is written outside `workspace/` unless you set `CONCEPTAI_ALLOWED_PATHS`. Images are read only from the workspace, this repo, and `CONCEPTAI_ASSET_ROOT`.

### Connect your own generator (the `command` adapter)
```bash
export CONCEPTAI_IMAGE_COMMAND='["python","my_generate.py","--prompt-file","{prompt_file}","--negative-file","{negative_file}","--out","{out}","--seed","{seed}","--width","{width}","--height","{height}"]'
export CONCEPTAI_IMAGE_MODEL="the-model-name-you-use"      # recorded as declared by you; never verified
```
Tokens: `{prompt_file} {negative_file} {out} {seed} {width} {height} {index} {reference_dir}`. The program is run without a shell, with a timeout, and must write the
`{out}` file. Prompts travel in files, never on a command line. `my_generate.py` is where a local model or a provider's SDK goes; this repository contains none.

## How it works

```
 brief (+ locks) + style brief + past corrections + retrieved references (and their avoid-examples)
   -> propose_directions: explicit axes, farthest-point sampling, >= 3 axes apart       (deterministic)
   -> build_prompt: environment OR prop structure; negatives from style + brief          (deterministic)
   -> generate_concepts (dry run first) -> adapter you choose -> files + run record      (your generator)
   -> analyze / compare_outputs / check_novelty / check_composition_lock / evaluate_concept   (pixel measurements)
   -> a person looks at the images and gives a verdict                                   (human judgment)
   -> rate_concept stores it -> promote_concept (candidate, then confirm after an accept) -> curated library
```

### Directions
Environment axes: silhouette, palette scheme, lighting, composition, materials, mood. Prop axes: silhouette, palette scheme, material focus, detail level, view, wear.
`propose_directions` samples candidates and picks greedily so each new direction is as far as possible from those already chosen (weighted by how much each axis changes the picture).
Contradictory combinations are excluded. `locked.axes` holds an axis fixed in every direction; `locked.palette` (3-8 colours) is used verbatim. When too few axes are free the tool says
so rather than pretending the directions differ.

### Environment vs prop
Different prompt structure (environment: depth layers, lighting, composition, scale cues; prop: one isolated object, silhouette that reads in solid black, material zones, views) and a
different rubric (props add `silhouette_clear`). Keep them as separate briefs.

### Reading the results
`evaluate_concept` returns the measurements, the flags against `style/style.yaml` ranges, the novelty versus the library, and the rubric. Criteria split three ways: **auto** (pixels),
**manual** (brief adherence, usability as a reference), **hybrid** (style cohesion, readability, prop silhouette: needs a metric *and* a person; the lower counts). Until the manual
scores are supplied the rubric is incomplete and `passed` is false.

## Your references and library

The tracked `examples/` are **synthetic fixtures**. Your library is private:

```bash
export CONCEPTAI_ASSET_ROOT=/path/to/your/licensed/references       # files stay where they are; nothing is uploaded
python -m conceptai ingest "$CONCEPTAI_ASSET_ROOT" --kind reference_image --owner user --tag coast          # dry run
python -m conceptai ingest "$CONCEPTAI_ASSET_ROOT" --kind reference_image --owner user --tag coast --apply  # writes CANDIDATES
```
Candidates are never returned by default; curate by editing `workspace/library/assets.jsonl` (description, tags, `domain.subject_kind`, `"status": "curated"`; negatives use
`"polarity": "negative"` with `correction_notes`). `python -m conceptai validate-library --check-files` checks everything.

## Optional: LoRA / PEFT (separate workflow)

`dataset.yaml` + images (format in `skills/concept-art-workflow/references/lora-dataset.md`) -> `lora_validate_dataset` (licences, owners, captions, trigger word, resolution, byte and
near duplicates, AI-generated share) -> `lora_make_config` (a trainer-agnostic config and a validation plan: base vs LoRA arms on held-out prompts, fixed seeds) -> **you** run your trainer ->
generate the validation images through your adapter -> `lora_check_overfit` + the rubric + a human review. This repository prints a command *template* and never runs one. Hyperparameters
are generic starting points, marked unverified. Policy: `style/lora_policy.yaml`.

## Style

`style/style.yaml` + `style/STYLE.md`: traits, metric ranges, constraints, exclusions, correction dimensions. **A starter template**; tune the ranges against concepts you consider good.

## Tools (MCP and `python -m conceptai call <tool> --json '{...}'`)

Read-only tools carry `readOnlyHint`; writing tools default to `dry_run=true`.

| Tool | Writes? | Purpose |
|---|---|---|
| `get_style_brief` | no | Compact style spec |
| `find_past_corrections` | no | Your earlier corrections relevant to this request |
| `search_library` | no | Generic hybrid search of the reference library (any kind) |
| `search_concept_library` | no | Concept images, references, directions by text/tags/palette, filtered by environment or prop; returns avoid-examples |
| `analyze_image` | no | Pixel measurements: value range/structure, edges, palette, saturation, silhouette stats, optional palette adherence and focal contrast |
| `extract_reference_traits` | no | Merged palette and trait hints from 1-12 reference images (pixels only); suggests a brief fragment |
| `propose_directions` | no | Several meaningfully different directions (farthest-point sampling over explicit axes); locked axes/palette respected; seeded |
| `build_prompt` | no | Prompt + negative prompt for one direction; environment and prop structures differ; refuses production-ready claims |
| `list_image_adapters` | no | Adapters (`dryrun`, `placeholder`, `command`) and whether each is usable here |
| `check_novelty` | no | 1 - similarity to the nearest curated library image (null when the library is empty) |
| `check_composition_lock` | no | A locked focal box must stand out and edge energy must centre near it |
| `compare_outputs` | no | Pairwise dissimilarity of one run's outputs: catches 'different' directions that look alike |
| `evaluate_concept` | no | Measurements + environment/prop rubric; manual criteria stay unscored until a person supplies them |
| `get_generation` | no | Full record of one run: brief, adapter and declared model, prompts, settings, seeds, references, outputs with hashes, feedback |
| `list_generations` | no | Recent runs |
| `lora_validate_dataset` | no | Licences, owners, captions, trigger, resolution, duplicates, AI share of a LoRA dataset folder |
| `lora_check_overfit` | no | Flag outputs nearly identical to a training image |
| `generate_concepts` | run record + images when `dry_run=false` | Generate through the chosen adapter and record everything; `dry_run=true` shows the prompts only |
| `rate_concept` | generation record + feedback log when `dry_run=false` | Store the USER's verdict and structured corrections |
| `promote_concept` | library file when `dry_run=false` | Curation step: candidate first; `confirm=true` needs a prior accept; flags `ai_generated` |
| `lora_make_config` | config + plan files when `dry_run=false` | Trainer-agnostic config and validation plan for a dataset that passes validation. **Never trains.** |
| `record_run` | feedback file | Save request, retrieved ids, outputs (generic) |
| `record_decision` | feedback file | Save a verdict (generic; `rate_concept` is the concept-art route) |
| `promote_run` | library file | Generic promotion (`promote_concept` adds the AI-output safeguards) |

## Connecting agents

Canonical: `AGENTS.md` and `skills/concept-art-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (CI checks drift).

| Client | Instructions | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

Where each vendor documents its files **as understood when written**; none tested against live clients. The server is local, stdio, unauthenticated and refuses non-loopback binds.
Set `CONCEPTAI_IMAGE_COMMAND` in the client's MCP `env` block if you want the agent to be able to generate.

## How it improves (and what that does not mean)

Retrieval of curated references with avoid-examples -> `rate_concept` corrections recalled by `find_past_corrections` -> explicit curation (`promote_concept`, `confirm` only after an accept) ->
recurring corrections turned into style-range or axis changes -> eval tasks that lock lessons in (`eval compare`, and `eval check-results <dir>` to score an agent's saved outputs).
No step changes model weights. A LoRA, if you train one, changes *that LoRA's* weights, outside this repository, from a dataset you validated.

## Limitations

- No real generator has been run. Everything about output quality is untested; the placeholder renderer is a fixture, not a model.
- Pixel metrics: palette adherence compares dominant colours (k-means) with the direction palette; readability is a heuristic from value range, value spread and edge density; silhouette
  stats come from an Otsu threshold and are meaningful mainly for props on plain backgrounds; novelty/similarity use a 16x16 luminance layout plus palette. None of them judge taste, anatomy,
  perspective or whether the forms are buildable.
- Directions vary the axes listed; they cannot invent a new axis. Distances use hand-set axis weights.
- Environment concepts do not give a modeller proportions or hidden sides; say so in the report.
- The LoRA validator checks files and statements, not truth; it cannot see whether an image is actually licensed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `command adapter not configured` | Set `CONCEPTAI_IMAGE_COMMAND` (see above); check with `list_image_adapters` |
| `image command did not write` | Your script must write the `{out}` path (PNG/JPEG/WEBP) |
| `outside the allowed directories` | Put images under `workspace/`, this repo, or `CONCEPTAI_ASSET_ROOT` |
| `a locked palette needs 3-8 colours` | Give at least three `#rrggbb` colours, or lock axes instead |
| `only N axes are unlocked` | You locked most axes; unlock some to get directions that differ |
| `prompt must not claim production readiness` | Remove "game-ready/production-ready/final mesh" from notes: it is concept art |
| `placeholder images are synthetic fixtures` | They are never promoted; use a real generator |
| Rubric `complete: false` | Subjective criteria need `manual_scores` from a person |
| Search returns nothing | Entries must be `status: curated` (`--candidates` shows candidates) |

## Repository map

`conceptai/` (`core/` vendored; `domain/`: `direction`, `prompts`, `adapters`, `runs`, `analysis`, `lora`, `synthetic`, `schema`) | `style/` | `library/` schema | `examples/` briefs, synthetic fixtures,
manifest | `evals/` (tasks, rubrics, baseline) | `feedback/` | `skills/` | `adapters/` | `references/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for images, generated output and training data.
