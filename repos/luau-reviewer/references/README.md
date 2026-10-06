# References and verification log

**Honest status: no external documentation or source was read while building this repository.** There is no network access in the build environment. The rules, the deprecations and the backend
output formats are written from the author's general knowledge of Roblox, Luau, `luau-analyze`, `selene` and `stylua`. Nothing below is a citation; no URL is given because none was consulted.

## What was read
| Item | How | Date |
|---|---|---|
| The build instructions for this repository (two versions: the original and the update with `guide-core`, projects/places and token discipline) | read in full | 2026-10 |
| The structure of the sibling repositories `concept-art-ai` and `modular-set-dressing-ai` and the shared kit (then vendored in `luaurev/core`, now the installed `guide-core` library) | read in full | 2026-10 |
| `guide-core` and its spec file (`roblox-support-repo-build-instructions.md`) | was not available at first build; now installed and used through `luaurev/guide_adapter.py` | 2026-10 |

## What is assumed and must be checked by you
| Claim | Status |
|---|---|
| `luau-analyze` prints `path(line,col): Kind: message` and `Kind` is `SyntaxError`, `TypeError` or a lint name | author's memory; parsed and tested against **stub programs only** |
| `selene --display-style=json2` prints one JSON diagnostic per line with `primary_label.span.start_line` zero-based; the quiet style is `path:line:col: severity[code]: message` | author's memory; stub-tested only |
| `stylua --check` prints `Diff in <file> at line N:` and exits non-zero when a file is not formatted | author's memory; stub-tested only |
| `wait`, `spawn`, `delay`, `tick`, `table.getn`, `Humanoid:LoadAnimation`, the lower-case `connect`, body movers and `Instance.new(class, parent)` are deprecated or discouraged | author's knowledge; **verify against current Roblox docs** |
| `ProcessReceipt` must return an `Enum.ProductPurchaseDecision`; DataStore calls can throw and are throttled; BindToClose gives a short window to save; `loadstring` is disabled by default | author's knowledge; verify |
| 30 s autosave floor (DAT005), file and folder limits, similarity threshold 0.6 | heuristic defaults in `rules/` (`verify_against_current_docs`), not Roblox limits |
| Where each MCP client reads its configuration | follows the sibling repositories; not tested against live clients |

## What has been run
Everything in `tests/` and `evals/`: fixtures in `examples/`, stub backends, an in-memory MCP session and a real stdio server start. No real `luau-analyze`, `selene` or `stylua`, no real game, no Roblox Studio.
If a real backend is installed on the machine running the tests, extra tests run it (they are skipped otherwise). In the build environment none was installed, so none of those ran.
