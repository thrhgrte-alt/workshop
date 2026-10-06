# modular-set-dressing-ai

A set-dressing assistant for **modular asset kits** (aimed at Roblox, engine-neutral core): it places approved kit modules into a scene
deterministically, checks collisions, clearances, sightlines and composition exactly, keeps locked pieces untouched, makes every change a
reviewable and reversible plan, and hands safe Luau to Roblox Studio's own MCP server. Built so a capable AI agent (Claude Code, Codex CLI,
Gemini CLI, Copilot, Cursor, ...) can do real dressing work.

**What it is:** instructions + a kit metadata model + a searchable reference-scene library + exact placement geometry (oriented footprints, height ranges,
sockets) + seeded rule-driven dressing + composition metrics + versions/undo/locks + a Luau mock for dry runs + feedback loop + eval suite, as an MCP server and a CLI.

**What it is not:** it does not train or change any model, and it does not connect to Roblox Studio itself. A model reasons over what is in its context
(style brief, retrieved scenes, tool results). Improvement comes from data you curate. "Does it feel lived-in?" stays a human judgment.

> **Honest status.** Built without Roblox Studio or your assets. Geometry is exact for the footprints and heights the kit states; the Luau is checked by a lint and
> run on a mock DataModel. **Nothing has run inside real Studio**, and the yaw convention should be confirmed visually on the first placement of a new kit.
> The example kit is synthetic (primitives only). Thresholds in the style spec are heuristics.

## Capability matrix

| Capability | Without Studio | Needs Studio / extra | Status |
|---|---|---|---|
| Kit metadata validation, search, report of unresolved (defaulted) fields | yes | | tested |
| Oriented footprints, height-aware collision (SAT), region/exclusion checks | yes | | tested, cross-checked against brute-force sampling |
| Path clearance and eye-height sightline checks | yes | | tested |
| Socket snapping (chair to table, prop to surface), grid/yaw snapping | yes | | tested |
| Seeded rule-driven dressing with explanations, rejected candidates listed | yes | | tested, deterministic per seed |
| Composition metrics (coverage, repetition, colour spread, negative space, focal, hierarchy) | yes | | tested |
| Plans (adds/moves/removes), apply, invert, locks, versions, undo, diffs | yes | | tested |
| Luau generation (primitives or clone-from-kit), lint, injection-safe ids/paths | yes | | tested |
| Run the Luau on a mock DataModel | yes (`lupa`) | | tested |
| Plan-view maps | yes (Pillow) | | tested |
| Place in Studio, read it back, capture, playtest | no | Studio + its built-in MCP server | **unverified** |
| Clone mode with real kit models | no | your kit in `ReplicatedStorage` | **unverified** |
| MCP server (stdio) | yes | an MCP-capable client | tested in-memory and over real stdio |

## Quick start

```bash
git clone <this repo> && cd modular-set-dressing-ai
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+
python -m setdress doctor
python -m setdress validate examples/scenes/tavern_corner.json
python -m setdress measure examples/scenes/tavern_corner.json
python -m setdress dress examples/scenes/tavern_bare.json --seed 2     # a plan; nothing is saved
python -m setdress map examples/scenes/tavern_corner.json
python -m setdress export examples/scenes/tavern_corner.json          # prints the Luau
python -m setdress eval run --label mine && python -m setdress eval compare baseline mine
python -m pytest
```

Nothing is written outside `workspace/` unless you set `SETDRESS_ALLOWED_PATHS`.

## How it works

```
 brief -> locks + style brief + past corrections + retrieved reference scenes        (few, relevant)
       -> search_kit: only approved modules                                          (retrieval)
       -> plan: place_module / snap_to_socket / dress_region (dry run first)         (model judgment + seeded rules)
       -> collisions, paths, sightlines, locks, composition metrics                  (exact computation)
       -> save version (undo any time) -> Luau -> Studio's execute_luau              (Studio runs it)
       -> person looks at it in Studio -> verdict recorded                           (human judgment)
```

**Scene format:** region, exclusions, functional `paths`, eye-height `corridors`, `focal` points, `instances`, `locked`; see
`skills/modular-set-dressing-workflow/references/scene-format.md`. **Kit format:** `examples/kit/kit.yaml` (modules with footprint, height, pivot, tags,
sockets, scale range, collision, priority; kit-level `defaults`). Anything the kit does not state is defaulted and **listed by `kit_report`** instead of silently assumed.

**Conventions:** `x`/`z` horizontal studs, `y` up, `yaw` degrees matching `CFrame.Angles(0, math.rad(yaw), 0)`, module forward is local +Z.

### Locks
`locked` (and per-instance `locked: true`) pieces can never be moved or removed by a plan. The first saved version is the baseline; `check_locked_constraints` and every write compare against it.

### Safety
Write tools default to `dry_run=true`. Generated Luau creates one Folder `AI_SetDress_<id>` and on re-run replaces **only** a folder with `AIGeneratedBy = "setdress"`;
anything else with that name stops it with an error. No publishing, saving or network calls (lint-enforced and tested). Ids and `target_path` are restricted so they cannot
break out of the script. Invalid placements are refused rather than saved.

### Connecting to Roblox Studio
Enable Studio's built-in MCP server and connect the same agent to it in addition to this server; forward the generated Luau to its `execute_luau` (Edit datamodel) and read that
tool's schema for argument names. Studio's MCP can modify your open place: work on a copy and use an agreed Folder (`target_path`).

## Your kit and library

The tracked `examples/` are **synthetic** (a 16-module tavern kit, five scenes, plan maps). Your kit and references are private:

```bash
export SETDRESS_KIT=/path/to/your/kit.yaml                       # replaces the example kit
export SETDRESS_ASSET_ROOT=/path/to/your/reference/scenes        # files stay where they are; nothing is uploaded
python -m setdress ingest "$SETDRESS_ASSET_ROOT" --kind scene --owner user --tag tavern          # dry run
python -m setdress ingest "$SETDRESS_ASSET_ROOT" --kind scene --owner user --tag tavern --apply  # writes CANDIDATES
```
Candidates are never returned by default searches; curate by editing `workspace/library/assets.jsonl` (description, tags, `domain`, `"status": "curated"`; negatives use
`"polarity": "negative"` with `correction_notes`). `python -m setdress validate-library --check-files` checks everything.

## Style

`style/style.yaml` + `style/STYLE.md`: traits, metric ranges, constraints, exclusions, correction dimensions. **A starter template**; tune the thresholds on scenes you consider good.

## Tools (MCP and `python -m setdress call <tool> --json '{...}'`)

Scene tools take one of `scene` (inline), or `scene_name` (+`version`) for a saved scene. Read-only tools carry `readOnlyHint`; writing tools default to `dry_run=true`.

| Tool | Writes? | Purpose |
|---|---|---|
| `get_style_brief` | no | Compact style spec |
| `find_past_corrections` | no | Your earlier corrections relevant to this request |
| `search_library` | no | Generic hybrid search of the reference library |
| `search_scene_library` | no | Reference scenes/modules by text and tags; returns avoid-examples |
| `search_kit` | no | Approved modules by text, tags, size limits, socket type, size class |
| `kit_report` | no | Kit validation, defaulted (unresolved) metadata, socket types, tags |
| `inspect_scene` | no | Region, constraints, instances per module, locks, findings |
| `check_collisions_and_clearance` | no | Collisions (with height overlap), region/exclusion, blocked paths and sightlines |
| `check_locked_constraints` | no | Compare locked instances with the baseline version |
| `validate_composition` | no | Density, repetition, colour, negative space, focal points, hierarchy + rubric (manual criteria unscored) |
| `diff_scenes` | no | Added, removed, changed instances between saved versions |
| `simulate_scene` | no | Run the generated Luau on the mock DataModel (needs `lupa`) |
| `inspect_placed` | no | Read-only Luau, then compare Studio's instances with the scene (drift, hand edits) |
| `parse_studio_report` | no | Parse what a script returned or printed |
| `remove_scene_script` | no | Luau that removes a scene this tool created |
| `place_module` | version when `dry_run=false` | Place one approved module; grid/yaw snapping; refuses invalid placements |
| `snap_to_socket` | version when `dry_run=false` | Place a module so its socket meets an existing instance's socket |
| `apply_scene_plan` | version when `dry_run=false` | Apply a reviewed plan `{adds, moves, removes}`; locked instances cannot change |
| `dress_region` | version when `dry_run=false` | Seeded, rule-driven dressing; returns an explanation per placement |
| `undo_last_change` | version when `dry_run=false` | Restore the previous version (saved as a new version; history is kept) |
| `save_scene_variant` | variant when `dry_run=false` | Named variant with its own history |
| `export_scene` | file when `dry_run=false` | Engine-neutral JSON or Roblox Luau for Studio's MCP |
| `render_scene_map` | PNG when `dry_run=false` | Plan view: footprints, paths, sightlines, focal radii, locks, problems |
| `record_run` | feedback file | Save request, retrieved ids, outputs |
| `record_decision` | feedback file | Save the user's verdict and structured corrections |
| `promote_run` | library file | Add a reviewed run to the library (`confirm=true` to curate) |

## Connecting agents

Canonical: `AGENTS.md` and `skills/modular-set-dressing-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (CI checks drift).

| Client | Instructions | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

Where each vendor documents its files **as understood when written**; none tested against live clients. The server is local, stdio, unauthenticated and refuses non-loopback binds.
Add Roblox Studio's own MCP server to the same client.

## How it improves (and what that does not mean)

Retrieval of curated reference scenes (with avoid-examples) -> `record_decision` corrections recalled by `find_past_corrections` -> explicit curation (`confirm`) -> recurring corrections turned into
style/threshold changes -> eval tasks that lock lessons in (`eval compare`, and `eval check-results <dir>` to score an agent's saved outputs). No step changes model weights.

## Limitations

- Never run in real Studio. The mock proves coherence with the documented API only.
- Plan-view geometry: footprints are oriented rectangles with a height range; no tilt, no stacking beyond the stated `y`, no mesh-accurate collision, no physics.
- Sightlines are corridors at one eye height, not ray casts against the whole scene. Path clearance is a corridor rectangle, not a navmesh.
- Clone mode assumes your kit models live at the stated path with the stated pivots; it is unverified without your assets.
- Thresholds are heuristics. "Lived-in" is not measured; those criteria stay unscored until a person scores them.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `not in the approved kit` | Use `search_kit`; do not invent a module. Add it to the kit file first |
| `[blocks_path]`, `[blocks_sightline]` | Move the piece out of the corridor or change the corridor deliberately |
| `locked ... cannot be moved` | Locked pieces are law; start a new baseline deliberately if the brief changed |
| `refusing to save` | The result has errors; read the findings, fix the plan |
| `refusing to replace 'AI_SetDress_x'` | A folder not created by this tool has that name |
| `dotted path` error | `target_path` must look like `workspace.Props` |
| Search returns nothing | Entries must be `status: curated` (`--candidates` shows candidates) |
| `Simulation needs the optional 'lupa' package` | `pip install -e ".[simulate]"` |

## Repository map

`setdress/` (`core/` vendored; `domain/`: `geom`, `kit`, `scene`, `dress`, `compose`, `export`, `mapview`, `mock_roblox`, `api`, `studio`) | `style/` | `library/` schema |
`examples/` synthetic kit, scenes, maps | `evals/` | `feedback/` | `skills/` | `adapters/` | `references/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your assets.
