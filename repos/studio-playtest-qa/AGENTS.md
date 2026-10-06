# AGENTS.md - studio-playtest-qa

A toolkit that lets an AI agent QA one Roblox place in the OPEN Studio session: it generates assertion Luau and a run plan, the hub (`roblox_studio_start_stop_play`, `execute_luau`, `get_console_output`,
`screen_capture`) runs them, and this tool parses what comes back and reports with evidence. It does not retrain any model, never connects to Studio, never publishes and never touches a live server.

## Setup and commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e /path/to/guide-core       # the shared library (not on PyPI); then:
pip install -e ".[dev]"                  # pytest, lupa (the Luau mock)
python -m playqa doctor                  # what works on this machine right now
python -m playqa places                  # the registry (projects.yaml)
python -m playqa checks --project-id demo_mine --place-id main      # which checks can run for a place
python -m playqa export-skill --scope demo_mine --place main        # agent skill from learned parameters/corrections (dry run; --write)
python -m playqa eval run --label x      # self-written evals; compare: eval compare baseline x
python -m playqa serve                   # MCP server over stdio (PLAYQA_DISABLE_GROUPS=rare leaves out the rarely used tools)
python -m playqa call                    # list tools; `call <tool> --json '{...}'` runs one without an MCP client
python -m pytest
python scripts/make_examples.py          # regenerate the SYNTHETIC results, console texts, library and samples from the mock world
python scripts/build_schema.py           # library/manifest.schema.json
python scripts/sync_agent_files.py       # regenerate CLAUDE.md, GEMINI.md, copilot file, skill mirrors
```

## Operating rules for an AI agent using this repo

1. Load `skills/studio-playtest-qa-workflow/SKILL.md` and follow it. Keep context small: answers are short on purpose, ask for `detail=true` only when needed.
2. **Name the project and place on every call.** Without both a tool refuses. Never guess a place; state the project and place in your report.
3. **Match the open Studio place first.** `plan_playtest` and `generate_check_script` need `studios` = the output of the hub's `list_roblox_studios` and refuse unless EXACTLY ONE open place is the named one. Never reuse a `studios` list from an earlier session.
4. **Run only in the open Studio session, never on a live server.** Run the plan the tool gives you; save each raw answer, the console text and any screenshot path; do not invent results.
5. **A data check runs only in TEST MODE.** The generator refuses it unless the place's `playtest.yaml` declares a test-mode switch; the script refuses at run time unless the switch is on. Never point it at live data, never add a DataStore call.
6. **Every report states what it measured.** Start from the one-line `summary`. A failure has the check, the evidence (log lines, measured value, screenshot path) and the most likely cause; the cause is a heuristic, say so. A pass lists exactly what was checked and what was not.
7. **Flaky checks are re-run N times before a failure is called** (`plan_playtest` plans the repeats). A failure in fewer runs than N is INCONCLUSIVE; a mixed result is FLAKY; do not call either a bug.
8. **Every threshold is a PLACEHOLDER** (the user has not given pacing or budgets) and **every parser of hub output is `schema_unverified`** (no real capture exists yet). Say both whenever you judge a result.
9. Every write is a dry run first (`dry_run=true`). Record outcomes with `record_result`, runs with `record_run`, the user's verdicts with `record_decision` using their words and a dimension from `style/style.yaml`. Mark a record `mark_global` only when the user says it applies to every place. Promote only with a yes.
10. Never claim anything ran in Studio without the hub's answers. Never publish. Never edit a place's files.

## Conventions for editing this repo

- Python 3.10+. `lupa` only in `playqa/domain/mockworld.py`, `playqa/domain/harness.py` and the tests.
- Shared machinery comes from the installed `guide-core` library. **Import it only through `playqa/guide_adapter.py`** (a test enforces this); fix shared code in guide-core, not here.
- Tool functions are annotated `-> dict[str, Any]`, raise `ValueError` (scope refusals are `ScopeError`, a subclass) with an actionable message, take `project_id`/`place_id`, put a one-line `summary` first, keep descriptions at 300 characters or fewer, and start `[rare]` descriptions with `[rare]`.
- Judgments are data: thresholds in `style/style.yaml` (registered automatically as guide-core parameters by `playqa/learning_params.py`), checks and assertion ids in `rules/*.yaml`, cause rules in `rules/causes.yaml`, log patterns in `rules/log_patterns.yaml`. A new assertion needs a line in its `rules/<check>.yaml` (a test requires every emitted id to be declared), an evaluator branch, a cause rule and an eval.
- Generated Luau stays in the subset Luau and the mock's Lua 5.4 both run (no type annotations, no `+=`, no `continue`), injection-safe (ids, names and paths validated; strings through `quote_string`) and lint-clean. Exactly one `JSONEncode`, no `DataStore` token, no `require(`.
- New behaviour needs an eval task with a hand-computed expectation, and the budget evals (`evals/tasks/budgets.yaml`) must still pass; if output legitimately grows, raise the budget in the same commit and say why.
- After editing `AGENTS.md` or `skills/`, run `python scripts/sync_agent_files.py`; a test fails on drift. After editing the mock world, generators or evaluators, run `python scripts/make_examples.py` and `python -m playqa eval run --label baseline`.
- Real examples go in `evals/real/` (see its README). Report self-written and real results separately; self-written evals only show the tool agrees with itself.
