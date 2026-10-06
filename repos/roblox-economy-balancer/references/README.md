# References and verification log

This repository is built from the project's own build instructions, the vendored suite kit and the libraries listed below. **No Roblox documentation, Roblox API snapshot or player data was read or used**: the Roblox facts it relies on
(`Instance.new("ModuleScript")`, writing `Script.Source`, `game.PlaceId` and `game.Name`, `Instance:GetAttributes()`, Value objects, `HttpService:JSONEncode`, the shape of Studio MCP's `list_roblox_studios` output, `execute_luau`) come from the
build instructions and general knowledge and are **unverified**. Do not invent URLs here; add a source only after it has been read.

## Sources actually read
| Source | Used for |
|---|---|
| `roblox-repo-build-instructions.md` (the user's build brief, both versions) | requirements, tool names, shared rules 1-13, per-place scoping, token discipline |
| `suite/kit/core` and `suite/kit/tests` (was vendored into `econbal/core` and `tests/core_suite`; now the installed `guide-core` library) | feedback store, versioning, eval runner, MCP kit, style loading, path scope |
| `repos/modular-set-dressing-ai`, `repos/concept-art-ai` (earlier repos of the suite) | the pattern to follow (hooks, tools, evals, docs) |

## Verification log
| Item | How verified | Date |
|---|---|---|
| Simulator purchase minutes | 27 hand-derived tasks in `evals/tasks/hand_computed.yaml`; an independent exact-fraction paper model on 60 random ladders; a naive minute stepper on the toys (`tests/test_domain.py`) | 2026-10 |
| Conservation of currency (sources - sinks = balance change, every day) | property test on the toys, with tax and upkeep | 2026-10 |
| Toy economies: balanced passes, wall/runaway/dominant flagged | `evals/tasks/toy_economies.yaml`, `tests/test_domain.py` | 2026-10 |
| Locked values never changed by a proposal | property test over several lock patterns, evals | 2026-10 |
| Generated Luau: lint, injection-safe names, place guard, no overwrite | lint and unit tests; run on the bundled mock DataModel with lupa 2.8 (Lua 5.5) | 2026-10 |
| Import dump and normaliser | mock tree + hand-written JSON | 2026-10 |
| MCP server | in-memory session and a real stdio subprocess (`suite/smoke_stdio.py`), mcp 1.28.1 | 2026-10 |
| Environment | Python 3.11.15, Pillow 12.3.0, PyYAML 6.0.1, pytest 9.1.1 | 2026-10 |
| Luau inside real Studio; writing `Script.Source` from `execute_luau` | **Not verified** (mock only) | - |
| `list_roblox_studios` output shape (`name`, `place_id`, `studio_id` and aliases) | **Assumed**; the matcher accepts several spellings | - |
| Real game values, real archetypes, real playtests, retention, revenue | **Not verified, not modelled** | - |
| matplotlib backend | **Not run** (matplotlib not installed here) | - |
| Client config files in `adapters/mcp-clients/` | **Not tested against live clients** | - |
