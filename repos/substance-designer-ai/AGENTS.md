# AGENTS.md - substance-designer-ai

A toolkit that lets an AI agent build Substance 3D Designer materials in the user's style: retrieve
approved references, pick a validated graph *recipe*, generate a Designer script, check rendered maps
against a rubric, and store the user's corrections. It does not retrain any model.

## Setup and commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add ".[embeddings]" for CLIP-style image search
python -m sdai doctor              # what works on this machine right now
python -m pytest                   # unit + MCP + fake-Designer tests
python -m sdai eval run --label x  # deterministic eval suite (compare with: eval compare baseline x)
python -m sdai serve               # MCP server over stdio (same as `python -m sdai.server`)
python -m sdai call                # list tools; `call <tool> --json '{...}'` runs one without an MCP client
python scripts/sync_agent_files.py # regenerate CLAUDE.md, GEMINI.md, copilot file, skill mirrors
```

## Operating rules for an AI agent using this repo

1. Load the skill `skills/substance-material-workflow/SKILL.md` and follow it. Keep context small: retrieve a
   few examples, not the whole library.
2. **Never invent Designer node ids or parameter ids.** Use only `recipes/node_catalog.yaml`. Its entries are
   marked `verified: false` until the user runs the probe inside Designer (`python -m sdai probe-script`).
3. Prefer a recipe plus validated parameters over node-by-node construction. If no recipe fits, say so and
   propose a new recipe file plus an eval task.
4. Generated scripts run **inside Designer**; there is no live connection. Do not claim a material was built
   until `read_build_result` says `ok`, and do not claim it looks right until you have seen a preview or
   measured exported maps.
5. Write tools default to `dry_run=true`. Show the plan before applying. Never write outside `workspace/`
   unless `SDAI_ALLOWED_PATHS` was widened by the user.
6. Record the user's verdicts with `record_decision` using their actual words and a correction dimension from
   `style/style.yaml`. Only promote a result into the library with the user's explicit yes (`confirm=true`).
7. Subjective rubric scores are judgments. Leave them unscored rather than guessing.

## Conventions for editing this repo

- Python 3.10+, standard library first; `numpy`/`Pillow` only in `core/imaging.py`, `domain/materials.py`,
  `domain/synthetic.py`.
- `sdai/core/` is vendored from the suite kit: do not edit it here (re-run the scaffold to refresh).
- Tool functions must be annotated `-> dict[str, Any]` and raise `ValueError` with an actionable message.
- Any new recipe needs: a YAML file in `recipes/`, nodes from the catalog, an eval task, and passing
  `python -m pytest tests/test_recipes.py`.
- Change `style/style.yaml` ranges only with measurements (see `style/STYLE.md`).
- After editing `AGENTS.md` or `skills/`, run `python scripts/sync_agent_files.py`; CI fails on drift.
- Large or private assets stay out of Git (`.gitignore` covers common binaries). Point `SDAI_ASSET_ROOT` at them.
