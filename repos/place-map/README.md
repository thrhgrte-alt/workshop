# place-map

A searchable map of a Roblox place, for **one place at a time**, so an AI agent does not have to re-explore the place every session. It keeps a snapshot of the instance tree and the scripts, indexes them (names, classes, attributes,
script summaries, remotes, requires, DataStore keys), builds a dependency graph, finds landmarks **by role from structure, not by name** (a vendor called `Bob`, a collectible called `Thing1`), answers "where is X",
"what fires remote Y", "what breaks if module Z changes" and "what changed since last time", and keeps several places apart (`dive-and-mine::ServerScriptService/Services/ShopService`).
Built so a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real work with it; it is an MCP server and a CLI exposing the same functions.

**What it is not:** it never connects to Roblox Studio, never publishes, and does not change any model. It **emits Luau** for the hub's `roblox_studio_execute_luau` to run and **reads the JSON or text that comes back**.
The model reads intent and asks; code does the exact work. **Quote scores, hashes and snapshot ages from tool results; never compute them.**

> **Honest status.** Built without Roblox Studio and without your game. **Never run against real Studio, the real hub, a real game, a real Luau interpreter or a real capture.** The collector Luau was linted and run only on the
> guide-core **mock** DataModel (via `lupa`, extended here with a few classes). **Every parser of hub output is `schema_unverified`** (no real capture exists; see `samples/README.md`). **Every weight, band and limit is a placeholder.**
> The evals were written by the builder next to the code, so they only show that **the tool agrees with itself**; real precision and recall need examples in `evals/real/`, which is empty.
> Learning improves this only as far as the corrections and labels it is given: it cannot learn taste it has not been shown, and early on it runs on default parameters.

## What you still need to supply
1. **Your places list**: `projects.yaml` (project id, place alias, Roblox place id, Studio name, universe id). The bundled one lists clearly synthetic places (`demo_mine/dive-and-mine`, `demo_mine/dive-and-mine-hardcore`, `demo_roles/role-lab`, `demo_tycoon/tycoon-main`). Point `PLACEMAP_PROJECTS` at your own private file.
2. **One real capture of each hub output** the parsers read (exact hub call and file name for each in `samples/README.md`): `list_roblox_studios`, the result of the `plan_refresh` Luau run with `roblox_studio_execute_luau`, `roblox_studio_search_game_tree`, `roblox_studio_script_read`.
3. **A handful of real examples** for `evals/real/` (how, in `evals/real/README.md`). Until then there are zero real results.
4. **Your confirmations**: no landmark names are needed. The tool finds roles by structure with default weights; you only confirm or reject the borderline candidates (`list_candidates`, `label_landmark`), which tunes the weights.

## Capability matrix

| Capability | Without Studio | Needs Studio / a real game | Status |
|---|---|---|---|
| Snapshot schema, ingest of the collector JSON, generic trees, text outlines, `script_read` | yes (hand-written and synthetic input) | real hub captures | tested on synthetic input; **parsers schema_unverified** |
| Collector Luau (read-only, place-guarded, secret-skipping, capped, incremental) | lint + `lupa` mock run | running it in Studio via the hub | tested on the mock DataModel; **unverified in Studio** |
| Index and search: exact path, landmark alias, literal name, role, hybrid text (BM25 + tags + hashing-embedding similarity, misspellings) | yes | | tested on synthetic places |
| Script analysis (requires, remotes fired/handled, DataStore names and keys, summaries cached by content hash) | yes | | tested; a lexer plus regexes, **not a Luau parser** |
| Dependency graph: `who_uses` (transitive), `show_dependencies`, `list_remotes` | yes | | tested with hand-derived answers |
| `diff_snapshots`, staleness flag with a refresh offer | yes | | tested |
| Landmark roles (8 generic roles, 6 generic feature functions, bands, learning from labels, undo) | yes | your labels | tested on synthetic places; **placeholder weights** |
| Multi-place: registry, place-prefixed paths, open-place check, `find_across_places`, `compare_places`, `find_shared_code`, snapshot pruning | yes (matching uses data you pass in) | the hub's `list_roblox_studios` | tested on two synthetic places with a drifted module |
| MCP server (stdio) | yes | an MCP-capable client | tested in memory and over real stdio |
| Real-world precision and recall | no | **your examples in `evals/real/`** | **not claimed** |

## Quick start

```bash
git clone <this repo> && cd place-map
python -m venv .venv && . .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e /path/to/guide-core                     # FIRST: the shared library (a local repository, not on PyPI)
pip install -e ".[dev]"                                # Python 3.10+; lupa (Luau mock), pytest
python -m placemap doctor
python -m placemap places                              # four SYNTHETIC places in three projects
python scripts/make_examples.py                        # (re)generate the synthetic places
python -m placemap call ingest_snapshot --json '{"project_id": "demo_roles", "place_id": "role-lab", "file": "examples/places/role-lab.collect.json", "dry_run": false}'
python -m placemap call find_in_place --json '{"query": "vendors", "role": "vendor", "project_id": "demo_roles", "place_id": "role-lab"}'
python -m placemap eval run --label mine && python -m placemap eval compare baseline mine
python -m placemap eval-report                         # self-written and real results, separately
python -m placemap export-skill --scope demo_roles --place role-lab   # agent skill from the learned weights and corrections (dry run; --write)
python -m pytest
```

Private data (snapshots, labels, feedback, the summary cache, learned weights) lives in `workspace/` (git-ignored); nothing is written elsewhere unless you set `PLACEMAP_ALLOWED_PATHS`.
The server is local, stdio and unauthenticated; `--http` refuses non-loopback binds. Set `PLACEMAP_WORKSPACE` to keep the workspace elsewhere and `PLACEMAP_NOW` (an ISO time) to freeze the clock in tests.

## Projects and places

* **Every tool takes `project_id` and `place_id`** (reads may pass the hub's `studios` instead of `place_id`, which makes the single registered place that is open in Studio the *active* place; zero or several matches refuse). Without a resolvable pair a tool **refuses and says what is missing and what is known**; it never guesses.
* **Registry**: `projects.yaml` (the shared guide-core format) plus an optional `places.yaml` of the same shape (`PLACEMAP_PLACES`). A place has an alias (`dive-and-mine`), a Roblox place id or `local-<alias>` until it is published, a Studio name, a universe, an optional local file and an optional landmarks file.
* **One index per place**: `workspace/projects/<project_id>/<place_id>/` holds its snapshots (latest plus `snapshots.keep_older` older ones; `prune_snapshots` removes the rest), labels and feedback. **Every path an answer returns is prefixed with the place.**
* **Open Studio must be the named place.** `plan_refresh` needs `studios` (the output of `list_roblox_studios`, passed in as data) and **refuses** when the open place is not the one named, saying which place is open; the Luau also guards itself at run time. `ingest_snapshot` refuses data that was collected in another place.
* **`find_in_place` searches the active place only.** Cross-place questions are separate tools (`find_across_places`, `compare_places`, `find_shared_code`); they stay inside ONE project, read stored snapshots only, and label every hit with its place, snapshot and age.
* **Isolation.** Landmark labels, corrections and snapshots belong to one place unless marked global (`is_global` on `record_run` / `record_decision`, `mark_global` on `label_landmark`). The summary cache (keyed by content hash, holds only what is derived from the code text) is shared across places.

## Tools (MCP and `python -m placemap call <tool> --json '{...}'`)

Read-only tools carry `readOnlyHint`; writing tools default to `dry_run=true`. Output is short on purpose: a one-line `summary` first (with the snapshot age and a **STALE** flag), ranked results capped at 10 unless `detail=true`.
Tools marked **rare** have descriptions starting `[rare]`: leave them out with `PLACEMAP_DISABLE_GROUPS=rare`.

| Tool | Writes? | Group | Purpose |
|---|---|---|---|
| `find_in_place` | no | core | Text and semantic search in the active place: a path, a landmark alias, an exact name, a role ("the vendors") or text. Exact answer plus 'not selected' near matches; asks when unsure |
| `get_path_info` | no | core | One path: class, attributes, children, script summary (remotes, requires, DataStore keys), the roles it plays with scores |
| `who_uses` | no | core | Callers and requirers: scripts that require a module (transitively: what breaks if it changes), fire or handle a remote, or mention an instance |
| `list_remotes` | no | core | Remotes: where defined, who fires, who handles; flags unused ones and ones nobody handles |
| `show_dependencies` | no | core | What a script requires (transitively), fires, handles and stores; unresolved requires and cycles |
| `summarize_area` | no | core | A folder, model or service: counts, cached script summaries, remotes, landmarks inside |
| `diff_snapshots` | no | core | What changed between two snapshots (instances, scripts by content hash, remotes) |
| `find_past_corrections` | no | core | This place's earlier corrections (plus global ones) relevant to a request |
| `find_across_places` | no | core | Search several places of one project; every hit carries its place |
| `get_style_brief` | no | core | How to phrase answers (global style plus the project's overrides) |
| `list_places` | no | core | The registry: places, Roblox ids, whether a snapshot exists (reads no place data) |
| `list_candidates` | no | core | Borderline role candidates for the user to confirm or reject |
| `compare_places` | no | rare | Two places of one project: remotes, modules (same path, different content), structure |
| `find_shared_code` | no | rare | Scripts present in several places by content hash; modules whose copies have drifted |
| `list_snapshots` | no | rare | Stored snapshots and how many a prune would remove |
| `list_roles` | no | rare | Roles with the weights and bands in force for a place and where each value came from |
| `plan_refresh` | records the plan when `dry_run=false` | core | The Luau for the hub's `execute_luau`; refuses unless the open Studio place is the named one; full, partial (`roots`/`scripts`) or incremental |
| `ingest_snapshot` | snapshot and summary cache when `dry_run=false` | core | Parse the hub's JSON or text into a snapshot of THIS place; refuses data from another place; flags parser status |
| `label_landmark` | label and weights when `dry_run=false` | core | The user's confirm or reject of a role for a path; nudges that project's weights by a bounded step (undoable) |
| `undo_label` | label and weights when `dry_run=false` | rare | Undo a label and restore the weights it changed (only if nothing newer changed them) |
| `prune_snapshots` | deletes when `dry_run=false` | rare | Remove snapshots beyond the latest plus `keep_older` |
| `record_run` | feedback file | core | Save a request for this place (`is_global` to share) |
| `record_decision` | feedback file | core | Save the user's verdict and structured corrections (use their words) |

`label_landmark`, `undo_label`, `prune_snapshots`, `plan_refresh` and `ingest_snapshot` default to `dry_run=true`. There is no tool that connects to Studio or publishes.

## How a request is resolved

A request names a path, a labelled landmark (alias), a role, an exact name, or text. In that order: a path is exact (a path that names another place is refused: use `find_across_places`); a landmark alias resolves to its path; words that are exactly a role
("the vendors", "collectibles") return **only** the instances that play the role in this place; a name that exactly one instance has returns **only that instance**, with similar siblings and name-sharers listed as `not_selected`; several instances with the same name
are never guessed (the tool asks); otherwise a ranked hybrid text search whose top hit is chosen only if it clearly beats the next. Anything borderline is listed as `not_selected` with a question for the user.

## Landmark roles

Eight generic roles ship in `rules/roles.yaml`: `vendor`, `upgradable_tool`, `resource_node`, `collectible`, `spawn`, `zone`, `currency_display`, `progression_gate`. **No game's names appear in them**: a role is only a weighted list of generic features
(the `vendor` role is the spec's example exactly). Add roles for one project in `projects/<project_id>/roles.yaml`; extend synonyms in `projects/<project_id>/synonyms.yaml` (lists are added to `rules/synonyms.yaml`). Synonyms are hints, not identities:
`Bob` is a vendor because it has a prompt, a humanoid, price attributes and a script that fires a purchase remote.

`score(r, x) = clamp( sum_i w_i * f_i(x) / sum_i |w_i| , 0, 1 )`. At or above `confident` an instance is selected, between `candidate` and `confident` it is listed for you to confirm, below `candidate` it is ignored. The six generic feature functions, written once (`placemap/domain/roles.py`):
**token match** (`|tokens ∩ synonyms| / min(|synonyms|, k)`; tokens split at camelCase, digits, underscores; plural ignored), **class presence** (the instance, if `include_self`, or any descendant has the class), **remote and string references**
(scripts inside the instance, mentioning its name or the name of an ancestor; `matched tokens / min(|tokens|, k)`), **repetition** (`n_similar / (n_similar + c)`, `c = 3`; similar = same class signature and name-token Jaccard above 0.6, numbers in names ignored),
**reference density** (mentions normalised by the place's median, `r / (1 + r)`), **proximity cluster** (within `d` studs of an already-confident landmark of another role, linear fall-off, computed in a second pass).
Deviations from the spec text, all stated: `k` (default 1) is the number of matches that saturate a token feature; a plain Folder is never a landmark and a container is dropped when something inside it scores higher for the same role; a ProximityPrompt itself does not count as "having" an
interaction (only descendants do).

**Learning the weights.** `label_landmark` stores the label with the feature values and applies `w_i <- w_i + eta * (label - score) * f_i(x)` (`eta = 0.1`, label 1 or 0), clamped to the parameter range (the default's sign is kept) and to one parameter step, as a **new version** of the named
parameter `weight.<role>.<feature>` for that project; `undo_label` restores the previous version. Precedence: explicit user label, then learned project weights, then promoted global weights, then defaults; the answer always says which won. A learned adjustment becomes global only when you mark it so.
Labels also leave signals in a local observation log; `python -m placemap propose --project-id ...` groups repeated ones into parameter proposals (nothing is applied; guide-core's gate and an approval by a named person are the only way in).

## Token discipline

Results are capped at `output.max_findings` (10) unless `detail=true`; every tool description is at most 300 characters; the rarely used tools are in the `rare` group; `evals/budgets.yaml` holds a character budget per tool measured on the synthetic mining place, and an eval fails if a tool outgrows it
(`plan_refresh` is large on purpose: it returns the whole collector script). `python -m placemap telemetry` summarises typical output size and time per tool from the local telemetry log written by the server.

## Safety and privacy

Write tools default to `dry_run=true` and return a plan. The collector Luau only reads (it cannot create, destroy, save, publish, load code or use the network: the shared lint enforces it and tests check it) and refuses to run in another place. **Secrets are not stored**: attribute keys or values and
string literals that look like keys or tokens are dropped by the Luau and again at ingest (`rules/secrets.yaml`; heuristics, not a guarantee: review a capture before sharing it). Snapshots hold hashes and analyses, not source code. Identifiers that reach Luau are validated with `fullmatch` (a trailing newline is refused) and embedded with `quote_string`.
`ingest_snapshot` reads only `workspace/`, `samples/`, `examples/` and `PLACEMAP_INGEST_PATHS`.

## What is verified, and what is not

**Verified here (tests and evals, on synthetic places and hand-computed expectations):** the script reader on tricky inputs; the collector Luau (lint, run on the mock DataModel, output parsed and scored, fingerprints equal to Python's, caps, duplicate names, secrets, wrong-place error, print-mode chunks); ingest and snapshot semantics (idempotent, partial merge, carry-over, refusal of other places' data);
role scores on tiny trees with the arithmetic written out, a vendor called `Bob` and collectibles called `Thing1`.. found by structure, decoys with matching names but no interaction never confident, borderline candidates not selected, the weight update, bounds, undo and precedence; the dependency graph and diff against answers derived from the fixture's source text; the two-place cases
(a shared module that drifted, the wrong open place refused, cross-place hits labelled); scope refusal and isolation for every tool; MCP over an in-memory session and real stdio; docs list every tool; skills and agent files in sync; output-size budgets; guard tests that break the judge on purpose and demand the evals notice.
**Results:** `python -m placemap eval run`: 160 self-written tasks, all passing (`evals/reports/baseline.json`); **0 real cases** (`evals/real/` is empty). On the self-written places (mine-main: 28 landmarks, role-lab: 9) precision is 35/35 = 1.00 and recall 35/37 = 0.95 (the borderline vendor and the spawn pad stay unselected by design); this shows the tool agrees with its author, **not** real-world precision.
**Not verified:** anything in real Studio or against the real hub (the wrapper the hub puts around results, `list_roblox_studios`, `search_game_tree` and `script_read` shapes are assumed), the Luau on a real Luau VM (the mock is Lua 5.4, plain-Lua compatible code only), the Studio/hub output-size limits (the caps are placeholders), performance on places with hundreds of thousands of instances, the script reader on real-world code styles beyond the cases tested,
the hashing-embedding fallback against a real embedding model (optional `[embeddings]` extra not wired to search), and client configs in `adapters/mcp-clients/` against live clients.

## Limitations

- Script analysis is a lexer plus regular expressions: dynamic requires, remotes held in tables, `Instance.new("RemoteEvent")` created at run time and code in other places are not seen; unresolved items are reported, not guessed.
- Roles are heuristics over structure. A repeated, interactive object can look like a harvestable node or a collectible until its scripts or attributes say which (that confusion is why borderline candidates are asked about, not decided).
- Snapshots are point in time: the answer is only as fresh as the last refresh (the tool flags stale ones, it cannot know about edits made since).
- References by name treat any instance with the same name as the same thing; generic names (`Part`, `Folder`, `Model`, `Handle`...) are ignored as references.
- The collector skips plain leaf Parts past a per-parent cap and stops at an instance cap (both reported); very large places need partial refreshes (`roots`).
- Positions are the Part position, or the model pivot, or the first descendant's position; proximity ignores rotation and size.

## Connecting agents

Canonical: `AGENTS.md` and `skills/place-map-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (a test checks drift).

| Client | Instructions | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

None tested against live clients. Add Roblox Studio's own MCP server (or the hub) to the same client; the Luau from `plan_refresh` is meant for its `execute_luau`.

## Shared machinery and `guide-core`

All shared machinery (dry-run `Plan`, feedback store, retrieval, eval runner and compare, the Luau lint and mock, MCP kit, config, scope and the `projects.yaml` registry, parameters, observation log, proposals, skill export, telemetry) comes from the installed **`guide-core`** library. **Install guide-core first**
(`pip install -e /path/to/guide-core`; this package declares it as a dependency). **All code in `placemap/` imports it only through `placemap/guide_adapter.py`** (a test greps the package to enforce this). The vendored core the scaffold creates was removed.
Workarounds kept local, as guide-core follow-ups: the mock DataModel lacks scripts, models, parts with positions, prompts and `GetPivot` (`placemap/domain/mock_extra.py` patches its Lua prelude text and fails loudly if guide-core changes it); `ParamStore` has no helper for "one labelled step on many parameters" (done in `domain/labels.py`);
guide-core's `Scope` and `ProjectRegistry` validate ids with `match` and `$` (a trailing newline passes), so `domain/places.py` re-checks with `fullmatch`; the standard `eval run` CLI reads only `evals/tasks` (the split report is the `eval-report` command here).

## Tunable parameters and skill export (guide-core learning layer)

Every threshold, weight, band and limit is a named guide-core parameter (`placemap/learning_params.py`, **derived from `rules/limits.yaml` and `rules/roles.yaml` so the defaults are the values already there**): `weight.<role>.<feature>` and `band.<role>.confident|candidate` for each role (including a project's own roles),
`feature.repetition_c|repetition_jaccard|token_k|proximity_distance`, `learning.eta|weight_limit`, `snapshots.stale_hours|keep_older`, `collector.*` caps, `search.*`, `output.max_findings`. Each has a range, a bounded step and a version history; values may differ per project or place;
a rollback restores the previous version exactly. The Roblox- or Studio-dependent ones carry `verify_against_current_docs: true`. All are placeholders. Not parameters: the role feature structure, the secret patterns and the wording of answers.

`python -m placemap export-skill [--scope global|<project_id>] [--place ID] [--out DIR] [--write]` generates a short skill folder with the tools, workflow, verified and unverified limits, the scope's current corrections, and the parameter values with versions. Dry run unless `--write`; it refuses an unregistered project or place.
There is no MCP tool for it (a maintenance command; a tool would add to every client's context).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `project_id is required` / `place_id is required` | Pass both (or `studios` for reads); the message lists the known places |
| `refusing: the open Studio instance(s) ... are not place ...` | Open the right place in Studio and pass a fresh `list_roblox_studios` output as `studios` |
| `refusing: the data was collected in ...` | You are ingesting another place's capture; use that place's id |
| `no snapshot has been stored for this place yet` | `plan_refresh`, run its Luau with the hub, then `ingest_snapshot` with `dry_run=false` |
| `snapshot is Nh old ... STALE` | `plan_refresh` (partial: `roots=[...]`; incremental by default) |
| `nothing to store: no instances were recognised` | The shape is not one of those in `samples/README.md`; save a capture there and read the warnings |
| a role search returns nothing selected | Look at `not_selected` (borderline) and `list_candidates`; confirm with `label_landmark` |
| Luau mock tests skipped | `pip install -e ".[simulate]"` |

## Repository map

`placemap/` (`guide_adapter.py` the only door to the installed `guide-core`; `learning_params.py`; `evalkit.py`; `domain/`: `scan` (script reader), `collector` (Luau generator), `mock_extra`, `ingest`, `snapshot`, `cache`, `index`, `search`, `graph`, `diff`, `roles` (features and scoring), `labels`, `view`, `multi`, `places`, `secrets`, `util`, `schema`)
| `rules/` `roles.yaml`, `synonyms.yaml`, `limits.yaml`, `secrets.yaml` (placeholders) | `style/` | `projects.yaml` + `projects/` (per-project synonyms and roles; synthetic) | `examples/places/` synthetic places and ground truth | `samples/` what to capture, plus synthetic samples |
| `evals/` tasks, budgets, baseline, `real/` | `feedback/` | `skills/` | `adapters/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your data.
