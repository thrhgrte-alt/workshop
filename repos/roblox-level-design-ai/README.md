# roblox-level-design-ai

A level-design assistant for **Roblox**: it turns a design brief into a structured level *spec*, analyses the layout exactly, builds a
deterministic blockout, checks the built geometry is walkable, preserves locked constraints across revisions, and hands safe Luau to Roblox
Studio's own MCP server. Built so a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real level-design work.

**What it is:** instructions + a searchable reference library + level templates + exact graph/sightline/pacing analysis + blockout generation +
walkability verification + a Luau mock for dry runs + locks, revisions and diffs + feedback loop + eval suite, as an MCP server and a CLI.

**What it is not:** it does not train or change any model, and it does not connect to Roblox Studio itself. A model reasons over what is in its context
(style brief, retrieved examples, tool results). Improvement comes from data you curate. "Is it fun?" stays a human judgment.

> **Honest status.** Built without Roblox Studio. The analysis is exact for the spec; the blockout is validated by flood-filling the *generated* geometry
> and by running the generated Luau on a mock DataModel. **Nothing has run inside real Studio.** Character size, step height and thresholds are
> heuristic defaults to replace with your game's values.

## Capability matrix

| Capability | Without Studio | Needs Studio / extra | Status |
|---|---|---|---|
| Spec validation (overlaps, bounds, shared walls, doors, ramps/stairs, items) | yes | | tested (100 eval tasks) |
| Routes, loops, independent routes, choke points, fairness, spawn separation | yes | | tested, hand-checked numbers |
| Plan-view sightlines, landmark visibility, spawn exposure, pacing | yes | | tested |
| Blockout generation (floors, walls with door gaps, stairs/ramps, markers) | yes | | tested |
| Walkability of the built geometry (independent of the spec) | yes | | tested; catches blocked doors, too-tall steps, low doors |
| Luau generation, safety lint, injection-safe ids/paths | yes | | tested |
| Run the Luau on a mock DataModel | yes (`lupa`) | | tested; the mock rejects bad classes/properties/types |
| Locked constraints, revisions, diffs | yes | | tested |
| Player-height review cameras, plan maps | yes (Pillow for maps) | | tested |
| Build in Studio, read it back, capture, playtest | no | Studio + its built-in MCP server | **unverified** |
| MCP server (stdio) | yes | an MCP-capable client | tested in-memory and over real stdio |

## Quick start

```bash
git clone <this repo> && cd roblox-level-design-ai
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+
python -m rbxlevel doctor
python -m rbxlevel evaluate loop_arena                 # findings, metrics, rubric
python -m rbxlevel map loop_arena                      # plan PNG under workspace/output/maps
python -m rbxlevel build loop_arena --target workspace.Levels     # prints the Luau
python -m rbxlevel eval run --label mine && python -m rbxlevel eval compare baseline mine
python -m pytest
```

Nothing is written outside `workspace/` unless you set `RBXLEVEL_ALLOWED_PATHS`.

## How it works

```
 brief -> locks + style brief + past corrections + retrieved reference levels   (few, relevant)
       -> level spec (template + parameters, or written)                        (model judgment)
       -> normalise + validate; routes/loops/sightlines/pacing/fairness         (exact computation)
       -> blockout parts -> walkability of the BUILT geometry -> mock run       (deterministic)
       -> Luau -> Studio's execute_luau (the agent forwards it)                 (Studio runs it)
       -> player-height views via screen_capture -> person walks it -> verdict  (human judgment, recorded)
```

**Spec format** (rooms as rectangles on a 4-stud grid, door/opening/stairs/ramp connections, spawns, objectives, landmarks, encounters, `locked`): see
`skills/roblox-level-design-workflow/references/spec-format.md`. **Templates:** `hub_and_spoke`, `loop_arena`, `linear_with_branches` (each valid at its parameter extremes).

### Locks
`locked` lists rooms, connections, bounds and placed items that must never change. The first saved revision is the baseline; `check_locked_constraints`, `save_level_revision`
and `build_blockout` all compare against it and refuse on any difference.

### Reviewing like a player
`capture_review_views` returns cameras at eye height along the main route (plus landmark checks, room corners and an overview). A top-down map is a diagnostic only.

### Connecting to Roblox Studio
Enable Studio's built-in MCP server (**Assistant > ... > Manage MCP Servers**) and connect the same agent to it in addition to this server. Used: `list_roblox_studios`,
`execute_luau` (Edit datamodel), `screen_capture`, `get_console_output`, `start_stop_play`. Read each tool's schema for argument names. Studio's MCP can modify your open place:
work on a copy and build into an agreed Folder (`target_path`).

### Safety
Generated Luau creates one Folder `AI_Blockout_<id>` and on re-run replaces **only** a folder with `AIGeneratedBy = "rbxlevel"`; anything else with that name stops it with an error.
No publishing, saving or network calls (lint-enforced and tested). Ids and `target_path` are restricted so they cannot break out of the script. Only Folder, Model, Part and
SpawnLocation can be created. Dry runs reject exactly what real runs reject.

## Your library

Tracked `examples/` are **synthetic** plan maps and specs from the templates (and templates with deliberate defects as negative examples). Your library is private:

```bash
export RBXLEVEL_ASSET_ROOT=/path/to/your/level/specs            # files stay where they are; nothing is uploaded
python -m rbxlevel ingest "$RBXLEVEL_ASSET_ROOT" --kind level --owner user --tag arena          # dry run
python -m rbxlevel ingest "$RBXLEVEL_ASSET_ROOT" --kind level --owner user --tag arena --apply  # writes CANDIDATES
```
Candidates are never returned by default searches; curate by editing `workspace/library/assets.jsonl` (description, tags, `domain`, `"status": "curated"`; negatives use
`"polarity": "negative"` with `correction_notes`). `python -m rbxlevel validate-library --check-files` checks everything. Optional embeddings: `build-index --embedder hashing-512`
(local, lexical) or `st:clip-ViT-B-32` with `pip install -e ".[embeddings]"` (CLIP-style; untested here).

## Style

`style/style.yaml` + `style/STYLE.md`: defaults, **character assumptions**, limits, metric ranges, per-profile route-choice requirements, rules, exclusions. **A starter template**;
thresholds are heuristics. Every evaluation lists the assumptions it used.

## Tools (MCP and `python -m rbxlevel call <tool> --json '{...}'`)

Analysis tools take one of `spec`, `level_name` (+`version`) or `template_id` (+`params`). Read-only tools carry `readOnlyHint`; writing tools default to `dry_run=true`.

| Tool | Writes? | Purpose |
|---|---|---|
| `get_style_brief` | no | Compact style spec |
| `find_past_corrections` | no | The user's earlier corrections relevant to this request |
| `search_library` | no | Generic hybrid search |
| `search_level_library` | no | Reference levels by text, type, tags; returns avoid-examples |
| `list_level_templates` | no | Templates, parameters, ranges |
| `create_level_spec` | revision 1 when `dry_run=false` | Normalised, validated spec with assumptions and open questions |
| `save_level_revision` | revision when `dry_run=false` | Diff vs latest; blocks lock violations |
| `diff_level_specs` | no | Compare two saved revisions |
| `check_locked_constraints` | no | Compare with the baseline's locked geometry |
| `validate_connectivity` | no | Components, unreachable rooms, dead ends, routes, choke points |
| `analyze_routes_and_loops` | no | Loops, alternates, independent routes, fairness, spawn separation |
| `check_sightlines` | no | Long sightlines, landmark visibility, spawn exposure |
| `evaluate_level` | no | Everything + built-geometry walkability + locks + rubric (manual criteria unscored) |
| `capture_review_views` | no | Player-height cameras for `screen_capture` |
| `render_level_map` | PNG when `dry_run=false` | Plan view with routes and landmark line-of-sight |
| `build_blockout` | Luau file when `dry_run=false` | Parts, walkability, locks; then the Luau for Studio |
| `verify_blockout_geometry` | no | Flood-fill the generated geometry with the configured character |
| `simulate_blockout` | no | Run the Luau on the mock DataModel (needs `lupa`) |
| `inspect_blockout` | no | Read-only Luau, then compare Studio's parts with the spec (drift, hand edits) |
| `parse_studio_report` | no | Parse what a script returned/printed |
| `remove_blockout_script` | no | Luau that removes a blockout this tool created |
| `record_run` | feedback file | Save request, retrieved ids, outputs |
| `record_decision` | feedback file | Save the user's verdict and structured corrections |
| `promote_run` | library file | Add a reviewed run to the library (`confirm=true` to curate) |

## Connecting agents

Canonical: `AGENTS.md` and `skills/roblox-level-design-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (CI checks drift).

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

Retrieval of curated references (with avoid-examples) -> `record_decision` corrections recalled by `find_past_corrections` -> explicit curation (`confirm`) -> recurring corrections turned into
template/threshold changes -> eval tasks that lock lessons in (`eval compare`, and `eval check-results <dir>` to score an agent's saved outputs). No step changes model weights.

## Limitations

- Never run in real Studio. The mock proves coherence with the documented API only.
- Rooms are axis-aligned rectangles on a grid; no curved or diagonal walls, no multi-storey overlaps (rooms cannot overlap in plan), ramps are fine steps.
- Distances are door-to-door plan distances; line of sight is plan view at eye height (floors, ceilings, props do not occlude); there is no navmesh, combat or physics simulation.
- Character numbers and thresholds are assumptions/heuristics. "Fun" is not measured.
- Three templates; other genres need new templates or a hand-written spec.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[room_overlap]`, `[no_shared_wall]`, `[door_outside_wall]` | Rooms must touch along a wall; keep a 2-stud frame at each end of a door |
| `[floor_mismatch]` | Doors need equal floors; use `stairs` or `ramp` |
| `locked constraints changed` | You edited locked geometry; revert, or start a new baseline deliberately |
| `not walkable` | A door/step/clearance mismatch in the built geometry; read `geometry_findings` |
| `refusing to replace 'AI_Blockout_x'` | A folder not created by this tool has that name |
| `dotted path` error | `target_path` must look like `workspace.Levels` |
| Search returns nothing | Entries must be `status: curated` (`--candidates` shows candidates) |
| `Simulation needs the optional 'lupa' package` | `pip install -e ".[simulate]"` |

## Repository map

`rbxlevel/` (`core/` vendored; `domain/`: `spec`, `graphs`, `sight`, `build`, `walkcheck`, `evaluate`, `mapview`, `mock_roblox`, `api`) | `recipes/` templates | `style/` | `library/` schema |
`examples/` synthetic maps + specs | `evals/` | `feedback/` | `skills/` | `adapters/` | `references/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your assets.
