# AGENTS.md - luau-reviewer

A toolkit that lets an AI agent **review Luau scripts** for correctness, security, data safety, performance and API currency. It folds the output of `luau-analyze`, `selene` and
`stylua --check` (when installed) and its own pattern rules into ONE report that names the tool behind each finding. It never edits files, never connects to Studio and never publishes.
It does not retrain any model: the model judges, the code does the exact work, saved false positives and corrections make later reviews quieter.

## Setup and commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m luaurev doctor                                   # which backends are installed, rule counts
python -m luaurev review path/to/Script.server.luau --project example-sandbox
python -m luaurev review path/to/folder --project example-obby --strict
python -m luaurev rules --category security                # list the rules
python -m luaurev eval run --label x && python -m luaurev eval compare baseline x
python -m luaurev eval-pr --compare baseline                # precision and recall
python -m luaurev export-skill --scope <project_id>           # generate the agent skill from learned parameters/corrections (dry run; --write)
python -m luaurev serve                                    # MCP server over stdio
python -m luaurev call <tool> --json '{...}'               # any tool without an MCP client
python -m pytest
python scripts/sync_agent_files.py                         # regenerate CLAUDE.md, GEMINI.md, copilot file, skill mirrors
python scripts/build_schema.py                             # library/manifest.schema.json
python scripts/make_examples.py                            # regenerate examples/ and evals/tasks from scripts/example_fixtures.py
```

## Operating rules for an AI agent using this repo

1. Load `skills/luau-reviewer-workflow/SKILL.md` and follow it. Keep context small: reports are brief by default; ask `explain_finding` for detail.
2. **Every tool that reads or writes project data needs `project_id`** (from `projects.yaml`; `place_id` where it applies). If you do not know the project, ASK. Never guess one.
3. **Say what ran.** Read `backends` and `not_checked` in the report. A backend that is `missing` did NOT run; never claim it did. A clean report means "nothing found by the checks listed", not "no bugs".
4. **Low confidence = question.** Put `questions` to the user as questions; do not count them as defects. High and medium confidence findings are defects.
5. **Never edit the user's files.** `suggest_patch` returns a unified diff as text. Show it, say what behaviour it changes, let the user apply it.
6. **Suppress with a reason, only with the user's yes.** `suppress_finding` stores a documented suppression in the project's ruleset (or the place's, or the global one with `apply_globally`); it is still listed in every report.
   `mark_false_positive` records the user's verdict that a finding was wrong; later reviews of similar code in that project lower the rule's confidence. Never use either to make a report look clean.
7. Write tools default to `dry_run=true`. Read the plan, then repeat with `dry_run=false` when the user agrees.
8. Get script source from Studio with the hub's `script_read`, save it to disk, then review the file. This server never connects to Studio and never publishes.
9. Record the outcome: `record_run`, `record_decision` with the user's own words and a dimension from `style/style.yaml`.
10. The checks are heuristics over a tokenizer, not a Luau parser. Say so when a result matters.

## Conventions for editing this repo

- Python 3.10+. All shared machinery (feedback, evals, retrieval, mcp helpers, style/config, scope, dry-run plans, params, skill export) comes from the installed `guide-core` library (install it first:
  `pip install -e /path/to/guide-core`) and is imported ONLY through `luaurev/guide_adapter.py` (a test enforces it). There is no vendored `core/` any more: fix shared code in guide-core, not here.
- Tool functions are annotated `-> dict[str, Any]` and raise `ValueError` with an actionable message. Read-only tools use `read_only=True`. Keep descriptions short (a test limits their length) and output brief (evals budget it).
- Everything the tool judges is a rule in `rules/*.yaml` (id, severity, confidence, problem, explanation, example, good, fix, params). The detector for a rule lives in `luaurev/domain/detectors.py`. Numeric limits are defaults marked `verify_against_current_docs`.
- New rule: add it to the YAML, write its detector, add a planted-bug fixture to `scripts/example_fixtures.py` (plus a clean trap if there is a lookalike), run `python scripts/make_examples.py`, then the evals. A test fails if a rule has no eval.
- Expectations in `scripts/example_fixtures.py` are written from the planted bug, not copied from the checker's output. Do not edit an expectation to make a failing eval pass; fix the checker or document a known gap (`gap=`).
- Tunable numbers live in `rules/*.yaml`; they are registered as guide-core parameters automatically by `luaurev/learning_params.py` (defaults come from the YAML). Add a numeric rule param there and it is registered; `limits.*` stay locked.
- After editing `AGENTS.md` or `skills/`, run `python scripts/sync_agent_files.py`; CI fails on drift.
