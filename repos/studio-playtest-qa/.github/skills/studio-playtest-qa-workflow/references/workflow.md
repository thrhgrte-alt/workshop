# Workflow in detail

```
list_roblox_studios (hub) -> list_checks -> plan_playtest(studios)
   for each session in the plan:
      roblox_studio_start_stop_play start
      for each run: generate_check_script(dry_run=false) -> execute_luau -> save the raw answer
      get_console_output -> save the text
      screen_capture (only on failure) -> save the path
      roblox_studio_start_stop_play stop
   per check: explain_failure(check_id, results, console_log, screenshots) -> record_result(dry_run=false)
```

## Why flaky checks are repeated
`remotes`, `economy_smoke` and `perf_snapshot` are flaky by default (timing, shared state, noisy measures); a place can mark others with `flaky:` in its playtest.yaml, or release one with `not_flaky:`.
N = `flaky_repeats` (placeholder 3). The verdict over the runs:

| runs that failed | conclusive runs | verdict |
|---|---|---|
| 0 | any | pass (with a note if fewer than N ran) |
| >= 1 | fewer than N | INCONCLUSIVE: re-run |
| share >= `flaky_confirm_fail_fraction` (placeholder 1.0) | N or more | FAIL |
| below that share | N or more | FLAKY |

A stable (non-flaky) check fails on one failing run. Unreadable answers and refusals are not counted as passes or failures.

## What a pass says
`checked` lists, with numbers, what was measured (for example "3 of 3 named area(s) reached", "0 error(s) and 0 warning(s) in the first 10 s from the first time stamp"). `not_checked` lists what the check never covers plus what was not evaluated in this run (for example "no console_log was supplied").

## Refusals you will meet
- missing or unknown project_id / place_id; a place with no `profiles.playtest` in projects.yaml;
- the open Studio place is not the named one, or two open places match, or no `studios` list was given;
- a check the place does not configure; a data check without `data.test_mode.switch`;
- a script that stopped itself at run time (wrong place; test-mode switch off; the game did not acknowledge test mode): verdict REFUSED, nothing was checked.
