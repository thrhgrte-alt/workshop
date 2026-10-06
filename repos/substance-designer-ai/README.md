# substance-designer-ai

A style-aware material and node-graph assistant for **Substance 3D Designer**, built so that a capable AI
agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real work with your references,
your recipes and your feedback.

**What it is:** instructions + a searchable reference library + validated graph recipes + script generation for
Designer + map measurement + a feedback loop + an eval suite, exposed as an MCP server and a CLI.

**What it is not:** it does not train or change any model. The model reasons over what is placed in its
context (your style brief, the few references retrieved, tool results). Improvement comes from *data you
curate* (references, corrections, recipes, eval tasks), not from weights.

> **Honest status.** This repository was built without Substance 3D Designer installed. Everything that does
> not need Designer is implemented and tested (see [Capability matrix](#capability-matrix)). The Designer-facing
> scripts and the node catalog follow Adobe's documented API shape but are **unverified** until you run the
> included probe inside your Designer ([Verify on your install](#verify-on-your-install)).

## Capability matrix

| Capability | Without Designer | Needs Designer / extra | Status |
|---|---|---|---|
| Search references (filters, tags, text, palette, optional embeddings) | yes | CLIP-style image search needs `.[embeddings]` | tested |
| Style brief, past corrections, curation, feedback log | yes | | tested |
| Recipe validation (nodes, slots, types, ranges, cycles, outputs) | yes | | tested |
| Compile recipe to plan + Designer build script | yes | | tested (syntax + fake Designer) |
| Run the script / build the graph | no | Designer runs the script | **unverified** |
| Inspect the open graph | script only | Designer runs the script | **unverified** |
| Summarise `.sbs` files (nodes used, library instances, outputs) | yes | | tested on a hand-written fixture |
| Render previews | dry-run command only | `sbscooker` + `sbsrender` from Adobe | **unverified** (stub-tested orchestration) |
| Measure exported maps (tileability, palette, contrast, detail, normals) | yes (numpy + Pillow) | | tested on synthetic maps |
| Rubric scoring with explicit manual/subjective criteria | yes | someone has to look at the preview | tested |
| Eval suite + baseline comparison + checking an agent's saved results | yes | | 37 tasks |
| MCP server (stdio) | yes | an MCP-capable client | tested with the SDK's in-memory client |

## Quick start

```bash
git clone <this repo> && cd substance-designer-ai
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+
python -m sdai doctor                                  # capability report for this machine
python -m sdai search "chunky stone wall rounded bevels" --kind material
python -m sdai build stylized_stone_wall --param bevel_softness=3.5           # dry run: shows the plan
python -m sdai build stylized_stone_wall --name castle_wall --apply           # writes the Designer script
python -m sdai eval run --label mine && python -m sdai eval compare baseline mine
python -m pytest
```

Nothing is written outside `workspace/` unless you set `SDAI_ALLOWED_PATHS`.

## How it works

```
 request -> style brief + past corrections + retrieved references   (few, relevant, with reasons)
         -> pick a recipe, set validated parameters                  (model judgment)
         -> compile + validate -> Designer script                    (deterministic code)
         -> run INSIDE Designer -> result JSON                       (you or an agent with app access)
         -> export / render maps -> measure + rubric                 (deterministic metrics + explicit manual scores)
         -> show preview -> your verdict -> structured feedback      (stored, retrievable)
```

The model interprets intent and chooses; code does exact work (parameter ranges, connection types, graph
order, file handling). Recipes constrain the model to graphs that are known to be valid, which is why a recipe
plus parameters beats asking a model to place nodes freely.

## Your library

Tracked `examples/` are **synthetic placeholders** that make the repo runnable. Your real library is private:

```bash
export SDAI_ASSET_ROOT=/path/to/your/substance/projects     # files stay where they are; nothing is uploaded
python -m sdai ingest "$SDAI_ASSET_ROOT" --kind material --owner user --tag stylized          # dry run
python -m sdai ingest "$SDAI_ASSET_ROOT" --kind material --owner user --tag stylized --apply  # writes CANDIDATES
python -m sdai summarize-sbs path/to/wall.sbs        # which nodes/library graphs it uses
```

Ingest creates **candidates** only. Candidates are never returned by default searches. To curate: edit
`workspace/library/assets.jsonl` (or promote a reviewed run), add a description, tags, palette, shape language and
`"status": "curated"`. Negative examples ("avoid this") use `"polarity": "negative"` with `correction_notes`.
Keep `.sbs` files (not just renders): `summarize-sbs` records *how* a material was built so recipes can be derived from it.
`python -m sdai validate-library --check-files` checks everything. Manifest schema: `library/manifest.schema.json`.

Optional image/text embeddings (local, replaceable): `python -m sdai build-index --embedder hashing-512` (lexical, no
dependencies) or `--embedder st:clip-ViT-B-32` with `pip install -e ".[embeddings]"` (CLIP-style: text-to-image and
image-to-image similarity over your previews; untested here, large download).

## Style

`style/style.yaml` (machine-readable) and `style/STYLE.md` (human-editable) describe traits, numeric ranges, rules
and exclusions. **The shipped content is a starter template**, calibrated only on synthetic maps. Replace it with
measurements of your own approved materials (STYLE.md explains how). Ranges are smoke alarms, not judges.

## Tools (MCP and `python -m sdai call <tool> --json '{...}'`)

Read-only tools are annotated `readOnlyHint`. Tools that write default to `dry_run=true`.

| Tool | Writes? | Purpose |
|---|---|---|
| `inspect_environment` | no | CLI tools found, whether the catalog is verified, recipes available |
| `get_style_brief` | no | Compact style spec; focus words list relevant traits first |
| `search_library` | no | Generic hybrid search over any asset kind |
| `search_material_library` | no | Material search with `material_type`, tags, palette; returns recipe ids and avoid-examples |
| `find_past_corrections` | no | The user's earlier corrections relevant to this request |
| `list_recipes` | no | Recipes, parameters, ranges, defaults |
| `validate_graph_spec` | no | Findings (error/warning) for a recipe + parameters |
| `create_graph_from_recipe` | script + plan version (when `dry_run=false`) | Compile a recipe to a Designer build script |
| `set_validated_parameters` | script + plan version (when `dry_run=false`) | Change parameters of a created graph with diff and re-validation |
| `save_graph_version` | snapshot version (when `dry_run=false`) | Version history; store a live-graph snapshot |
| `read_build_result` | no | Read what a script wrote (status, errors) |
| `inspect_active_graph` | no | Script to snapshot the open graph, or summary of a snapshot |
| `summarize_sbs` | no | Nodes, library instances and outputs of a `.sbs` |
| `render_preview` | files (when `dry_run=false`) | `sbscooker` + `sbsrender` previews; expensive |
| `compare_material_to_rubric` | no | Measure exported maps; score rubric with explicit manual scores |
| `catalog_diff` | no | Compare the draft catalog to a Designer probe result |
| `probe_script` | no | The probe script to run inside Designer |
| `record_run` | feedback file | Save request, retrieved ids, recipe, outputs, preview |
| `record_decision` | feedback file | Save the user's verdict and structured corrections |
| `promote_run` | library file | Add a reviewed run to the library (`confirm=true` to make it curated) |

## Connecting agents

Canonical instructions: `AGENTS.md` and `skills/substance-material-workflow/SKILL.md`. `scripts/sync_agent_files.py`
copies them to the places different clients look (CI checks they have not drifted).

| Client | Reads instructions from | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (no skill support assumed; instructions in `GEMINI.md`) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

This table records where each vendor documents its files **as understood when this was written**; client
behaviour changes, and none of these integrations were tested against live clients. A client that cannot load
skills can still follow `AGENTS.md`, or call `python -m sdai call`. The server runs **locally over stdio**; it has
no authentication and refuses to bind a non-loopback address.

## How it improves (and what that does not mean)

1. **Retrieval:** curated examples with reasons, including *avoid* examples.
2. **Corrections:** `record_decision` stores structured corrections; `find_past_corrections` returns them for similar requests.
3. **Curation:** accepted results can be promoted into the library only through an explicit confirm step.
4. **Recipes and style:** recurring corrections become recipe changes or style-range changes made by a person or agent.
5. **Evals:** every lesson becomes a task in `evals/tasks/`; `eval compare` shows regressions between two runs.
   Agents can be measured with the same checks: `python -m sdai eval check-results <dir> --label agentA`.

No step changes model weights. Fine-tuning is a separate, model-specific decision; for visual style it is usually an
image model (see the concept-art repo of this suite), not the language model that plans the work.

## Verify on your install

1. `python -m sdai probe-script > probe_designer.py`; run it inside Designer (it writes a JSON result).
2. `python -m sdai catalog-diff <result.json>`; fix `recipes/node_catalog.yaml` until the diff is clean.
3. `python -m sdai build stylized_stone_wall --name probe_test --apply`; run the script in Designer;
   `read_build_result`. Errors come back as readable messages with a traceback.
4. Export maps and run `python -m sdai measure --maps baseColor=... normal=... roughness=... height=...`.
5. Recalibrate `style/style.yaml` ranges from your approved materials.

## Limitations

- No live connection to Designer: scripts are generated, then run inside Designer.
- Node ids, parameter ids, enum values and several Designer API calls are **unverified** (listed in
  `skills/.../references/designer-api-notes.md`). Output *usage* tags are not assigned by the script.
- `render_preview` uses Adobe's `sbscooker`/`sbsrender`; flags were not executed here.
- Recipes cover three starter materials. New looks need new recipes.
- Metrics are measurements, not taste. Subjective criteria require a human (or a clearly labelled model judgment).
- `.sbs` parsing is tolerant but was written without a large file corpus.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: ... not in the catalog` | Use only nodes in `recipes/node_catalog.yaml`; propose a catalog entry plus probe verification |
| Script result says `no input property 'x'` | Run the probe + `catalog-diff`; fix the catalog id |
| `library graph 'Tile Generator' not found` | Load Designer's library packages, or fix the node `label` in the catalog |
| `outside the allowed directories` | Set `SDAI_ALLOWED_PATHS` to include the folder, deliberately |
| Search returns nothing | Entries must be `status: curated`; ingest creates candidates. Try `--candidates` to see them |
| `Image analysis needs numpy and Pillow` | `pip install -e ".[imaging]"` |
| `sbscooker not found` | Install Adobe's tools or export maps from Designer and use `measure` |
| MCP client shows no tools | Use the interpreter where the package is installed; set `SDAI_ROOT` to this checkout |

## Repository map

`sdai/` code (`core/` is vendored shared code; `domain/` is Designer-specific) | `recipes/` catalog + recipes |
`style/` | `library/` schema | `examples/` synthetic positive/negative examples with explanations | `evals/` tasks,
rubric, fixtures, versioned reports | `feedback/` schema and notes | `skills/` canonical skill | `adapters/` MCP client
configs | `references/` official links | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your assets.
