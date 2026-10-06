# roblox-economy-balancer

An economy and progression balancer for Roblox games, for **one place at a time**: it models ore values, drill or tool tiers, vendor prices, upgrade costs, rebirths and gamepass boosts in an
`economy.yaml`, simulates players deterministically, and finds problems before players do: grind walls, cliffs, runaway currency, dominant choices, upgrades nobody should buy, boosts that
make progress pay-to-win. It proposes the smallest fix, and emits reviewable Luau. Built so a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real balancing work.

**What it is:** a spec format + validator, a seeded simulator, analyses, a rebalancer, an importer (a read-only Luau dump you run in Studio, and a normaliser for the JSON it prints), a Luau exporter,
per-place bands/specs/feedback, an eval suite, as an MCP server and a CLI. **What it is not:** it never connects to Roblox Studio, never publishes, never overwrites a config, and does not change any model.
The model chooses targets and explains trade-offs; the simulator does all the arithmetic. **Every number in a reply must come from a tool result; never mental maths.**

> **Honest status.** Built without Roblox Studio and without your game. Tested only on hand-built toy economies and hand-computed expectations. **Never run against a real game, real values or live Studio.**
> The simulator is only as good as the archetype assumptions. It predicts **relative pacing**, not real **retention** or **revenue**. The target pacing in `style/style.yaml` is a **placeholder** (you have not supplied yours).
> `guide-core` was **not available**: shared machinery comes from the vendored `econbal/core` through one adapter file, `econbal/guide_adapter.py` (see below).

## What you still need to supply
This repository cannot work without these, and ships only synthetic stand-ins:
1. **Your places list**: `projects.yaml` (project id, place id, Studio name, Roblox place id, universe id). The bundled one lists three clearly synthetic places (`demo_mine/main`, `demo_mine/hardcore`, `demo_tycoon/main`). Point `ECONBAL_PROJECTS` at your own private file.
2. **Real dumped values**: run `emit_import_luau`'s script in your open place and give the JSON to `normalise_import` (or write `economy.yaml` yourself). Nothing real has been imported.
3. **Your target pacing**: replace the starter bands (`style/style.yaml`, or `projects/<project>/<place>/bands.yaml` per place). Early game 5-15 active minutes per upgrade etc. are placeholders.
4. **Real archetype data**: session length, sessions per day and efficiency of your casual, regular and grinder players. The importer fills in clearly marked guesses (`assumed: true`).
5. **Real examples**: a good and a bad economy from your game to replace the toys in `examples/`.

## Capability matrix

| Capability | Without Studio | Needs Studio / a real game | Status |
|---|---|---|---|
| Spec schema, validator (ids, references, ranges, contiguous tiers, cycles, typos, locked paths) | yes | | tested |
| Seeded simulator (active-minute model, save-for-best-payback policy, boosts, rebirths, tax/upkeep) | yes | | tested against hand-computed numbers and an independent paper model |
| Time to next upgrade per tier and archetype vs target bands, walls, cliffs, projected steps beyond the horizon | yes | | tested on toys |
| Flow and inflation per currency and day, content exhaustion, conservation of currency | yes | | tested |
| Dominant options and strategies, dead options, monetisation checks, sensitivity, spec comparison | yes | | tested on toys |
| Smallest-change rebalance proposals (never touching locked values) | yes | | tested on toys |
| Luau export of a NEW config module (lint, place guard, never overwrites) | yes (`lupa` mock) | running it in Studio | tested on a mock DataModel; **unverified in Studio** |
| Importer: read-only dump script + JSON normaliser | yes (`lupa` mock, hand-written JSON) | running the dump in your place | tested on mock and hand-written JSON; **unverified on a real game** |
| Projects and places: per-place spec, bands, feedback, versions, output; open-Studio match | yes (matching uses data you pass in) | the hub's `list_roblox_studios` | tested |
| Learning from playtest notes, accepted/rejected rebalances (`suggest_band_adjustments`) | yes | real playtests | rules tested; **no real notes used** |
| Plots (Pillow; matplotlib optional) | yes | | tested (matplotlib path untested: not installed) |
| MCP server (stdio) | yes | an MCP-capable client | tested in memory and over real stdio |
| Retention, revenue, real player behaviour | no | **not modelled** | **not claimed** |

## Quick start

```bash
git clone <this repo> && cd roblox-economy-balancer
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"                                # Python 3.10+; Pillow, lupa, pytest
python -m econbal doctor
python -m econbal places                               # demo_mine/main, demo_mine/hardcore, demo_tycoon/main (SYNTHETIC)
python -m econbal check --project-id demo_mine --place-id main        # PASS
python -m econbal check --project-id demo_tycoon --place-id main      # FAIL: runaway currency
python -m econbal table --project-id demo_mine --place-id main
python -m econbal plot --project-id demo_mine --place-id main --out /tmp/curves.png
python -m econbal eval run --label mine && python -m econbal eval compare baseline mine
python -m pytest
```

Nothing is written outside `workspace/` unless you set `ECONBAL_ALLOWED_PATHS`. The server is local, stdio and unauthenticated; `--http` refuses non-loopback binds.

## Projects and places

One spec, one set of target bands and one decision history **per place**, in `projects/<project_id>/<place_id>/` (`economy.yaml`, optional `bands.yaml`) and `workspace/projects/<project_id>/<place_id>/` (feedback,
saved versions, output, library). `projects.yaml` is the registry.

* **Every tool takes `project_id` and `place_id`** (the comparison tool takes `a_*` and `b_*`). Without a resolvable pair a tool **refuses and says what is missing and what is known**; it never guesses.
* **Every report states the project and place** (`project_id`, `place_id`, and `synthetic_place` for the demo places).
* **Isolation.** Notes, corrections and saved versions for one place are invisible to another. Mark a record `is_global=true` (on `record_run` / `record_decision` / `promote_run`) to share it; global records live in `workspace/projects/_global/_global/` and are read by `find_past_corrections` and `suggest_band_adjustments` in every place (switch off with `include_global=false`).
* **Per-place bands.** `bands.yaml` replaces `target_bands` (and may override `ranges`, `learning`, `reference_archetypes`) on top of the global `style/style.yaml`. The same economy can pass in one place and fail in another.
* **Comparing places is allowed** (`compare_specs`), **changing them across places is not**: `propose_rebalance`, `export_values_luau` and `save_spec_version` take exactly one place, and there is no argument that could name a second.
* **Open Studio must be the named place.** `emit_import_luau` and `export_values_luau` need `studios`, the output of the hub's `list_roblox_studios` passed in as data: a list of `{"name", "place_id", "studio_id"}` (aliases `placeName`/`title`, `placeId`/`PlaceId`, `id`/`studioId` are accepted). It matches the registry by Studio name (case-insensitive) or Roblox place id and **refuses** when nothing, or more than one instance, matches. The generated Luau also **embeds the place and stops itself** in any other place (`game.PlaceId`, or `game.Name` for an unpublished place).

## Tools (MCP and `python -m econbal call <tool> --json '{...}'`)

Read-only tools carry `readOnlyHint`; writing tools default to `dry_run=true`. Output is short on purpose: a one-line `summary` first, ranked findings (errors and warnings; info only with `detail=true`), rows only with `detail=true`.
Tools marked **rare** have descriptions starting `[rare]`: leave them out with `ECONBAL_DISABLE_GROUPS=rare` (about 40 percent less tool text in the context).

| Tool | Writes? | Group | Purpose |
|---|---|---|---|
| `get_style_brief` | no | core | Pacing brief and bands for this place (global style + the place's bands) |
| `find_past_corrections` | no | core | This place's earlier corrections (plus global ones) relevant to a request |
| `validate_economy_spec` | no | core | Ids, references, ranges, tiers, cycles, typos, locked paths; errors block everything else |
| `check_economy` | no | core | Run every analysis: verdict, ranked findings, headline metrics. Start here |
| `simulate_progression` | no | core | The seeded simulation per archetype: each purchase minute and gap |
| `time_to_upgrade_table` | no | core | Active minutes per tier and archetype vs the bands; over/under band, wall, cliffs |
| `flow_report` | no | core | Income, sinks, net inflation per currency and day; runaway currency, content exhaustion |
| `find_dominant_strategies` | no | core | Choices where one option beats the rest; strategy labels that win every choice |
| `find_dead_options` | no | core | Upgrades whose payback is too long (or that do nothing) |
| `monetisation_check` | no | core | Boost effect on pace, free-player cliff, pay-to-win |
| `sensitivity` | no | rare | Vary one value and show how every tier's timing moves |
| `compare_specs` | no | rare | Two places (or two saved versions): changed values, timing differences |
| `plot_curves` | no (derived PNG in the git-ignored place folder) | rare | Income and purchase curves per archetype (Pillow; matplotlib optional) |
| `suggest_band_adjustments` | no | rare | Deterministic suggestions from saved playtest notes and decisions (see Learning) |
| `emit_import_luau` | no | rare | Read-only Luau that dumps vendor items, tool stats, ore values as JSON; refuses unless the open place is this place |
| `normalise_import` | no | rare | Dumped JSON into a DRAFT spec, listing every assumption and unresolved field |
| `propose_rebalance` | file when `dry_run=false` | core | Smallest set of value changes (1-2) that clears ONE finding, before/after timing, locked values never changed |
| `export_values_luau` | file when `dry_run=false` | rare | Luau that creates ONE NEW config ModuleScript with the values; embeds the place; never overwrites |
| `save_spec_version` | version when `dry_run=false` | rare | Numbered, copy-on-write version of a spec for this place |
| `record_run` | feedback file | core | Save a request or playtest note for this place (`is_global` to share) |
| `record_decision` | feedback file | core | Save the user's verdict and structured corrections (use their words) |
| `promote_run` | library file | rare | Add a reviewed run to this place's library (`confirm=true` to curate) |

## The model (what the numbers mean)

Documented at the top of `econbal/domain/sim.py` and in `skills/roblox-economy-balancer-workflow/references/model.md`. In short: time is **active minutes**; an archetype plays `sessions_per_day` sessions of
`session_minutes`; income per source is `(per_minute + adds) x mults` times the archetype's `efficiency`; taxes and upkeep are sinks; the player **saves for the upgrade with the best payback** among those available
(tier order, `requires`, alternatives foreclosed) and buys it at the end of the first whole minute that the balance covers; boosts apply from minute 0; a rebirth is bought when the ladder is complete, resets listed tracks
and currencies and multiplies income. `variance` (default 0) draws one factor per session from `random.Random("<seed>:<archetype>")`: same spec and seed, identical output. Walls, cliffs, dead options and runaway
currency are judged by thresholds in `style/style.yaml` and wording/severity in `rules/findings.yaml`, not in code. Tiers beyond the simulated horizon are **projected** from current income and still flagged.

## Learning (and what it does not mean)

`record_run` + `record_decision` save accepted/rejected rebalances (put `{"rebalance": {"changes": [{"path", ...}]}}` in the run's `constraints`) and real playtest notes (`corrections: [{"dimension": "pacing", "tier": 4, "felt": "too_slow", "note": "tier 4 felt too slow"}]`).
`suggest_band_adjustments` turns them into suggestions by fixed rules: enough reports (`learning.min_votes`) of "too slow" inside a band lower its maximum to `(1 - shrink) x` the simulated step; "too fast" raises its minimum to `(1 + grow) x`; if the simulation already
shows the step outside the band it says to change the economy instead; archetype observations (`dimension: archetype_assumption`) move a field to the median; a value rejected repeatedly and never accepted is a candidate for `locked`.
Suggestions are never applied; a person edits the bands or the spec and adds an eval task that locks the lesson in. No step changes model weights.

## Safety

Write tools default to `dry_run=true` and return a plan. Generated Luau creates ONE new `ModuleScript` (or only reads); it never overwrites, replaces or destroys anything, publishes, saves, loads code or touches the network (lint-enforced and tested, including
injection attempts in names and paths). `propose_rebalance` writes a NEW file named by content hash; the place's `economy.yaml` is never modified by any tool. Locked values (`locked:` patterns or `locked: true` on an entity) can never be changed by a proposal.

## What is verified, and what is not

**Verified here (tests and evals, on toy economies and fixtures):** the simulator against hand-computed expectations (27 small economies derived on paper) and an independently written exact-fraction model on 60 random ladders, plus a naive minute-by-minute stepper; conservation of currency every day;
the four toy economies (balanced passes; wall, runaway and dominant are flagged); validator rejections; rebalance minimality on the grid and lock safety; Luau lint, place guard, no-overwrite and a mock-DataModel run (needs `lupa`); importer on hand-written JSON and a mock tree; scope refusal, isolation and global flag;
open-Studio matching; MCP over an in-memory session and real stdio; docs list every tool; skills and agent files in sync; output-size budgets; a guard test that breaks the judge on purpose and demands the evals notice.
**Not verified:** anything in real Studio (the Luau has only run on the mock; Studio may refuse `Script.Source` from `execute_luau`), anything on a real game's values, real archetype data, real playtests, the `list_roblox_studios` output shape (assumed), the matplotlib backend, client configs in `adapters/mcp-clients/` against live clients,
and any claim about retention or revenue. Target bands are placeholders.

## Limitations

- Pacing is relative to the assumed archetypes. Efficiency is a single multiplier; real players differ by skill, AFK time, social play and mood.
- Active minutes only: no offline earnings, no gaps between sessions, no daily rewards, events, limited-time boosts, trading or other players.
- One purchasing policy per run (best payback by default; `cheapest_cost` and `spec_order` exist). Real players are less rational; a zero-effect upgrade that gates later tiers is flagged because the policy cannot represent it honestly.
- Incomes add across sources (the player is modelled as using every unlocked source at once); backpack capacity, ore rarity rolls and luck are not modelled.
- One rebirth definition per spec. Costs are single-currency.
- `propose_rebalance` searches a fixed grid of relative changes for one value, then pairs; "smallest" means smallest on that grid, and it refuses (rather than guessing) when nothing safe is found.
- The importer reads attributes and Value objects only. Games that keep values in ModuleScripts need a hand-written dump in the same JSON format.

## Connecting agents

Canonical: `AGENTS.md` and `skills/roblox-economy-balancer-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (a test checks drift).

| Client | Instructions | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

None tested against live clients. Add Roblox Studio's own MCP server to the same client; this server's Luau is meant for its `execute_luau`.

## Shared machinery and `guide-core`

`guide-core` was **not available** (its spec file was not provided). All shared machinery (dry-run `Plan`/`Versioner`, feedback store, retrieval, eval runner and compare, MCP kit, style and rubric loading, path scope) comes from the vendored copy of the suite kit in `econbal/core/`
(not edited). **All code in `econbal/` imports it only through `econbal/guide_adapter.py`**, which re-exports it under the interface names `dryrun`, `feedback`, `retrieval`, `evals`, `mock`, `luau_safety`, `mcpkit`, `config`, `scope`
(a test greps the package to enforce this). **Swapping in the real guide-core means editing that one file.** The vendored core has no scoping, so the adapter adds `scoped_project` (per-place workspace) and its own `all_tools` (scoped tools replace the common ones; the
unscoped `search_library` is dropped; `ECONBAL_DISABLE_GROUPS` is honoured). The mock DataModel and the Luau lint have no equivalent in the vendored core and live in `econbal/domain/` (`mock_luau.py`, `luau.py`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `missing project_id and place_id` | Pass both; the message lists the known places |
| `unknown place` | Add it to `projects.yaml` (or `ECONBAL_PROJECTS`); the tool will not guess |
| `refusing: the open Studio instance(s) ... are not place` | Open the right place in Studio and pass a fresh `list_roblox_studios` output as `studios` |
| `the spec ... has errors` | Run `validate_economy_spec` and fix what it lists |
| `refusing to overwrite: 'X' already exists` (in Luau) | Choose a new `config_name`; the script never replaces anything |
| `no safe fix found` | Unlock a value, change something by hand, or revisit the band (`suggest_band_adjustments`) |
| Luau mock skipped | `pip install -e ".[simulate]"` |

## Repository map

`econbal/` (`core/` vendored; `guide_adapter.py`; `domain/`: `spec`, `sim`, `analysis`, `rebalance`, `luau`, `importer`, `mock_luau`, `places`, `plot`, `learning`, `schema`) | `rules/findings.yaml` finding catalogue | `style/` bands and thresholds (placeholders) |
`projects.yaml` + `projects/` per-place specs and bands (synthetic) | `library/` schema | `examples/` toy economies, import dump, plots | `evals/` tasks, rubric, baseline | `feedback/` | `skills/` | `adapters/` | `references/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your data.
