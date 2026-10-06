# guide-core

The shared machinery behind the Roblox guide repositories, as one installable Python library (`pip install -e .`, package `guide_core`, Python 3.10+).
It exists so a fix lands everywhere: dry-run plans, project/place scope, scoped feedback with retrieval, an eval runner with regression compare, one Luau
safety lint, a mock Roblox DataModel, MCP helpers, config with locked values, and a **learning layer** (observations, tunable parameters, proposals, an
eval gate, promotion with provenance, skill export, telemetry).

It is a **library**. It has no domain tools. `examples/toytool.py` is a toy built on it, used by the evals.

> **What "learning" means here.** The language model's weights never change. What improves is the memory (runs and corrections), the retrieval over it, the
> tunable parameters (thresholds, weights, bands, confidences), the rules and recipes, and the checks around the model. The model reads intent, notices patterns
> and drafts proposals; code does the exact numbers, the repeatable checks, the safe storage, the regression testing and the rollback.

## Honest limits (read before relying on it)

- **It improves only as far as the corrections and evals it is given.** It cannot learn taste it has not been shown. Early on it will be mostly default parameters
  (every value is its shipped default); real improvement needs real runs across real projects. With no corrections recorded, `export_skill` says "nothing learned yet".
- **Self-written evals only show the tool agrees with itself.** The 80 tasks in `evals/tasks/` were written by the builder next to the code. They are reported
  as `self-written`, never as real-world precision. `evals/real/` is empty on purpose (see its README for how to drop examples in); an empty real set is reported as
  **0 cases**, not as a pass.
- **Never run against real Studio, Blender or Luau tools.** The Luau lint is a deny-list over text, not a parser or sandbox. The mock DataModel (`lupa`) proves a
  generated script is coherent against a tiny model of the DataModel; it does not model Studio's security rules or most classes. The Studio match works on the
  data of the hub's `list_roblox_studios` as the caller passes it; its exact shape in the real hub was not checked here (`name|placeName|title`, `place_id|placeId|PlaceId`,
  `studio_id|studioId|id` are accepted), so treat it as `schema_unverified` until a real capture is tried.
- **Placeholders.** Every default number in `examples/toytool.py` (lines, TODO limits, step sizes) is arbitrary. Staleness (180 days), `k = 3` projects and the
  monitor's "worse" thresholds (+0.10 override rate, +25% output size, +50% time) are defaults to be tuned, not findings.
- **Redaction is pattern based** (paths, key-like strings, e-mails, JWTs, long random strings): it reduces accidental leakage, it is not a guarantee.
- **Embeddings** (`sentence-transformers`/CLIP) are an optional extra and are not exercised by the tests; the always-available hashing embedder captures lexical similarity only.
- **The mock needs `lupa`** (`pip install 'guide-core[mock]'`). Without it the mock tests skip and `mock.available()` is `False`.
- Learned items are keyed by an abstract signature that a person writes (features and roles, not game names). guide-core cannot check that a signature is abstract.

## Install

```bash
pip install -e /path/to/guide-core            # runtime: mcp, pyyaml, jsonschema
pip install -e "/path/to/guide-core[dev]"     # + pytest, numpy, pillow, lupa
# on a system Python that refuses (PEP 668): add --break-system-packages, or use a venv
python -m guide_core doctor                   # every module imports; which optional dependencies are present
```

Repositories built on it list `guide-core` as a dependency and say "install guide-core first" in their README (it is not on PyPI; it is a local repository).

## Modules

| Module | What it gives you |
|---|---|
| `dryrun` | `Plan` (steps, warnings, fingerprint), `run_plan(plan, applier, apply=False, journal=..., expect=...)`: the plan is the default, apply is explicit and journaled, a stale plan is refused; `Versioner` copy-on-write checkpoints |
| `scope` | `Scope(project_id, place_id)`, `require_scope` (refuses when unresolved), shared `projects.yaml` loader (`ProjectRegistry`), `match_open_studio` (refuses a wrong/ambiguous open place), per-scope workspaces, per-project style overlay on the global style, path allow-list |
| `config` | YAML loading, JSON-Schema validation with all problems reported, locked values (list or marker), layered resolution that shows which layer won, `verify_against_current_docs` listing |
| `feedback` | `record_run`, `record_decision`, `find_past_corrections`, `promote_run`: append-only JSONL + a derived, rebuildable SQLite index (schema versioned); project/place scope, `global` marking; executable regression cases |
| `retrieval` | BM25 text + tags hybrid for library assets and for any dict records (`search_items`); optional embedding similarity |
| `evals` | task format + strict validation, runner, agent-results check, `compare_reports` / `compare_detailed`, JSON and Markdown reports, self-written vs real split |
| `mock` | mock Roblox DataModel on `lupa` (optional) |
| `luau_safety` | the ONE Luau lint (network, publish/save, code loading, destruction) and injection-safe embedding helpers (paths, names, strings, long brackets, place guard) |
| `mcpkit` | `ToolSpec` with read-only/write labels and a `group` (e.g. `rare`, disabled with `<PREFIX>_DISABLE_GROUPS=rare`), stdio server builder, local `call` |
| `observe` | append-only local run log with redaction of paths/keys/tokens; never uploaded |
| `params` | named tunable parameters: default, per-scope value, range, version history, bounded steps, rollback restoring exactly the previous version, `locked`; conflict order explicit label > project > promoted global > default, always showing which won |
| `propose` | groups repeated patterns into structured diff proposals with evidence run ids; **cannot apply anything** |
| `gate` | runs a proposal against self-written evals, `evals/real/` and every past accepted correction; rejects automatically if anything that passed now fails, listing the cases; zero cases is "inconclusive", not a pass |
| `promote` | approved promotion with provenance and changelog; global-candidate only after >= k distinct projects without contradiction, never for known-non-transferable items; staleness decay (archived, not deleted); contradictions surfaced; monitor of the next N runs |
| `skillgen` | `export_skill`: a short `SKILL.md` (+ references) for a scope with the knowledge version and date; `export-skill` CLI command helper |
| `telemetry` | local per-tool output sizes and timings, budgets |

Vendored-kit modules (`manifest`, `cli`, `commontools`, `agentfiles`, `style`, `rubric`, `doctor`, `imaging`, `embeddings`, `safety`) are the shared kit the first three repositories
shipped copies of, kept behaviour-compatible. Full list of names: `docs/api.md`.

## The improvement loop

```
observe (RunLog)  ->  propose (patterns -> diffs, evidence attached)  ->  gate (all evals + real + past corrections)
        ->  approve (a person: approved_by + confirm=True)  ->  promote (version, changelog, provenance)  ->  monitor (next N runs; offer rollback)
```

Nothing in the chain applies itself. Conflict order for any value: explicit user label, project value, promoted global value, shipped default. A learned item becomes a global
candidate only after it is confirmed in at least `k` distinct projects (default 3) with no contradicting evidence and a person still approves. Unconfirmed items stop being retrieved after a
configurable period and are archived, never deleted. Nothing touches a project's files; a rule or synonym diff is recorded for a person to apply.

## Status (2026-10-06)

`python -m pytest`: 347 passed (50 of them are the kit tests the three repositories used to vendor, run here against `guide_core`), 0 skipped with `lupa` installed (the mock tests skip without it).
`python evals/run_evals.py run`: self-written 80/80, **real 0/0 (no real examples yet)**. `python evals/run_evals.py mutate`: all six broken toys fail the tasks that guard what was broken. Tested on Python 3.11 only; the source is checked to parse as 3.10.
The three repositories that use it were migrated with identical results (see their READMEs and `docs/extraction-notes.md`). Nothing was run against a real Studio, Blender, Luau tool or game.

## Evals and tests

```bash
python -m pytest                              # unit tests for every module + kit tests + toy-tool eval tests + hygiene
python evals/run_evals.py run --label mine    # 80 self-written tasks (dry run, feedback, regression, lint, scope, params, promotion, telemetry, skill, redaction)
python evals/run_evals.py mutate              # six deliberately broken toys: each must fail the tasks that guard that behaviour
```

Results are in the repository's commit message and `evals/reports/baseline.md`. The toy suite is the self-written set; `evals/real/` is the place for real examples.

## Building a repository on it

Short guide for agents: `skills/build-a-repo-on-guide-core/SKILL.md`. API: `docs/api.md`. Migrating the five older repositories (not done): `docs/migrating-older-repos.md`.
What was extracted from the three repositories and why: `docs/extraction-notes.md`. Versions and migration notes: `CHANGELOG.md`.
