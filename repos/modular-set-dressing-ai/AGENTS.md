# AGENTS.md - modular-set-dressing-ai

A toolkit that lets an AI agent dress a scene with a **modular asset kit**: placing approved modules, snapping them to sockets, running seeded rule-driven
dressing, checking collisions, clearances, sightlines and composition exactly, preserving locked pieces, and producing safe Luau for Roblox Studio. It does not retrain any model.

## Setup and commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"              # numpy, Pillow, lupa (Luau mock)
python -m setdress doctor            # what works on this machine right now
python -m pytest                     # unit, geometry, mock-Studio, MCP and eval tests
python -m setdress validate examples/scenes/tavern_corner.json    # findings for a scene file
python -m setdress measure  examples/scenes/tavern_corner.json    # composition metrics
python -m setdress dress    examples/scenes/tavern_bare.json --seed 2   # prints a plan
python -m setdress map      examples/scenes/tavern_corner.json    # plan-view PNG
python -m setdress export   examples/scenes/tavern_corner.json    # prints the Luau
python -m setdress eval run --label x       # eval suite (compare: eval compare baseline x)
python -m setdress serve                    # MCP server over stdio
python -m setdress call                     # list tools; `call <tool> --json '{...}'` runs one without an MCP client
python scripts/refresh_api_snapshot.py      # rebuild references/roblox_api_snapshot.json (needs network)
python scripts/sync_agent_files.py          # regenerate CLAUDE.md, GEMINI.md, copilot file, skill mirrors
```

## Operating rules for an AI agent using this repo

1. Load `skills/modular-set-dressing-workflow/SKILL.md` and follow it. Keep context small.
2. **Kit first.** Only place modules from the approved kit. `search_kit` before proposing anything; never invent a replacement module.
3. Every change is a **plan** (`adds`, `moves`, `removes`). Review the dry run, then apply. Every applied change is a saved version and `undo_last_change` reverses it.
4. This server does **not** connect to Studio. Forward generated Luau to Roblox Studio's own MCP server (`execute_luau`, Edit datamodel). Read that tool's schema for
   argument names. Never claim a scene exists in Studio until `parse_studio_report` says `ok`.
5. **Never publish, save or overwrite a live project.** Generated code replaces only the folder it created itself (`AIGeneratedBy = setdress`) and stops otherwise. Do not loosen the lint.
6. **Locked pieces are law.** Never move or remove them; `check_locked_constraints` must pass before saving or exporting.
7. Keep functional paths and eye-height sightlines clear. A refused placement is information: read the finding, do not work around it.
8. Report every assumption (defaulted kit metadata from `kit_report`, yaw convention) and open question. Write tools default to `dry_run=true`.
9. Metrics are exact for the stated footprints, but "lived-in", "matches style" and "supports gameplay" are human judgments: leave them unscored until someone has looked at the scene in Studio.
10. Record the user's verdicts with `record_decision` using their words and a dimension from `style/style.yaml`; promote results only with a yes.

## Conventions for editing this repo

- Python 3.10+. `numpy`/`Pillow` only in `core/imaging.py` and `domain/mapview.py`, `lupa` only in `domain/mock_roblox.py`.
- `setdress/core/` is vendored from the suite kit: do not edit it here.
- Tool functions are annotated `-> dict[str, Any]` and raise `ValueError` with an actionable message.
- Geometry stays in `domain/geom.py`; the yaw convention (matches `CFrame.Angles(0, rad(yaw), 0)`) is documented there and tested against the mock.
- Generated Luau stays plain-Lua compatible so the mock can run it; ids end up in generated code: keep them `[A-Za-z][A-Za-z0-9_]*` (enforced).
- New kit module: add it to `examples/kit/kit.yaml` (or your own kit) with footprint, height, pivot, tags; run `kit_report` and fix unresolved fields.
- After editing `AGENTS.md` or `skills/`, run `python scripts/sync_agent_files.py`; CI fails on drift.
