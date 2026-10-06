# Migrating the older repositories to guide-core (notes only: nothing here was done)

Applies to `substance-designer-ai` (`sdai`), `roblox-level-design-ai` (`rbxlevel`), `modular-set-dressing-ai` (`setdress`), `roblox-vfx-ai` (`rbxvfx`) and `concept-art-ai` (`conceptai`).
Facts checked on 2026-10: each has a vendored `<pkg>/core/` that is byte-identical to `suite/kit/core` (and therefore to guide-core's kit modules), a `tests/core_suite/` copy of the 50 kit tests, **no `guide_adapter.py`** (hooks, tools, server and domain modules import `.core` directly), and **no `projects.yaml`**
(they predate the project/place rules). So the migration has two parts: swap the import source (mechanical, no behaviour change), then add project/place scope and the learning layer (new behaviour, optional per repository). Do them one repository at a time, as was done for the first three.

## Part 1: swap the import source (no behaviour change)

1. **Record BEFORE:** `python -m pytest` counts, `python -m <pkg> eval run --label before` (and `eval-pr` or any repo-specific report). Keep the JSON.
2. **Install guide-core** (`pip install -e /path/to/guide-core`, `--break-system-packages` on a system Python) and add `"guide-core>=0.1"` to the repository's `pyproject.toml` dependencies.
3. **Create `<pkg>/guide_adapter.py`** and make it the only file that imports `guide_core` (copy the one in `luau-reviewer/luaurev/guide_adapter.py`). Rewrite imports across the package:

   | was | becomes |
   |---|---|
   | `from .core import X` / `from ..core import X` for `agentfiles, cli, commontools, doctor, embeddings, evals, feedback, imaging, manifest, mcpkit, project, retrieval, rubric, style` | `from .guide_adapter import X` (the adapter does `from guide_core import X`) |
   | `from .core.safety import Plan, Versioner` | `from guide_core.dryrun import Plan, Versioner` (in the adapter) |
   | `from .core.safety import resolve_inside, safe_name, PathNotAllowed` | `from guide_core.scope import ...` (in the adapter) |
   | `from .core.project import Project` | `from guide_core.project import Project` |
   | `from .core.cli import DomainHooks, run, emit, all_tools` | same names from `guide_core.cli` |
   | `from .core.mcpkit import ToolSpec, build_server, call_local, serve` | same names from `guide_core.mcpkit` |

   `guide_core.safety` still exists as a re-export, so a first pass can keep the old names and tidy later.
4. **Scripts:** `scripts/build_schema.py` (`from <pkg>.core.manifest import base_schema`), `scripts/sync_agent_files.py` (`from <pkg>.core.cli import run`) and, where present, `scripts/make_examples.py` (`from <pkg>.core.style import load_style`) -> `guide_core.*`.
5. **Delete** `<pkg>/core/` and `tests/core_suite/` (those 50 tests run in guide-core). Delete any test that compares the vendored `core/` with `suite/kit/core`.
6. **Fix the hygiene tests** that grep for `.core` imports: make them forbid `guide_core` imports outside `guide_adapter.py` (otherwise they pass vacuously). Keep any README phrase a test checks (for example "guide-core was not available" as a history sentence).
7. **Docs:** README (install guide-core first; vendored core gone), `AGENTS.md` conventions line ("`<pkg>/core/` is vendored: do not edit it"), `references/README.md`, `feedback/README.md` and `feedback/schema.json` title (they name `core/feedback.py`). Then `python scripts/sync_agent_files.py` to regenerate `CLAUDE.md`, `GEMINI.md` and the copilot file.
8. **Verify AFTER:** same `pytest` count minus the removed vendored tests (say exactly how many), same eval pass counts, and compare the per-task pass/fail JSON (messages that contain temp-dir names or run ids differ by design).
9. Commit (do not rewrite history).

Repository-specific things to look at:

* `roblox-vfx-ai`: `domain/luau.py` (its own lint, which also requires every `:Destroy()` to be guarded by the `AIGeneratedBy` marker) and `domain/mock_roblox.py` (API-snapshot-driven mock). Keep both for now. A later step can call `guide_core.luau_safety.lint` first and keep only the marker-guard check locally, which leaves one copy of the shared deny-list.
* `roblox-level-design-ai`, `modular-set-dressing-ai`: their `domain/mock_roblox.py` mocks are API-snapshot-driven and differ from each other; `guide_core.mock` is the simpler economy-style mock. Do not swap them without comparing what the evals need.
* `concept-art-ai`: its `conceptai/core/imaging.py` is the pixel-metric code `visual-verify` is to reuse. `guide_core.imaging` is the same file, so `visual-verify` can import it from guide-core; the LoRA tooling stays local.
* `substance-designer-ai`: no Roblox specifics; only Part 1 and the learning layer apply.
* Any repository whose tests count tools (`tool_count == N`) or list tool names: Part 1 does not change the tool list.

## Part 2: scope, parameters, skill export (new behaviour, per the support-repo spec)

1. **Projects and places (shared rules 11-13).** Add a `projects.yaml` in the shared format (`docs/api.md`, `scope.ProjectRegistry`), make every tool take `project_id` (and `place_id` where it applies), resolve it with `scope.require_scope(..., registry=...)` and refuse when unresolved. Store feedback per scope: either pass `scope=` to `feedback.record_run/record_decision/find_past_corrections` (rows carry it) or use `scope.scoped_project` per place as the economy balancer does. Per-project style: `scope.load_layered_style(project, scope)`. Tools that need the open Studio place: take the hub's `list_roblox_studios` output as an argument and call `scope.match_open_studio`.
2. **Parameters.** Find the numeric thresholds, weights, bands and confidences (look in `style/style.yaml` ranges, `recipes/*.yaml`, `rules/`, and constants in `domain/`). Register each as a `params.ParamSpec` with the SAME default; derive specs from the YAML where the numbers live there (see `luaurev/learning_params.py`, `econbal/learning_params.py`, `preflight/learning_params.py`) so there is no second copy. Apply learned values at one choke point, and make it return the same object when nothing is learned; prove it with the eval baseline. Mark safety limits `locked=True`.
3. **Skill export.** `skillgen.add_export_skill_command(sub, project, kwargs_for, resolve=...)` in `register_cli`; write `kwargs_for` with the repository's tools, workflow, verified and unverified limits, and its scoped corrections.
4. **Evals.** Add `evals/real/` (empty, with the README from guide-core) and report self-written and real results separately; add a mutation per behaviour so the suite can fail.
5. **README** must state what is verified on fixtures, what was never run against real Studio/Blender/Luau tools, which numbers are placeholders, and the honest learning limits (see guide-core's README).

If a repository cannot keep its results identical after Part 1, stop, revert, and write down exactly which test or eval differs and why.
