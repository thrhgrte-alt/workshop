# Thresholds for the playtest checks (PLACEHOLDERS)

`style.yaml` is the machine-readable version of this file. **Every number in it is a PLACEHOLDER.** The user has not supplied pacing, performance budgets or timing limits,
so the starter values only give the checks something to compare against. Replace them, per place, in `projects/<project>/<place>/playtest.yaml` under `overrides:` (or tune
them through guide-core's improvement loop; see README "Learning"). Nothing in it is an official Roblox number; the two pathfinding agent defaults mirror Roblox's documented
defaults and carry `verify_against_current_docs: true`.

| Area | What a pass needs |
|---|---|
| boot | no error lines (locked at 0) and no warning lines in the first `boot_window_seconds`; expected services and folders exist |
| spawn | a character exists within `spawn_timeout_seconds`, is alive above `spawn_void_y`, stands on a valid surface, and covers `spawn_min_move_fraction` of `spawn_probe_distance_studs` in `spawn_probe_seconds` |
| reachability | every named area has a `Success` pathfinding result from the spawn with at least `reach_min_waypoints` waypoints ending within `reach_end_gap_studs` of the target (a flood fill over the results, so hops through other areas count when the hop mode is on) |
| remotes | a valid call answers within `remote_timeout_seconds` without error; each bad-input probe is rejected (falsey or `{ok=false}` result) and raises no error |
| economy | each step's watched values change by the configured delta (exact within `economy_delta_tolerance`, or a min/max, or a sign); no balance below `economy_min_balance`; no step beyond `economy_max_abs_delta` |
| data | TEST MODE only: save, reset, load returns the saved values |
| performance | part count, script count, memory, frame time against the stored baseline, within the `perf_*` percentages; memory growth over the run within `perf_memory_growth_pct_over_run` |

Flaky checks get `flaky_repeats` runs; a failure is called only when `flaky_confirm_fail_fraction` of them fail, otherwise the check is reported as FLAKY with the counts.
