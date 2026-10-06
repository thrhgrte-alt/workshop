# roblox-vfx-ai

A style-aware assistant for creating, checking and refining **Roblox Studio visual effects** (ParticleEmitters, Beams,
Trails, lights), built so that a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real
work with your references, recipes and feedback.

**What it is:** instructions + a searchable reference library + validated effect recipes + safe Luau generation + budget
and readability estimates + a Luau mock for dry runs + a feedback loop + an eval suite, exposed as an MCP server and a CLI.

**What it is not:** it does not train or change any model. A model reasons over what is placed in its context (the style
brief, the few references retrieved, tool results). Improvement comes from data you curate (references, corrections,
recipes, eval tasks), not from weights. It also does **not connect to Roblox Studio itself**: it generates and validates
Luau that an agent forwards to Studio's own built-in MCP server.

> **Honest status.** Built without Roblox Studio installed. Recipes are validated against an API snapshot parsed from
> Roblox's public documentation repository, and generated Luau is executed on a mock DataModel in tests, but **nothing
> here has been run inside real Studio**. Budget and readability numbers are heuristic estimates.

## Capability matrix

| Capability | Without Studio | Needs Studio / extra | Status |
|---|---|---|---|
| Search references (filters, tags, text, palette, optional embeddings) | yes | CLIP-style search needs `.[embeddings]` | tested |
| Style brief, past corrections, curation, feedback log | yes | | tested |
| Recipe -> typed plan, validated against the Roblox API snapshot | yes | | tested (70 eval tasks) |
| Luau generation + safety lint + injection-safe paths | yes | | tested |
| Run generated Luau on a mock DataModel (`simulate_effect`) | yes (needs `lupa`) | | tested; mock rejects bad properties/types like Studio |
| Inspect an effect read back from Studio and validate it | yes (given the JSON) | Studio runs the inspect script | round-trip tested on the mock |
| Budget / readability / timing / theme estimates | yes | | tested; thresholds are heuristics |
| Offline timeline sheets (size/transparency/colour curves) | yes (Pillow) | | tested |
| Create the effect in Studio, capture viewport, playtest | no | Studio + its built-in MCP server | **unverified** |
| Real-device performance | no | profile on device | not covered |
| MCP server (stdio) | yes | an MCP-capable client | tested with the SDK in-memory client + real stdio subprocess |

## Quick start

```bash
git clone <this repo> && cd roblox-vfx-ai
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+; dev includes numpy, Pillow, lupa
python -m rbxvfx doctor                                # capability report
python -m rbxvfx search "purple magic burst" --kind effect
python -m rbxvfx analyze arcane_burst --platform mobile
python -m rbxvfx build arcane_burst --param size=1.5 --target workspace.Effects    # prints the Luau
python -m rbxvfx eval run --label mine && python -m rbxvfx eval compare baseline mine
python -m pytest
```

Nothing is written outside `workspace/` unless you set `RBXVFX_ALLOWED_PATHS`.

## How it works

```
 request -> style brief + past corrections + retrieved references    (few, relevant, with reasons)
         -> pick recipe + validated parameters                       (model judgment)
         -> build plan -> validate against API snapshot              (deterministic)
         -> budget / readability / timing estimates; mock run        (deterministic)
         -> Luau -> Studio's execute_luau (agent forwards it)        (Studio runs it)
         -> screen_capture at returned cameras -> user verdict       (human judgment, recorded)
```

### Connecting to Roblox Studio

Enable Studio's own MCP server (**Assistant > ... > Manage MCP Servers > Enable Studio as MCP server**, per Roblox's
documentation) and connect the same agent to it **in addition to** this server. Studio's tools used here are
`list_roblox_studios`, `execute_luau` (Edit datamodel), `screen_capture`, `get_console_output`, `start_stop_play`,
`inspect_instance`. This repository's tools return a `studio` hand-off describing what to call; read Studio's tool schema
for the exact argument names. Studio's MCP can read and modify your open place: work on a copy and keep effects inside
an agreed Folder (`target_path`).

### Safety

Generated Luau creates one root named `AIEffect_<name>`, tags it, and on re-run replaces **only** a root carrying the
attribute `AIGeneratedBy = "rbxvfx"`. If something else has that name it stops with an error. It never publishes, saves, or
makes network requests (lint-enforced and tested); `target_path` is restricted to a dotted path (no string breakout);
all strings are ASCII; only five classes can be created. Dry runs reject exactly what real runs reject.

## Your library

Tracked `examples/` are **synthetic**: timeline sheets generated from the recipes' own curves. Your real library is private:

```bash
export RBXVFX_ASSET_ROOT=/path/to/your/effect/references     # files stay where they are; nothing is uploaded
python -m rbxvfx ingest "$RBXVFX_ASSET_ROOT" --kind reference_clip --owner user --tag fire    # dry run
python -m rbxvfx ingest "$RBXVFX_ASSET_ROOT" --kind reference_clip --owner user --tag fire --apply
```

Ingest creates **candidates** only; they are never returned by default searches. Curate by editing
`workspace/library/assets.jsonl`: add a description, tags, palette, traits and `"status": "curated"`; negative examples use
`"polarity": "negative"` with `correction_notes`. Record the recipe that produced each effect (`domain.recipe`) so the *how*
is retrievable. `python -m rbxvfx validate-library --check-files` checks everything; schema in `library/manifest.schema.json`.
Optional embeddings: `python -m rbxvfx build-index --embedder hashing-512` (local, lexical) or `--embedder st:clip-ViT-B-32`
with `pip install -e ".[embeddings]"` (CLIP-style; untested here, large download).

## Style

`style/style.yaml` (machine-readable) + `style/STYLE.md` (human-editable): traits, palette, test backgrounds, gameplay
distance and FOV, thresholds, per-platform budgets, rules and exclusions. **The shipped content is a starter template**;
budgets and thresholds are **heuristic defaults, not Roblox limits**. Calibrate with `python -m rbxvfx analyze <recipe>`.

## Tools (MCP and `python -m rbxvfx call <tool> --json '{...}'`)

Read-only tools carry `readOnlyHint`. Tools that write default to `dry_run=true`.

| Tool | Writes? | Purpose |
|---|---|---|
| `get_style_brief` | no | Compact style spec; focus words list relevant traits first |
| `find_past_corrections` | no | The user's earlier corrections relevant to this request |
| `search_library` | no | Generic hybrid search over any asset kind |
| `search_effect_library` | no | Effects by text, kind, tags, palette; returns recipe ids and avoid-examples |
| `list_effect_recipes` | no | Recipes, parameters, ranges, defaults, allowed classes |
| `validate_instance_tree` | no | Validate a recipe's effect, or a tree read back from Studio |
| `check_performance_budget` | no | Particle/segment/trail/light/coverage estimates vs the platform budget |
| `evaluate_effect` | no | Readability, colour separation, timing, theme, fade-out; rubric with unscored manual criteria |
| `simulate_effect` | no | Run the generated Luau on a mock DataModel (needs `lupa`) |
| `create_effect_from_recipe` | code + plan version when `dry_run=false` | Plan, then the Luau to forward to Studio |
| `preview_effect` | timeline PNG when `dry_run=false` | Preview Luau (fires bursts), camera setups, timeline sheet |
| `inspect_vfx` | no | Read-only Luau to inspect an effect; summarises + validates the returned JSON |
| `parse_studio_report` | no | Parse what a generated script returned/printed |
| `remove_effect_script` | no | Luau that removes an effect this tool created (refuses others) |
| `save_effect_variant` | version file when `dry_run=false` | Save a named parameter set for later recall |
| `record_run` | feedback file | Save request, retrieved ids, recipe, outputs, preview |
| `record_decision` | feedback file | Save the user's verdict and structured corrections |
| `promote_run` | library file | Add a reviewed run to the library (`confirm=true` to make it curated) |

## Connecting agents

Canonical instructions: `AGENTS.md` and `skills/roblox-vfx-workflow/SKILL.md`. `scripts/sync_agent_files.py` copies them to
where different clients look (CI checks for drift).

| Client | Reads instructions from | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

This table records where each vendor documents its files **as understood when this was written**; none of these integrations
were tested against live clients. The server runs **locally over stdio**, has no authentication, and refuses non-loopback
binds. Remember to add Roblox Studio's own MCP server to the same client.

## How it improves (and what that does not mean)

1. **Retrieval:** curated examples with reasons, including *avoid* examples.
2. **Corrections:** `record_decision` stores structured corrections; `find_past_corrections` returns them for similar requests.
3. **Curation:** accepted results enter the library only through an explicit confirm step.
4. **Recipes and style:** recurring corrections become recipe or threshold changes made by a person or agent.
5. **Evals:** each lesson becomes a task in `evals/tasks/`; `eval compare` shows regressions between two runs. Agents are measured
   with the same checks: `python -m rbxvfx eval check-results <dir> --label agentA`.

No step changes model weights. Fine-tuning is a separate, model-specific decision and is not part of this repository.

## Limitations

- Never run in real Studio; the mock proves coherence against the documented API, not acceptance by Studio.
- Property defaults are not in the API snapshot; the analysis lists every assumed default.
- Four recipes (burst, aura, beam, trail). Other effects (shockwaves, lightning, mesh particles, animation-driven effects) need new recipes.
- Only Attachment, ParticleEmitter, Beam, Trail and PointLight are creatable; Sound, animation and scripted gameplay logic are out of scope.
- `screen_capture` of particles in Edit mode may not show motion; trails need a moving part. Playtest for the real feel.
- Budgets are heuristics. Profile on target devices.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `has no property 'X' in the API snapshot` | The property does not exist (or the snapshot is old): `python scripts/refresh_api_snapshot.py` |
| `class_not_allowed` | Only the five effect classes are creatable; extend `ALLOWED_CLASSES` deliberately |
| `refusing to replace 'AIEffect_x'` | Something not created by this tool has that name; rename the effect or remove it yourself |
| `target path ... was not found` | Create the Folder in Studio first, or use `workspace` |
| `dotted path` error | `target_path` must look like `workspace.Effects` |
| `Simulation needs the optional 'lupa' package` | `pip install -e ".[simulate]"` |
| Search returns nothing | Entries must be `status: curated`; ingest creates candidates (`--candidates` shows them) |
| MCP client shows no tools | Use the interpreter where the package is installed; set `RBXVFX_ROOT` to this checkout |

## Repository map

`rbxvfx/` code (`core/` vendored shared code; `domain/` Roblox-specific: `api`, `effects`, `luau`, `analysis`, `preview`,
`mock_roblox`, `studio`) | `recipes/` | `style/` | `library/` schema | `examples/` synthetic positive/negative sheets |
`evals/` tasks, rubric, versioned reports | `feedback/` | `skills/` | `adapters/` | `references/` (API snapshot, links, verification log) |
`tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your assets.
