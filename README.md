# AI creative-work repositories

Twelve **separate** repositories (not a monorepo), each a practical toolkit that lets a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) do one kind of creative work
through instructions, a searchable reference library, deterministic tools, evals and a feedback loop. Each is a Git repository with its own history.

| Repository | Work | Tests | Eval tasks | Needs an app to *finish* the job |
|---|---|---|---|---|
| `substance-designer-ai` | Substance 3D Designer materials and graphs | 122 | 37 | Designer (to run the generated scripts) |
| `roblox-vfx-ai` | Roblox Studio VFX (ParticleEmitter, Beam, Trail, light) | 148 | 70 | Roblox Studio (to run the Luau) |
| `roblox-level-design-ai` | Roblox level design and blockouts | 119 | 100 | Roblox Studio |
| `modular-set-dressing-ai` | Modular asset placement and set dressing | 100 | 103 | Roblox Studio (clone mode needs your kit) |
| `concept-art-ai` | Environment and prop concept art (+ optional LoRA prep) | 105 | 127 | An image generator you configure |
| `asset-roblox-preflight` | Blender export preflight for Roblox uploads | 125 | 157 | Blender and the Roblox upload plan (via the hub) |
| `luau-reviewer` | Luau script review (security, data, performance, style) | 136 | 188 | real luau-analyze, selene, stylua (optional backends) |
| `roblox-economy-balancer` | Economy and progression simulation, per place | 143 | 177 | Studio (to dump real values and apply changes) |

Test counts include the 50 shared-core tests vendored into each repository.

## What is true of all five

- **Nothing here changes any model's weights.** A model reasons from what is in its context. These repositories supply that context (style brief, retrieved examples with avoid-examples, corrections),
  do exact work in deterministic code, and expose actions as typed MCP tools. Improvement comes from data **you** curate. `concept-art-ai` contains an optional LoRA/PEFT *preparation* workflow
  (dataset validation, config, validation plan); it never trains.
- **Not run in the real applications.** This workshop had no Substance 3D Designer, Roblox Studio or image-generation model. Everything that can be verified without them is (mock Designer/DataModel runs,
  independent geometry checks, generated scripts executed on mocks); everything else is labelled **unverified** in each README's capability matrix.
- **Safe by default.** Write tools default to `dry_run=true`; paths are allow-listed; generated scripts only replace what they created; nothing publishes to Roblox or overwrites a live project;
  private assets are read in place and never uploaded.
- **Subjective judgments are not ground truth.** Rubrics mix automatic, manual and hybrid criteria; unscored manual criteria keep a result "incomplete".
- **Style is data you edit.** Each `style/style.yaml` is a *starter template* with heuristic thresholds. Examples are synthetic and labelled so.

Shared plumbing lives in `suite/kit/core` and is vendored (copied, never imported across repos) into each repository so each stands alone. See `PUBLISH.md` to publish them and `CAPABILITY_MATRIX.md` for the detail.

## Layout of this workshop repository

```
bundles/           one git bundle per repository (complete history); restore with suite/restore_repos.sh
suite/kit/         the shared core and its own tests (canonical copy)
suite/scaffold.py  stamps the common skeleton into a repository and vendors the core
suite/*.sh         restore_repos.sh (from bundles), publish.sh (private GitHub repos; you run it)
PUBLISH.md  CAPABILITY_MATRIX.md
```
`repos/` holds the source of all five, browsable here (file copies; each repository's own history is in `bundles/`).

```bash
sh suite/restore_repos.sh            # recreate repos/<name> from bundles/
cd repos/concept-art-ai && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && python -m pytest
```

## Second batch (three Roblox helper repos)

`asset-roblox-preflight`, `luau-reviewer` and `roblox-economy-balancer` follow `roblox-repo-build-instructions.md`. `guide-core` was not available, so each reaches shared machinery through one adapter
module (`guide_adapter.py`); every tool is scoped to a `project_id` (and place), and output is kept short with a size budget in the evals. Starter limits, naming schemes, target pacing and the example
projects are **placeholders to replace**. None was run against real Blender, Studio, a real game, or (for `luau-reviewer`) the real `luau-analyze`/`selene`/`stylua`.

## Third batch: `guide-core` (shared library)

`guide-core` (`repos/guide-core`, package `guide_core`) holds the machinery the Roblox helper repos shared: dry-run plans, feedback store, retrieval, evals, Luau lint and mock DataModel, MCP helpers, config, project/place scope, and a learning layer
(observe, tunable parameters, proposals, an eval gate, versioned promotion, skill export). `luau-reviewer`, `roblox-economy-balancer` and `asset-roblox-preflight` now depend on it (their vendored `core/` folders were removed; test and eval
results were unchanged, see each repo's git history). **Install it first:** `pip install -e repos/guide-core`. The five older repositories still carry their own vendored copy; see `repos/guide-core/docs/migrating-older-repos.md`.
It improves only as far as the corrections and evals it is given; model weights never change.

## Fourth batch: `place-map`, `studio-playtest-qa`, `visual-verify`

Built on `guide-core` (see above). `place-map` indexes a Roblox place from hub-collected snapshots and finds landmarks by structural role scoring (no game-specific names); `studio-playtest-qa` generates assertion Luau and a run plan for scripted
playtests and parses the results (data checks refuse without a test-mode switch); `visual-verify` measures images numerically (silhouette IoU, palette, value structure, edges, tiling, PBR ranges, diffs).
**Every parser of hub output is `schema_unverified`**: no real hub captures existed here, so `samples/hub/` is empty in each repo and `samples/README.md` says which hub call to run and where to save it. `evals/real/` is empty in every repo: all
reported eval results are self-written and only show each tool agrees with itself. All numeric limits are placeholders. Nothing was run against real Studio, Blender or the real Luau tools.
