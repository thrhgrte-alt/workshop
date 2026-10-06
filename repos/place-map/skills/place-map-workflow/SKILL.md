---
name: place-map-workflow
description: Answers questions about a Roblox place from a saved snapshot instead of re-exploring it - where something is, which instances play a role (vendor, collectible, spawn, zone, gate ...), what fires or handles a remote, what depends on a module, what changed since the last snapshot, and how two places differ - for one named place at a time. Use when the user asks where something is in the game, what breaks if a module changes, who uses a remote, to refresh or diff the place map, or to confirm which objects are vendors, collectibles or spawns.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .) and guide-core. Tools come from the MCP server "place-map" or `python -m placemap call`. Collecting fresh data needs Roblox Studio with its MCP server (execute_luau) connected to the same agent; searching a saved snapshot does not. The Luau check on the mock needs lupa.
metadata:
  version: "0.1.0"
  domain: place-map
---

# Place map workflow

Follow in order. Detail is in `references/`; open one only when needed. **Quote scores, hashes, paths and snapshot ages from tool results; never compute them.**

## 1. Fix the scope
- Ask which **project and place** if not stated. Every tool needs `project_id` and `place_id` (or the hub's `list_roblox_studios` output as `studios`, which makes the place open in Studio the active one). A refusal lists what is known. Never guess.
- `find_past_corrections` with the request first; treat past corrections as requirements.

## 2. Ask the map
- `find_in_place` for a path, a name, a role ("the vendors") or text. The first field is a one-line summary with the snapshot age; **say so when it says STALE** and offer `plan_refresh`.
- A request that **names a thing returns that exact path only**; near matches come back as `not_selected`. If several things share the name, or candidates are borderline, **ask the user**. Never widen the match yourself.
- Detail: `get_path_info` (attributes, script summary, roles with scores), `who_uses` (what breaks if a module changes), `show_dependencies`, `list_remotes` (who fires, who handles, which remotes nobody uses), `summarize_area`, `diff_snapshots`.
- Several places: `find_across_places`, `compare_places`, `find_shared_code` (drift by content hash). Never use `find_in_place` for a cross-place question; every cross-place hit carries its place prefix.

## 3. Refresh when stale or missing
- `plan_refresh` with `studios` = the output of the hub's `list_roblox_studios`. **It refuses when the open Studio place is not the named one**; tell the user which place is open, do not retry with another place.
- Run the returned `luau` with `roblox_studio_execute_luau` in edit mode, then `ingest_snapshot` (dry run first, read `warnings`, then `dry_run=false`). Partial refresh: `roots=["Workspace/Vendors"]`. Parsers are **schema_unverified**: say so if a warning mentions the wrapper.

## 4. Roles and labels
- Roles come from structure (interaction objects, remotes, attributes, repetition), not names. Weights and bands are **placeholders** until the user confirms landmarks.
- `list_candidates` shows borderline instances. Ask the user; record the answer with `label_landmark` (`confirmed_by` = their name; dry run first). It nudges that project's weights a small, bounded step and is undoable with `undo_label`. Mark `mark_global` only if the user says so.

## 5. Learn
- `record_run` + `record_decision` with the user's words and a dimension from `style/style.yaml` (`role`, `landmark`, `naming`, `search`, `summary`, `scope`). `is_global` only when the user says it applies to every place.

## 6. Report
One line first: what was found, in which place, how old the snapshot is. Then the ranked results (at most 10), what was not selected and why, and what was not checked.

## Hard rules
- Never connect to Studio yourself, never publish, never run `plan_refresh` output in a place other than the named one, never reuse a `studios` list from an earlier session.
- Never record a label the user did not give. Never present a placeholder weight or band as measured.
- Self-written evals only show the tool agrees with itself; real results need `evals/real/`.

References: `references/workflow.md`, `references/snapshot-format.md`, `references/roles.md`, `references/multi-place.md`.
