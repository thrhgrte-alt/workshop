# Places example (synthetic)

Two places of the synthetic project `example-obby`, laid out the way `projects.yaml` expects (a sub-folder per place). Used by the evals to check that
`review_folder` labels every finding with its place and applies each layer of the ruleset only where it belongs.

| File | Place | Contains |
|---|---|---|
| `lobby/Door.server.luau` | lobby | API001 (`wait`) |
| `stage-1/Pad.server.luau` | stage-1 | API002 (`spawn`) and a PERF005 question (Touched without debounce) |

`example-obby` suppresses API001 in all its places (see `projects/example-obby/ruleset.yaml`); `stage-1` raises PERF005 to a warning. `example-tycoon` has no such suppression.
