# samples/: the hub outputs this repository parses

**No real hub capture exists in this environment.** Every parser here is **`schema_unverified`**: it was written against a documented shape (below) and against SYNTHETIC samples the builder wrote
(`samples/synthetic/`, labelled as such). The tool output says so too (`parser: schema_unverified` in every answer, `parser_status` in `ingest_snapshot`). Until you save real captures here and a
test pins them, assume the real shapes can differ and read the `warnings` that `ingest_snapshot` returns (run it with `dry_run=true` first).

## What to run, and where to save the result

| What this repository parses | Hub call to run | Save the result to | Then |
|---|---|---|---|
| The open Studio instances (used to match the open place to the named one) | `list_roblox_studios` (no arguments) | `samples/hub/list_roblox_studios.json` | pass it as `studios` to `plan_refresh` (and `find_in_place` if you want the open place to be the active one) |
| The tree and script sources, collected by the Luau that `plan_refresh` returns | `roblox_studio_execute_luau` with the `luau` field of `plan_refresh`, in Studio edit mode | `samples/hub/execute_luau-collect.json` (the returned JSON text; with `emit=print` the printed lines) | `ingest_snapshot` with `file=samples/hub/execute_luau-collect.json` (dry run first) |
| A cheaper tree-only read | `roblox_studio_search_game_tree` on the root you care about (for example `Workspace`) | `samples/hub/search_game_tree.json` | `ingest_snapshot` with `source=search_game_tree`; it reads whatever tree shape it recognises and lists what it ignored |
| One script's source | `roblox_studio_script_read` for one script path | `samples/hub/script_read.json` | `ingest_snapshot` with `source=script_read`; it updates the latest snapshot |

`ingest_snapshot` only reads files under `workspace/`, `samples/` or `examples/` (or a folder named in `PLACEMAP_INGEST_PATHS`). Do not put secrets in a saved capture: attributes and strings that look like keys
or tokens are dropped when parsing, but that is a heuristic, not a guarantee.

## Documented shapes (what the parsers expect)

* **`placemap-collect/1`** (this repository's own format, produced by the collector Luau): `{"format": "placemap-collect/1", "collector_version": 1, "place": {"name", "place_id", "game_id"}, "taken_unix": N,
  "roots": [...], "full": bool, "truncated": bool, "skipped": {...}, "nodes": [{"p": path, "c": class, "a": {attrs}, "t": [tags], "v": [x, y, z], "n": childCount, "rc": "Server"|"Client",
  "s": {"fp": "<bytes>:<hex>", "len": N, "src": "...", "cut": bool}}]}`. Proven on the guide-core mock DataModel (the Luau ran and its output parsed); **the wrapper the hub puts around an
  `execute_luau` result is unknown**, so a mapping with `result`, `output`, `content`, `stdout`, `text` ... is unwrapped heuristically and flagged.
* **generic tree**: nodes with `Name`/`ClassName`/`Children`/`Attributes` (also `name`, `class`, `children`, `attributes`, `path`/`FullName` spellings), as a nested tree or a flat list. Best effort.
* **text outline**: `path | Class | key=value; key=value` per line, or an indented outline `Name [Class] key=value`.
* **script_read**: `{"path" | "FullName", "source" | "Source"}`, a list of them, or text blocks `=== path ===` followed by the source.
* **list_roblox_studios**: a list of `{name | placeName | title, place_id | placeId, studio_id | studioId | id}` (guide-core's `match_open_studio`; also `schema_unverified`).

## `synthetic/`

Written by `scripts/make_examples.py` (never captured from the hub): `collect-tycoon.json` (collector format), `tree-generic.json` (a generic tree wrapped in `{"result": ...}`), `tree-outline.txt`,
`script-read.json`, `script-read.txt`. The synthetic places the evals use live in `examples/places/`.

When you have real captures: drop them in `samples/hub/`, run `ingest_snapshot` on them, and tell the builder (or add a test) so the parser can be pinned to the real shape and the `schema_unverified` flag removed.
