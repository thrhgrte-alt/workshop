# studio-playtest-qa

Closes the loop on everything the other guide repositories built, none of which was ever run in real Studio: it **generates assertion Luau and a run plan**, the hub runs them in the OPEN Studio session
(`roblox_studio_start_stop_play`, `execute_luau`, `get_console_output`, `screen_capture`), and this tool **parses what comes back** and reports **with evidence**. It is a CLI plus a stdio MCP server exposing the same functions.
It never connects to Studio, never publishes, never touches a live server, never writes to a real DataStore.

> **Read this first.**
> - **Never run against real Studio, the real hub or a real game.** Everything below was exercised on a *mock world* (a fake place in Lua, run by `lupa`) with planted faults. That proves the generated Luau is coherent against the mock and that the evaluators catch the planted faults. It does not prove that the scripts run in Studio, that the hub returns what the parsers expect, or that the checks find anything in your game.
> - **Every parser of hub output is `schema_unverified`.** No real capture of `execute_luau`, `get_console_output`, `screen_capture`, `list_roblox_studios` or `roblox_studio_start_stop_play` exists here. The formats are documented as assumptions, tested against **synthetic** samples (`samples/`), and labelled `schema_unverified` in this README and in tool output. A wrong guess shows up as INCONCLUSIVE, not as a pass.
> - **Every threshold is a placeholder.** The user has not given pacing, budgets or timing limits. `style/style.yaml` holds starter numbers so the checks have something to compare against; the economy smoke ranges in particular (`economy_expect_gain|spend|unchanged`) are not your pacing.
> - **The evals only show the tool agrees with itself.** The 153 evals in `evals/tasks/` were written by the builder next to the code and are reported as *self-written*. `evals/real/` ships empty (0 real cases): see its README for how to add examples that were not written by the builder.

## How it works

```
list_roblox_studios (hub) --> plan_playtest(studios) --> sessions + hub calls in order, N runs for each flaky check
generate_check_script --> assertion Luau (lint-clean, place-guarded) --> hub: execute_luau --> raw answer
hub: get_console_output --> console text        hub: screen_capture --> image path (only on failure)
explain_failure(check, results, console, screenshots) --> verdict first; failures with evidence + most likely cause; a pass states what was checked
record_result / record_run / record_decision (dry run first) --> per-place store and the shared learning layer
```

Rules (all enforced in code and tests): runs only in the open Studio session on the user's place; the open place is matched to the named project/place before any script is generated, and a wrong, missing or ambiguous match is refused;
a data check is refused unless a test-mode switch is declared (and the script refuses at run time unless it is on); every failure carries the check, the evidence (log lines, measured value, screenshot path) and the most likely cause (a heuristic, labelled as one);
a pass states exactly what was checked and what was not; a **flaky** check is re-run N times before a failure is called.

## Install

```bash
pip install -e /path/to/guide-core        # the shared library; it is a local repository, not on PyPI
pip install -e ".[dev]"                   # pytest, lupa (the Luau mock)
python -m playqa doctor                   # what works on this machine
```

All shared machinery (dry-run plans, scope and the projects registry, the open-Studio match, scoped feedback, retrieval, the eval runner, the mock DataModel, the Luau safety lint, MCP helpers, the learning layer) comes from **guide-core** and is imported
only through `playqa/guide_adapter.py` (a test greps the package to enforce it). Nothing is re-implemented here.

## Tools

Every tool takes `project_id` and `place_id` and **refuses without both** (and without a registered pair); every answer starts with a one-line `summary`, states the project and place, ranks findings and caps them at 10 (`detail=true` for more).
Write tools default to `dry_run=true`. Tools that need the open Studio place take `studios` (the output of the hub's `list_roblox_studios`, `schema_unverified`) and refuse unless exactly one open place is the named one.

| tool | label | what it does |
|---|---|---|
| `list_checks` | read-only | the 7 checks, which can run for this place, which are flaky, why one cannot run |
| `plan_playtest` | read-only | play sessions, hub calls in order, N runs per flaky check; needs `studios` |
| `parse_console_log` | read-only | console text -> ranked error/warning findings, optional boot window or per-check cut (`schema_unverified`) |
| `compare_baseline` | read-only, `rare` | a performance run against the stored baseline, per measure, against placeholder limits |
| `explain_failure` | read-only | the verdict, the evidence and the most likely cause for one check over all its runs |
| `find_past_corrections` | read-only | corrections saved for this place (and global ones) relevant to a request |
| `generate_check_script` | write (dry run default) | the assertion Luau for one run of one check; refuses a data check without a test-mode switch; needs `studios` |
| `save_baseline` | write (dry run default), `rare` | keep a performance run as this place's baseline (versioned) |
| `record_result` | write (dry run default) | judge and store a check's result in this place's workspace; flags a stable check that mixes pass and fail |
| `record_run` | write (dry run default) | save a request and its outputs (feedback + local observation log) |
| `record_decision` | write (dry run default) | save the user's verdict and corrections; `mark_global` only when asked |
| `promote_run` | write (dry run default), `rare` | add a reviewed run to this place's library (candidate unless `confirm=true`) |

**Tool groups.** `PLAYQA_DISABLE_GROUPS=rare` leaves out `compare_baseline`, `save_baseline` and `promote_run` so the day-to-day tools stay small in the model's context. `PLAYQA_TELEMETRY=1` records output sizes and timings locally (never uploaded).
**Token discipline.** One-line verdict first, findings ranked and capped at 10, details on request, descriptions at most 300 characters, and one output-size budget eval per tool (`evals/tasks/budgets.yaml`).

## The check library (`rules/*.yaml`)

| check | what a pass means (exactly) |
|---|---|
| `boot` | the expected services and paths exist; 0 error lines and 0 warning lines in the first N seconds of the console (N is a placeholder, 10); the LogService history shows no error (secondary evidence) |
| `spawn` | a player and character exist, alive, above the void line, standing on a surface not on the invalid list, and covering the required share of a MoveTo probe (not stuck) |
| `reachability` | from the spawn every named area has a usable `Success` path (flood fill over Studio's pathfinding results, optionally through other areas) that ends within the allowed gap of the target |
| `remotes` | each listed remote exists with its class; a valid call answers in time without error; each bad input (configured + generic probes: wrong types, NaN, inf, negative, huge string ...) is rejected without error |
| `economy_smoke` | each scripted step (mine, sell, buy) changes the watched values by the configured delta, range or sign; no currency goes below the floor; no step moves a value beyond the runaway cap |
| `data_roundtrip` | **TEST MODE only**: saved markers come back after reset and load. Refused without a test-mode switch; the script contains no DataStore call |
| `perf_snapshot` | part count, script count, memory and frame time at the start and after N minutes stay within a percentage of the stored baseline, and memory growth over the run is bounded |

Each file lists the assertion ids a check can emit and what it does **not** check (a test requires every emitted id to be declared). Details: `skills/studio-playtest-qa-workflow/references/checks.md`.
Reachability reuses the *idea* of roblox-level-design-ai's `walkcheck` (a flood fill) but works on Studio's pathfinding results through this repository's own result schema; no code is imported from it.

## Configuration

`projects.yaml` (shared guide-core format) lists projects and places; each place names its `playtest.yaml` (what to test: expected paths, areas, remotes, economy steps, data hooks, test-mode switch) under `profiles.playtest`. The three shipped projects are **synthetic**
(`demo_mine/main`, `demo_tycoon/main`, `demo_obby/main`); `demo_tycoon` declares data hooks without a test-mode switch on purpose (the data check must be refused) and `demo_obby` has only three sections. Format: `skills/studio-playtest-qa-workflow/references/config-format.md`.
Hooks named in a `playtest.yaml` (a Bindable function for "sell", "save", "load", a test-mode switch) are things **your game must provide** for QA.

## Result and hub formats (`schema_unverified`)

Scripts return one JSON string, `playqa.result/1` (documented in `playqa/domain/schema.py` and `references/result-format.md`), and also print it after `PLAYQA_RESULT` with `PLAYQA_MARK` / `PLAYQA_PROBE` lines so the console can be cut per check and per probe.
What the parsers accept from the hub is listed there; `samples/README.md` says exactly which hub call to run (`execute_luau`, `get_console_output`, `screen_capture`, `list_roblox_studios`, `roblox_studio_start_stop_play`) and where to save each output so the parsers can be checked against a real one.
Assumptions to verify: where `execute_luau` runs (server or client context: the `remotes` check needs a client), whether it returns the script's return value or only its output, how the console marks levels and time stamps, what `list_roblox_studios` calls its keys, that `LogService:GetLogHistory`, `Stats:GetTotalMemoryUsageMb` and the pathfinding status names exist as used (`verify_against_current_docs: true` in `rules/`).

## Flaky checks

`remotes`, `economy_smoke` and `perf_snapshot` are flaky by default; a place can add or release checks (`flaky:` / `not_flaky:` in its `playtest.yaml`). `plan_playtest` plans N runs (`flaky_repeats`, placeholder 3); `explain_failure` aggregates:
no failure -> pass; failures but fewer than N conclusive runs -> **INCONCLUSIVE** (re-run); N or more with the failing share at least `flaky_confirm_fail_fraction` (placeholder 1.0) -> **FAIL**; otherwise **FLAKY**. A stable check fails on one failing run. An unreadable answer is INCONCLUSIVE, a script that declined to run is REFUSED.

## Learning (and its honest limits)

Every setting and acceptance range in `style/style.yaml` is a named guide-core parameter (`setting.<name>`, `threshold.<name>.<min|max>`; 37 of them) with a default, a range, bounded steps, versions, rollback and a per-place scope; `boot_error_lines.max` is locked at 0.
`record_run` / `record_decision` feed guide-core's observation log and scoped feedback; promotion goes only through its gated, approved loop (`propose` -> `gate` -> `promote`, a person approves). `python -m playqa export-skill --scope demo_mine --place main` writes a short skill with the current values and corrections (dry run unless `--write`).
Resolution order: the place's own explicit number in `playtest.yaml` (`overrides:`) beats a learned value, which beats the shipped default. This improves only as far as the corrections and evals it is given; early on it is all defaults, and it cannot learn taste it has not been shown.

## Evals and tests

```bash
python -m pytest                                  # unit, tool, MCP, stdio, hygiene, planted-fault and vacuity-guard tests
python -m playqa eval run --label baseline        # the 153 self-written evals -> evals/reports/baseline.json
python -m playqa eval compare baseline mine       # regressions between two reports
python -m playqa eval-report --label mine         # self-written and real (evals/real) counts, kept apart
```

- **Self-written (153)**: hand-computed expectations on the mock world: clean runs that must pass and planted faults that must be caught (unreachable room, remote that errors, remote that accepts bad input, script error and warning at boot, missing folder, currency bug, doubled sale, negative balance, stuck / lava / void / missing character, path that stops short, lost data value, part bloat, memory leak, slow frames), 32 refusal cases (data check without test mode, wrong/missing/ambiguous open place, missing scope, injection attempts, unknown checks and kinds), flaky aggregation, hub answer shapes, generated-Luau safety, learning parameters and one output-size budget per tool. They show the tool agrees with itself.
- **Real: 0.** `evals/real/` is empty; reported as 0 cases, not as a pass.
- Vacuity guards: tests break the judge in several different ways (reachability, cause rules, the data refusal, the studio match, flaky handling, the lint, the economy evaluator, baseline comparison, scope guessing, a blind console parser, a mock world without faults, bloated outputs) and demand that the matching evals fail.

## Status (what was actually run)

`python -m pytest`: 149 passed (including 33 planted-fault tests on the mock world, the in-memory MCP session, a real stdio smoke test of `python -m playqa.server`, and the improvement-loop test: observe -> propose -> gate -> a person approves -> promote). `python -m playqa eval run --label baseline`: self-written 153/153, **real 0/0 (no real examples yet)**.
Tested on Python 3.11 with guide-core 0.1.0, `lupa` 2.8 and `mcp` 1.28; the source parses as 3.10. Nothing was run against Studio, the hub, Blender or a real game.

## What is verified, and what is not

| verified here (fixtures, mock world, lupa) | NEVER run against real Studio / the real hub / a real game |
|---|---|
| generated Luau passes the shared lint and runs on the mock DataModel; the same text is what the evaluators were tested on | that the Luau runs in Studio (no `execute_luau` was ever called), that its APIs behave as the mock assumes |
| the evaluators catch every planted fault and pass the clean world; hand-computed numbers | what a real console, a real `execute_luau` answer, a real `list_roblox_studios` answer look like |
| scope, studio-match and test-mode refusals; dry-run defaults; flaky aggregation; baselines; params and skill export | whether the pathfinding statuses, `LogService`, `Stats` and client/server contexts behave as assumed |
| MCP in-memory session and a real stdio smoke test of the shipped entry point | any claim about your game: the shipped places and numbers are synthetic |

Placeholders: every number in `style/style.yaml` (boot window 10 s, probe 8 studs / 3 s / 0.5 share, void y -50, ray 20 studs, end gap 4 studs, min 2 waypoints, max 12 nodes, remote timeout 3 s, 6 probes, economy cap 1,000,000 and floor 0, performance limits 10 / 10 / 25 / 25 / 20 %, N = 3 repeats, confirm fraction 1.0, cap 10 findings). The two agent defaults (radius 2, height 5) mirror Roblox's documented defaults and are flagged `verify_against_current_docs: true`.

## What you still need to supply

- Real captures of each hub output (`samples/README.md` says which call to run and where to save it) so the `schema_unverified` parsers can be checked.
- Your real places: registry entries, a `playtest.yaml` per place, and the QA hooks and test-mode switch in the game (for the economy and data checks).
- Your pacing and budgets: the economy smoke ranges, performance limits and timing numbers are placeholders until you give them.
- A handful of real examples in `evals/real/` that were not written by the builder.
- A decision on whether flaky checks should be re-run in fresh play sessions (the plan does this for `session: own` checks) or in place.

## Layout

`playqa/` (adapter, tools, hooks, learning parameters; `domain/`: config, checks, generators, evaluators, console parser, reachability, baselines, plan, mock world, harness) - `rules/` (the check library, cause rules, log patterns) - `style/` (placeholder thresholds) - `library/` (manifest schema) -
`examples/` (SYNTHETIC results, consoles, baseline, library) - `samples/` (SYNTHETIC hub outputs, with the capture instructions) - `evals/` (tasks, real/, reports) - `feedback/` - `skills/studio-playtest-qa-workflow/` - `adapters/mcp-clients/` - `projects.yaml`, `projects/` (three synthetic places) - `tests/` - `scripts/`.
`AGENTS.md` is canonical; `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md` and the skill mirrors are generated by `scripts/sync_agent_files.py` (a test fails on drift). MIT licensed (`LICENSE`, `ASSET_LICENSING.md`).
