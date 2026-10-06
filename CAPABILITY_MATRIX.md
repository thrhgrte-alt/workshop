# Capability matrix (all five repositories)

Legend: **tested** = exercised by the repository's tests/evals in this workshop; **mock** = run against a mock of the application built from its documentation; **unverified** = needs the real application,
model or key, not run.

| Capability | designer | vfx | level | set-dressing | concept-art |
|---|---|---|---|---|---|
| README, AGENTS.md, skill (`SKILL.md` + references), adapters for 6 clients | tested (spec check, sync drift) | same | same | same | same |
| MCP server over stdio, read-only vs write tools, `dry_run` defaults, path allow-list | tested in-memory + real stdio | same | same | same | same |
| Style spec (human + machine-readable) and library manifest schema | tested | same | same | same | same |
| Positive and negative examples with explanations | synthetic, tested | synthetic, tested | synthetic, tested | synthetic, tested | synthetic fixtures, tested |
| Hybrid retrieval incl. avoid-examples; optional embeddings | tested (embeddings optional, untested) | same | same | same | same |
| Feedback: runs, decisions, corrections recalled later; curation gate | tested | same | same | same | same (+ AI-output safeguards) |
| Evals with versioned baseline and regression compare | 37 tasks | 70 | 100 | 103 | 127 |
| Domain core without the app | graph spec, script generation, node catalog diff, `.sbs` summary, synthetic previews | effect spec, API-checked Luau, analysis, previews | spec, graphs, sightlines, blockout, walkability | geometry, kit, plans, dressing, composition | directions, prompts, measurements, run records |
| Generated code verified | scripts run against a fake Designer API: **mock** | Luau on a mock DataModel: **mock** | Luau on a mock DataModel: **mock** | Luau on a mock DataModel: **mock** | n/a |
| Result in the real application | **unverified** | **unverified** | **unverified** | **unverified** | **unverified** (no generator run) |
| Node ids / parameters / API surface | draft catalog + probe script and diff to verify; **unverified until probed** | parsed from Roblox creator-docs | parsed from Roblox creator-docs | parsed from Roblox creator-docs | no provider API used |
| Human judgment | rubric manual criteria left unscored | same | same | same | same, plus hybrid criteria |
| Never publishes / overwrites live work | lint + tests | lint + tests | lint + tests | lint + tests | nothing uploaded |
| Special: separate LoRA/PEFT workflow | - | - | - | - | validation, config, plan; **never trains** |

Details and the exact limitations are in each repository's README (Capability matrix, Limitations, `references/README.md` verification log).
