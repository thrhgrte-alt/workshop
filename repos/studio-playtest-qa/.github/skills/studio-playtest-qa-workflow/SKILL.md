---
name: studio-playtest-qa-workflow
description: Runs a scripted playtest of ONE named Roblox place in the open Studio session and judges it with evidence - boot errors and warnings, spawn, reachability of named areas, remotes, an economy smoke test, a save/reload round trip in TEST MODE, and a performance snapshot against a stored baseline. Generates assertion Luau and a run plan for the hub, parses the answers, re-runs flaky checks before calling a failure. Use when the user asks to playtest, QA, smoke-test or regression-check a place, to check that a room is reachable, a remote works, the economy still adds up, or performance has regressed.
compatibility: Needs Python 3.10+ with this repository and guide-core installed (pip install -e .). Tools come from the MCP server "studio-playtest-qa" or `python -m playqa call`. Running a plan needs Roblox Studio's MCP hub (list_roblox_studios, roblox_studio_start_stop_play, execute_luau, get_console_output, screen_capture) connected to the same agent; the tool itself never connects to Studio. The Luau mock needs lupa.
metadata:
  version: "0.1.0"
  domain: playtest-qa
---

# Studio playtest QA workflow

Follow in order. Detail is in `references/`; open one only when needed. **This tool never runs anything: the hub does. Never claim a check ran without the hub's answers.**

## 1. Fix the scope and the open place
- Ask which **project and place** if not stated. Every tool needs `project_id` and `place_id`; a refusal lists the known places. Never guess.
- Call the hub's `list_roblox_studios`. Pass its output as `studios` to `plan_playtest` / `generate_check_script`; they refuse unless exactly one open place is the named one. Never reuse an old list.
- `list_checks`: which checks can run for this place and which are flaky. A check the place does not configure, or a data check without a test-mode switch, is listed with the reason, never invented.
- `find_past_corrections` with the request. Treat past corrections as requirements.
- Say plainly: every threshold is a **placeholder** and every hub-output format is **schema_unverified** until the user supplies real ones / real captures.

## 2. Plan and generate
- `plan_playtest(studios=...)`: play sessions, the hub calls in order (`roblox_studio_start_stop_play`, `execute_luau`, `get_console_output`, `screen_capture`), and N runs for each flaky check. Run it in the OPEN Studio session only, never a live server.
- For each `execute_luau` step: `generate_check_script(check_id, repeat_no, phase, studios, dry_run=false)` and run its `luau`. The script stops itself in any other place. Save each raw answer to the file the plan names.

## 3. Collect
- After each session: `get_console_output` (save the text), and `screen_capture` only if an assertion failed or the console shows an error (save the image, keep the PATH).
- A data check runs only in TEST MODE: the user turns the switch on in the Studio session; the script refuses without it. Never ask to turn it on in the published place.

## 4. Judge
- `explain_failure(check_id, results=[one raw answer per run], console_log, screenshots)`. Read the one-line verdict, then each failure: the check, the **evidence** (log lines, measured value, screenshot path), the **most likely cause** (a heuristic: say so) and the next step. A pass lists exactly what was checked and what was not.
- `FLAKY` (some runs failed) and `INCONCLUSIVE` (fewer than N runs, an unreadable answer, no baseline) are not failures. Re-run, do not call them bugs.
- Performance: `save_baseline` after a run the user accepts, then `explain_failure` (or `compare_baseline`) on later runs. Without a baseline nothing is compared and the verdict says so.

## 5. Record and learn
- `record_result(dry_run=false)` stores the verdict; it flags a stable check whose stored results mix pass and fail (a candidate to mark flaky, never applied).
- `record_run` + `record_decision` with the user's words and a dimension (`threshold`, `cause`, `flaky`, `evidence` ...). A user who says "that limit is too tight" gives a correction, not a code change: thresholds change only through guide-core's gated, approved loop. `mark_global` only if the user says it applies to every place.

## 6. Report
One line verdict, the failures with evidence and cause, what was checked and what was not, open questions. State the project and place, that thresholds are placeholders, that hub formats are unverified, and what ran (and did not) in real Studio.

## Hard rules
- Never run against a live server or the published place; never write to a real DataStore; never publish.
- Never call a failure before the flaky runs are in; never call a heuristic cause a diagnosis.
- Never change a threshold yourself; never hide ignored console lines (they are counted).

References: `references/workflow.md`, `references/checks.md`, `references/config-format.md`, `references/result-format.md`, `references/hub-calls.md`.
