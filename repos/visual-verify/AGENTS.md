# AGENTS.md - visual-verify

A toolkit that lets an AI agent **measure** a render, screenshot or texture instead of reading it by eye: silhouette overlap with a reference, dominant colours and palette distance, value
structure, edge density, tiling seams and repetition, PBR channel ranges, before/after differences. Pillow and NumPy only: no model calls, no network, no randomness, so the same input gives
the same output. It never connects to Studio or Blender, never publishes and never uploads an image. It does not retrain any model: the model reasons from the numbers, the code measures,
and saved accept/reject decisions can move the thresholds toward what the user accepts, only through an approved, gated step.

## Setup and commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e /path/to/guide-core && pip install -e ".[dev]"
python -m visualverify doctor                                          # versions, registered projects, thresholds
python -m visualverify projects                                        # the projects and places in projects.yaml
python -m visualverify measure examples/shared/ref_hero.png --project-id demo-stylized-obby
python -m visualverify iou CANDIDATE REFERENCE --project-id P [--place-id L]
python -m visualverify tiling TILE.png --project-id P        # also: compare, diff, pbr
python -m visualverify learn list --project-id P             # learned thresholds + proposals; also propose, gate, promote, reject, rollback, monitor
python -m visualverify export-skill --scope P                # dry run; --write to generate the skill from learned values and corrections
python -m visualverify eval run --label x && python -m visualverify eval compare baseline x
python -m visualverify eval-report --label x                 # self-written and real eval sets, reported separately
python -m visualverify serve                                 # MCP server over stdio
python -m visualverify call <tool> --json '{...}'            # any tool without an MCP client
python -m pytest
python scripts/sync_agent_files.py                           # regenerate CLAUDE.md, GEMINI.md, copilot file, skill mirrors
python scripts/build_schema.py ; python scripts/make_examples.py ; python scripts/make_samples.py
```

## Operating rules for an AI agent using this repo

1. Load `skills/visual-verify-workflow/SKILL.md` and follow it. Reports are brief by default; pass `detail=true` only when you need the extra numbers.
2. **Every tool needs `project_id`** (from `projects.yaml`; `place_id` where it applies). If you do not know the project, ASK. Never guess one. Answers state the project and place they are for.
3. **Report numbers WITH the limits they were compared with, and whether each passed.** Never say an image "looks good" or "looks right". Say what was measured and what was NOT: taste, anatomy,
   perspective, lighting realism and whether it is the right object are never measured. Say the thresholds are PLACEHOLDERS until the user's profile or decisions replace them.
4. **Silhouette masks assume a plain background or alpha.** When a result carries a mask warning, say the silhouette is unreliable instead of quoting the IoU as fact.
5. **Reference images stay local.** Never upload, paste or copy a user's image anywhere; a target profile stores numbers, not pixels. Paths outside the allowed folders are refused: do not look for a way around it.
6. **Screenshots come from the hub's `screen_capture`**: save the result to disk (see `samples/README.md`), then `ingest_capture` (dry run, then apply). That parser is `schema_unverified`: if it refuses a
   real capture, report the key names it listed so the parser can be fixed; do not hand-edit the file to make it parse.
7. Write tools default to `dry_run=true`. Read the plan, then repeat with `dry_run=false` when the user agrees. They write only under the private `workspace/`.
8. **Never change a threshold yourself.** `record_run` (it re-measures, so the numbers are stored exactly) and `record_decision` (the user's own words, a dimension from `style/style.yaml`) only log evidence.
   Thresholds move one bounded step at a time through `learn propose` -> `learn gate` -> the user's explicit yes -> `learn promote --approved-by NAME --confirm`; rollback is `learn rollback`.
9. Same input gives the same output. If a number changed, the image or a threshold changed: check `learned_thresholds` in the answer.

## Conventions for editing this repo

- Python 3.10+. All shared machinery (feedback, evals, mcp helpers, style/config, scope, dry-run plans, params, observe/propose/gate/promote, skill export) comes from the installed `guide-core` library and is
  imported ONLY through `visualverify/guide_adapter.py` (a test enforces it). There is no vendored `core/`: fix shared code in guide-core, not here.
- The pixel metrics are this package's own (`visualverify/domain/`), re-derived from concept-art-ai's analysis code with every definition pinned down; do not import from concept-art-ai. No `random`, no network, no
  model calls: a test greps for them.
- Tool functions are annotated `-> dict[str, Any]` and raise `ValueError`/`PermissionError` with an actionable message. Descriptions stay short (a test limits them); outputs stay brief (evals budget every tool).
- Every number a verdict depends on lives in `style/thresholds.yaml` (default, range, largest step, operator, dimension) and is registered as a named parameter by `visualverify/learning_params.py`; `limits.*` are locked.
  A threshold in code is a bug. Defaults are PLACEHOLDERS: label them so in anything you write.
- New measurement: implement it in `visualverify/domain/`, report it as checks (`domain/checks.py`), add its thresholds to the YAML, add generated-image evals with hand-computed expectations to `evals/tasks/`
  (write the expectation from the generator's parameters, never from the tool's output) and a `budget-<tool>` eval. A test fails if a tool has no budget eval or a threshold is unregistered.
- Evals that assert a verdict near a threshold set `pin_defaults: true`; others must survive a one-step threshold change (that is what lets the gate accept a sensible proposal).
- After editing `AGENTS.md` or `skills/`, run `python scripts/sync_agent_files.py`; CI fails on drift.
