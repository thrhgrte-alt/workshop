# The check library (rules/*.yaml)

| check id | kind | context (assumed) | session | flaky | what it asserts |
|---|---|---|---|---|---|
| `boot` | boot | server | shared | no | expected services and paths exist; no error lines and no warning lines in the first N seconds of the console (and no error in the LogService history) |
| `spawn` | spawn | server | shared | no | a player and character exist, health above 0, above the void line, a surface under the character that is not on the invalid list, and the character covers the required share of a MoveTo probe |
| `reachability` | reachability | server | shared | no | from the spawn every named area has a usable Success path (flood fill over the pathfinding results) ending near the target |
| `remotes` | remotes | client | shared | yes | each remote exists with its class, a valid call answers in time without error, each bad input is rejected without error |
| `economy_smoke` | economy | server | own | yes | each scripted step changes the watched values by the configured delta/range/sign; no currency below the floor; no step beyond the runaway cap |
| `data_roundtrip` | data | server | own | no | TEST MODE only: save, reset, load returns the saved markers |
| `perf_snapshot` | perf | server | own | yes | part count, script count, memory, frame time at the start and after N minutes stay within a percentage of the stored baseline; memory growth over the run is bounded |

`context` is an assumption about where `execute_luau` runs the code (schema_unverified). `session: shared` checks of the same context share one play session; `session: own` checks get a fresh session per repeat.
Every limit is a placeholder in `style/style.yaml` (registered as a named parameter). Each file in `rules/` lists the assertion ids the check can emit and what it does NOT check; `rules/causes.yaml` maps failures to causes; `rules/log_patterns.yaml` classifies console lines.
