# visual-verify

**Measure a render, screenshot or texture instead of eyeballing it.** A model's reading of an image is imprecise, so this repository gives an AI agent (over MCP or a command line) *numbers it can reason
about*: silhouette overlap with a reference, dominant colours and palette distance, value structure, edge density, tiling seams, PBR channel ranges, before/after differences. Every number comes with the
limit it was compared with and whether it passed. It uses **Pillow and NumPy only** (no model calls, no network, no randomness), so the same input gives the same output.

It is built on [`guide-core`](../guide-core) (dry-run plans, project/place scope, feedback, evals, parameters, the improvement loop, skill export) and does not change any model: the model reasons from the
numbers, the code does the exact work, and your saved accept/reject decisions can move the thresholds toward what you accept, only through an approved, gated step.

## Read this first: honest limits

- **It measures; it does not judge.** It never says an image "looks good". It says what was measured and, always, what was **not**: taste, anatomy and perspective (plus lighting realism, whether the subject is the right
  object, and whatever the individual tool lists under `not_measured`).
- **Every threshold is a placeholder.** The defaults in `style/thresholds.yaml` were chosen by the builder as a starting point; they are not measured results and not official Roblox numbers. Save a target profile from a
  reference you approve, and record your accept/reject decisions, so they can be replaced by yours. Nothing was verified against Roblox documentation (the `pbr.*` bounds are flagged `verify_against_current_docs`).
- **The evals only show the tool agrees with itself.** The 146 tasks in `evals/tasks/` are **self-written**: the builder generated images with known properties and worked out the expected numbers by hand. That proves
  the arithmetic and the guard rails; it says nothing about how well the tool serves *your* renders. `evals/real/` is empty on purpose and is reported separately ("Real: 0 cases"). See its README to add your own.
- **Masks assume a plain background or alpha.** On a busy background the silhouette is unreliable, and the answer says so in `warnings`.
- **The screenshot reader is `schema_unverified`.** No real hub `screen_capture` result existed when it was written (see `samples/README.md`).
- **It was never run against** a real Roblox Studio screenshot, a real Blender render, or any real texture set of yours: only generated images. Studio and Blender are never connected to, from here, at all.
- **It improves only as far as the decisions it is given.** With few or no decisions every value stays at its placeholder default.
- Your images are **never uploaded**, copied or sent anywhere: they are read from folders you allow, on this machine, and a saved profile contains numbers only.

## Install

```bash
pip install -e /path/to/guide-core          # the shared library first (it is a local repository, not on PyPI)
pip install -e ".[dev]"                     # this repository (numpy and pillow are required dependencies)
python -m visualverify doctor               # versions, registered projects, how many thresholds
python -m visualverify projects             # the (synthetic) projects in projects.yaml
python -m visualverify iou examples/shared/cand_shifted.png examples/shared/ref_hero.png --project-id demo-stylized-obby
```

On a system Python that refuses (PEP 668) add `--break-system-packages` or use a venv. MCP client configs are in `adapters/mcp-clients/` (documented shapes, **not tested against live clients**); the server is
`python -m visualverify.server` over stdio.

## Projects and places

Every tool takes `project_id` (and `place_id` where it applies) from `projects.yaml` and **refuses without one**: it never guesses. An unknown project or place is refused with the list of known ones. The three
projects shipped are clearly **synthetic** (no real game, place id or Studio name). Each project lists the folders its tools may read in `image_roots`; **one project cannot read another project's folder**.
Besides `image_roots` a tool may read `examples/shared/` (synthetic knowns), `samples/`, the private `workspace/` and any folder in `VISUALVERIFY_ALLOWED_PATHS`. Everything the tools write goes under
`workspace/projects/<project>/<place or _project>/` (git-ignored). Feedback, corrections, profiles and learned thresholds are per project (and place); a correction may be marked global only by the user.

## Tools

Read-only (they write nothing, not even a log):

| Tool | What it gives |
|---|---|
| `measure_image` | dominant colours, saturation/value, luminance contrast and the 3 or 5 level value map, edge density and distribution (too flat or too noisy), silhouette descriptors; checks against thresholds, a `target_palette` or a saved profile |
| `compare_to_reference` | silhouette IoU and offsets, palette distance, luminance-histogram distance and edge-density ratio against a reference image (or a saved profile) |
| `silhouette_iou` | mask extraction (alpha, plain background, luminance, explicit mask image), IoU, Dice, centroid offset and bounding-box offset |
| `palette_distance` | CIE76 delta-E, both directions, between the dominant colours and a target palette or profile palette (optional weights) |
| `check_tiling` | seam ratio across the wrap-around edge (worst axis) and repetition inside the tile (autocorrelation peak and lag); full resolution only |
| `check_pbr_ranges` | albedo luminance bounds, roughness/metalness grayscale and spread, metalness in-between share, normal-map unit length and +Z, equal map sizes |
| `diff_images` | MAE, RMSE, PSNR, changed-pixel fraction and bounding box of two same-size images |
| `get_style_brief` | the style brief and the thresholds in force for this project/place, labelled placeholders |
| `find_past_corrections` | past accept/reject corrections for THIS project/place (and global ones) relevant to a request |
| `get_target_profile` | [rare] list the saved profiles, or show one profile's numbers |
| `list_thresholds` | [rare] every named threshold: current value, source (default or learned), range, largest step |

Write tools (all labelled `write`; those that touch files default to `dry_run=true`):

| Tool | What it does |
|---|---|
| `save_target_profile` | builds a target profile from a reference image and/or explicit values and saves it for the project/place; **numbers only, never the image**; dry run first; old versions are kept |
| `render_diff_image` | renders a `side_by_side` (before, after, amplified difference) or `difference` PNG into the private workspace; dry run first; refuses to overwrite different content |
| `ingest_capture` | turns a saved hub `screen_capture` result into a local PNG in the workspace (`schema_unverified` parser); dry run first |
| `record_run` | saves a request and, if you pass `measure={"tool": ..., "args": {...}}`, **re-runs** that measurement (deterministic) and stores every check with its limit and value, so nothing is retyped |
| `record_decision` | saves the user's accept/reject/revise with their reason and `{dimension, note}` corrections, a regression case for the gate, and the signals for the improvement loop; never changes a threshold |
| `promote_run` | [rare] adds a reviewed run's image to this project's example library (a candidate unless `confirm=true`; ask the user first) |

`search_library` of the shared kit is dropped (it is not project-scoped). **Tool groups:** `VISUALVERIFY_DISABLE_GROUPS=rare` leaves out the three tools marked `[rare]` (their descriptions are
not loaded into the model's context). **Token discipline:** the answer starts with a one-line `summary`, then `findings` (failed checks, ranked, capped at `limits.findings_cap` = 10, with value, operator and
limit), `passed` (each with its limit), `skipped` (could not be measured: never counted as passed), `measured` (headline numbers), `warnings`, `not_measured`; `detail=true` adds the rest. Each tool has an output-size budget eval
(typical answers are 150-3,100 characters, about 40-800 tokens).

## What is measured (and how)

Definitions are written out in `skills/visual-verify-workflow/references/measurements.md` and in the code (`visualverify/domain/`). In short: colours are sRGB-encoded; luminance is Rec.709 weights on the encoded values;
the analysis works on an area-averaged copy no bigger than `limits.analysis_max_side` (512 px; never upscaled), while tiling, PBR and diff run at full resolution up to `limits.max_side_exact` (2,048 px) and **refuse**
larger images instead of downscaling them silently. Palette = deterministic k-means (luminance-quantile starts, fixed-stride sampling). Palette distance = Lab chamfer, CIE76. Silhouette IoU is computed on the candidate's
grid (the reference is resampled onto it, with a warning if the aspect ratios differ). Seam ratio = wrap-around step divided by the steps just inside the edges (1.0 = continuous). The pixel metrics were extracted from
concept-art-ai's analysis code and re-implemented here with every definition pinned down (this package does not import it).

**Never measured:** taste, anatomy, perspective, lighting realism, whether the subject is what was asked for, colour harmony, visibility of a seam at the scale it is used, repetition across many tiles, whether PBR values are
physically right for a material, the normal map's green-channel convention (OpenGL vs DirectX), perceptual difference (these are pixel differences).

## Placeholder numbers

All of `style/thresholds.yaml` (44 named parameters): silhouette IoU >= 0.80, centroid and bounding-box offsets <= 0.05, palette distance <= 25 delta-E, luminance contrast >= 0.35, each of three value levels >= 5%,
edge density 0.01 to 0.35 and distribution >= 0.40, seam ratio <= 1.5, repetition peak <= 0.60, albedo luminance 30/255 to 240/255 with <= 5% outside, roughness std >= 0.02 and mean 0.05 to 0.98, metalness
in-between share <= 25%, normal length error <= 0.03 with <= 2% bad pixels and <= 1% pointing inward, changed-pixel threshold 8/255 and share <= 5%, and the extraction settings (k = 5, gradient 0.08, background
tolerance 0.12 ...). The `limits.*` entries are safety rails (file size 64 MiB, 40 megapixels, 2,048 / 512 px, findings cap 10) and are **locked**: learning can never change them.

## Learning from what you accept and reject

Save the user's verdict on a result together with its measurements, and the thresholds can be tuned toward what they accept, **only through guide-core's improvement loop**:

1. `record_run(request, measure={...})` then `record_decision(run_id, decision, reason, corrections=[{dimension, note}])`. Accepting a result that **failed** a check signals "this limit may be too strict";
   rejecting or revising one in a named dimension signals "the check that passed by the narrowest margin may be too lenient". A reject that names no dimension gives no signal.
2. `python -m visualverify learn propose --project-id P [--place-id L]` groups repeated signals (>= 3 runs and >= 50% of the runs that evaluated the threshold) into one bounded step per threshold. Contradicting
   evidence is shown, not merged. Nothing is applied. (Propose at the scope you recorded at: place-level decisions are not pooled into a project-level scope.)
3. `learn gate PROPOSAL` runs it against the self-written evals, `evals/real/`, and every past accepted or revised case; one previously passing case that now fails rejects it automatically, with the cases listed.
4. `learn promote PROPOSAL --approved-by NAME --confirm` creates a new parameter version for that project/place only (an automatic name such as "claude" is refused). `learn rollback PARAM` restores exactly the
   previous version; `learn monitor --since TIME` compares the runs after a change with those before; `export-skill` regenerates the agent skill with the current values.

No MCP tool can approve, apply or promote anything. Evals run with the shipped defaults whatever has been learned. See `skills/visual-verify-workflow/references/learning.md`.

## Screenshots from Studio

The hub's `screen_capture` returns the image; **this server never calls the hub**. Save the full result to disk, run `ingest_capture` (dry run, then apply) and measure the path it returns. Exactly which call and where to
save it: `samples/README.md`. The reader is **`schema_unverified`** (every `ingest_capture` answer says so); `samples/synthetic/` holds three hand-written stand-ins, labelled synthetic; `samples/hub/` is empty until you
save real captures there.

## Command line

`python -m visualverify <command>`: `doctor`, `projects`, `measure`, `compare`, `iou`, `tiling`, `pbr`, `diff` (exit 1 if a check failed), `learn ...`, `export-skill`, `eval run|compare`, `eval-report`,
`sync-agent-files`, `check-skills`, `serve`, `call <tool> --json '{...}'` (any tool, same functions as MCP). Exit code 2 means a refusal (unknown project, path outside the allowed folders, bad input).

## Evals, tests and what was verified

| What | Result in this environment |
|---|---|
| `python -m pytest` | 90 passed (about a minute), 0 skipped |
| Self-written evals (`evals/tasks/`, 146 tasks: known-good, known-bad, refusals, hand-computed, isolation, budgets) | 146/146; baseline in `evals/reports/baseline.json` / `.md`; `eval compare` shows regressions |
| Real evals (`evals/real/`) | **0 cases** (none exist yet: add yours) |
| Vacuity guards | tests break the judge on purpose (always-pass checks, a seam measure stuck at 0, an edge map that is empty, removed scope and path guards, dry runs that write, a blind gate, wording with a verdict, oversized output) and require the matching evals to fail |
| MCP | in-memory session, and a real stdio subprocess (`suite/smoke_stdio.py`) |

Verified **only on generated images** (flat shapes, gradients, seeded noise, known-answer normal maps): the pixel metrics against hand-computed values (IoU 0.6, delta-E 176.31 red-vs-blue, edge density 0.02,
seam ratio 255, mid-fraction 0.796875, normal length 0.85984 ...), determinism across processes and hash seeds, scope and path confinement (including symlinks and traversal), dry-run behaviour, the learning loop on
synthetic decisions. **Never run against** real Roblox Studio or Blender output, real user textures, a live MCP client, or the real hub `screen_capture`.

## guide-core follow-ups (found while building; guide-core was not edited)

- `guide_core.scope.Scope` validates ids with `re.match(r"^...$")`: `Scope("demo\n")` is accepted (the registry lookup still refuses it). Use `fullmatch`.
- `cli.run`'s `eval run` only knows the self-written folder; the split report (`eval-report`) is this repository's own command. A shared `eval run --split` would remove the duplicate.
- `cli.all_tools` has to be replaced process-wide to drop `search_library` and swap in scoped feedback tools (as the other repositories do); a supported hook would be cleaner.
- `observe.RunLog.runs(project-level scope)` returns only rows logged without a place, because `Scope.covers` is "record covers query"; proposing for a whole project cannot pool its places' decisions.
- `feedback.accepted_regression_cases` counts only accept/revise decisions, so a rejected result cannot be kept as a regression case.
- `ProjectRegistry` keeps unknown keys in `extra`; per-project read folders (`image_roots`) would be a natural first-class field (a shared path allow-list per project).
- `guide_core.imaging` was not used: its `seam_score` and palette code were re-derived here with their definitions pinned (this is a design choice, not a defect).

## What you still need to supply

- Your real projects and places in `projects.yaml` (alias, Roblox place id if published, local file, `image_roots`); the shipped three are synthetic.
- **One real `screen_capture` result** saved as described in `samples/README.md`, so the parser can be checked against it (and one for a richer place than an empty baseplate).
- **A handful of real examples** in `evals/real/` (see its README): images you have judged, with the verdict you made.
- **Your target look**: a profile saved from a reference you approve (`save_target_profile`), and your accept/reject decisions, so the placeholder thresholds can be replaced.
- Confirmation (or correction) of the texture conventions in `style/thresholds.yaml` (`pbr.*`) against current Roblox documentation.

## Layout

`visualverify/` (package: `domain/` measurements, `tools.py`, `hooks.py`, `learn.py`, `learning_params.py`, `evalsupport.py`, `evalgen.py`, `guide_adapter.py` = the only import of shared machinery), `rules/`
(what each measurement is), `style/` (style, thresholds), `library/` (manifest schema), `examples/` (synthetic), `evals/` (`tasks/`, `real/`, `reports/`), `feedback/`, `skills/visual-verify-workflow/`,
`adapters/mcp-clients/`, `samples/`, `projects.yaml`, `projects/` (per-project style overlays), `references/`, `scripts/`, `tests/`. `AGENTS.md` is the canonical agent file; `CLAUDE.md`, `GEMINI.md`, the Copilot file and the
skill mirrors are copies made by `python scripts/sync_agent_files.py` (CI fails on drift).

MIT licensed code; see `ASSET_LICENSING.md` for the assets you add.

## Status

`python -m pytest`: 90 passed. `python -m visualverify eval run --label baseline`: 146/146 self-written tasks (`evals/reports/baseline.json`); `eval-report`: self-written 146/146, real 0 cases.
`python -m visualverify check-skills`, `sync-agent-files --check`, `scripts/build_schema.py --check`, `scripts/make_examples.py --check` and `scripts/make_samples.py --check` are clean.
A real stdio MCP process (`suite/smoke_stdio.py`) lists 17 tools (11 read-only) and answers `get_style_brief` and `measure_image`. Date of these runs: 2026-10-06 (Python 3.11, NumPy 2.4, Pillow 12.3, guide-core 0.1.0).
