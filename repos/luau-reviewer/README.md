# luau-reviewer

A reviewer for **Roblox Luau scripts**. It reads a file, a folder, or a script you exported from Studio (the hub's `script_read` output saved to disk) and reports problems in correctness, security, DataStore and
purchase handling, performance, leaks, deprecated API and structure, ranked by severity and tied to a line. It wraps the existing tools `luau-analyze`, `selene` and `stylua --check` when they are installed,
adds its own pattern rules on top, and folds everything into **one report that names the tool behind each finding**. Built so a capable AI agent (Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor, ...) can do real review work
as an MCP server and a CLI.

**What it is:** a tokenizer-based rule engine (`rules/*.yaml` + detectors), backend wrappers, a layered per-project ruleset, a false-positive learning loop, patch suggestions as diff text, evals with precision and recall, a feedback loop, an MCP server.
**What it is not:** a Luau parser or type checker (that is what `luau-analyze` is for), a replacement for a human read of security-sensitive code, or something that changes any model. It never edits your files, never connects to Studio and never publishes.

> **Honest status.** Built without Roblox Studio, without a real game, and **without `luau-analyze`, `selene` or `stylua` installed**. Everything was tested on synthetic fixtures and on stub programs that print canned output in those tools' formats.
> It has **never been run against** the real tools, a real game or real player scripts, so the backend output formats and the precision on real code are unverified. The own rules are heuristics: expect misses and some noise on real code.
> Shared machinery now comes from the installed `guide-core` library (**install it first**, see Quick start). History: guide-core was not available when this repository was first built, so it shipped a vendored copy of the shared kit; that copy is gone. See "Shared machinery" below.

## Capability matrix

| Capability | Needs | Status |
|---|---|---|
| Tokenizer (comments, quoted and long strings, interpolated strings, numbers) and block/function/call structure | nothing | tested, incl. odd and truncated input |
| 41 own rules in 7 categories (security, data, performance, leaks, API, structure, correctness) | nothing | tested on fixtures: 49 planted-bug files, 21 clean/trap files, inline cases |
| Low-confidence findings reported as **questions**, not defects | nothing | tested |
| Module require cycles (folder review) | nothing | tested |
| `luau-analyze` / `selene` / `stylua --check` folded into the same report, tool named per finding | the tools | **parsing and process handling tested against stub executables only; never run against the real tools** |
| A missing, crashed, timed-out or unparseable backend reported as such (never as clean) | nothing | tested |
| Strict mode (strict-only rules, warnings to errors) | nothing | tested |
| Projects and places registry, layered ruleset (global, project, place), suppressions with reasons | nothing | tested |
| False-positive learning (per project, place or global; deterministic similarity) | nothing | tested, with hand-computed similarities |
| Patch suggestions as unified diff text, self-checked by re-analysis | nothing | tested (also applied with `patch` in a test) |
| Evals with precision and recall, baseline and compare | nothing | 188 tasks |
| MCP server (stdio) | an MCP client | tested in memory and by starting the real server over stdio |
| Review of a script inside Studio | Studio + the hub's `script_read` | **not done here**: save the script to disk first |

## Quick start

```bash
git clone <this repo> && cd luau-reviewer
python -m venv .venv && . .venv/bin/activate          # Python 3.10+
pip install -e /path/to/guide-core                    # FIRST: the shared library (a local repository, not on PyPI)
pip install -e ".[dev]"
python -m luaurev doctor                              # which backends are installed
python -m luaurev review examples/negative/sec002_client_price.server.luau --project example-sandbox
python -m luaurev review examples/places --project example-obby          # folder, labelled by place
python -m luaurev review my/folder --project example-sandbox --strict --backends none   # own rules only
python -m luaurev eval run --label mine && python -m luaurev eval compare baseline mine
python -m luaurev eval-pr --compare baseline          # precision and recall
python -m luaurev export-skill --scope example-obby    # agent skill with the learned parameters and corrections (dry run; --write to write it)
python -m pytest
```
The CLI prints the findings table; exit code 1 means at least one error-severity defect, 2 means the request was refused (for example no `--project`).

## Output

One-line summary first, then a table of findings (file, place, line, rule id, severity, confidence, tool, one-line problem, one-line fix), then questions (low confidence), the ruleset layers and suppressions in effect, the backend statuses and what was not checked:
```
[example-sandbox] ERRORS: 1 error, 0 warning, 0 info, 0 question(s), 0 suppressed in 1 file(s); backends ran: none; MISSING: luau-analyze, selene, stylua
| file | line | rule | severity | confidence | tool | problem | fix |
| ...sec002_client_price.server.luau | 16 | SEC002 | error | high | own | Handler takes `price` (price, currency or damage) from the client and uses it | Look the value up on the server ... |
```
A **clean review states exactly what was and was not checked**: the backends that ran and the ones missing, the rule categories and count, strict mode, and a list of what is out of reach (runtime behaviour, data flow across files, strings, ...).
MCP tools return the same content as compact JSON (`detail="brief"`, the default); `detail="full"` adds fingerprints, snippets and the markdown table.

## Rules

`rules/*.yaml`: every rule has an `id`, `severity` (error, warning, info), `confidence` (high, medium, low), `problem`, `explanation`, `example`, `good`, `fix`, and optional `params` (numeric limits, marked `verify_against_current_docs`), `strict_only`, `supersedes`. List them with `python -m luaurev rules` or `list_rules`.
`rules/_settings.yaml` holds the non-rule settings (learning threshold, limits, strict-mode escalation).

| Category | Rules |
|---|---|
| security | SEC001 remote handler trusts client arguments, SEC002 client-supplied price/currency/damage, SEC003 no cooldown or permission (question), SEC004 `loadstring`, SEC005 `require` of an asset id, SEC006 `InvokeClient` |
| data | DAT001 DataStore call not in `pcall`, DAT002 no retry, DAT003 `SetAsync` where `UpdateAsync`, DAT004 no `BindToClose`, DAT005 saving too often, DAT006 no session lock (question), DAT007 `ProcessReceipt` wrong status, DAT008 never `NotProcessedYet` (question), DAT009 key from player name |
| performance | PERF001 loop without a yield, PERF002 `while true do wait()`, PERF003 lookups every frame, PERF004 instances created every frame, PERF005 Touched without debounce (question) |
| leaks | LEAK001 connection never disconnected, LEAK002 unbounded table, LEAK003 per-player table never cleared, LEAK004 per-player instances not destroyed (question) |
| api | API001-003 `wait`/`spawn`/`delay`, API004 `Instance.new(class, parent)`, API005 `Humanoid:LoadAnimation`, API006 camera cached (question), API007 magic numbers (strict, question), API008 lower-case `connect`, API009 `tick`, API010 removed table functions, API011 body movers |
| structure | STR001 missing `--!strict`, STR002 untyped public function (strict), STR003 module cycle (folder), STR004 server API in a client script, STR005 client API in a server script |
| correctness | COR001 `pcall` result ignored |

## Projects and places

`projects.yaml` lists your games (id, alias, universe id, places with place id and sub-folder, ruleset paths). **The bundled projects `example-obby`, `example-tycoon` and `example-sandbox` are synthetic**, with made-up ids. Every tool that reads or writes project data takes `project_id`
(and `place_id` where it applies) and **refuses without a registered project, saying what is missing; it never guesses**.

The ruleset is layered: `projects/_global/ruleset.yaml` -> `projects/<project_id>/ruleset.yaml` -> the place's ruleset -> an optional explicit file. Suppressions (with a mandatory reason), disabled rules and severity overrides live in those files.
Suppressions and accepted false positives belong to the project (or place) where they were recorded; `apply_globally` marks one as applying everywhere. Every report names the project and place, lists the layers, the suppressions that matched and those that matched nothing, and `review_folder` labels each finding with its place.
This server cannot see Studio, so it cannot confirm that the open place is the one named; check the place name/id when you fetch a script through the hub.

## Learning

`mark_false_positive` stores the normalised code pattern and your reason. Later reviews of the same project lower the confidence of that rule by one step on code whose token 3-gram Jaccard similarity to the pattern is at least 0.6 (`rules/_settings.yaml`); a finding that falls to low confidence is shown as a question.
Similarity is deterministic (identifiers, numbers and strings are normalised; keywords, operators and API member names are kept) and unit-tested with hand-computed values. It is also written as a feedback decision, so `find_past_corrections` retrieves it. Nothing trains a model.

## Tools (MCP and `python -m luaurev call <tool> --json '{...}'`)

Read-only tools carry `readOnlyHint`. Tools that write default to `dry_run=true`. **Nothing edits a reviewed file.** Descriptions are short and results are brief by default.

| Tool | Writes? | Group | Purpose |
|---|---|---|---|
| `review_file` | no | core | Review one file: own rules + installed backends; needs `project_id` |
| `review_folder` | no | core | Review a folder (module cycles, findings labelled by place) |
| `explain_finding` | no | core | Why a rule matters, examples, fix, saved false positives; with `file`+`line` the code around it |
| `get_style_brief` | no | core | Compact style and review policy |
| `find_past_corrections` | no | core | Earlier corrections and false positives relevant to this request |
| `suggest_patch` | diff file only when `dry_run=false` | core | Unified diff as text; never edits your file |
| `suppress_finding` | ruleset YAML when `dry_run=false` | core | Documented suppression with a reason, in the project/place/global ruleset |
| `mark_false_positive` | feedback files when `dry_run=false` | core | Record a false positive so later reviews lower that rule's confidence (an addition to the spec's tool list: the shared `record_decision` has no place for a code pattern) |
| `record_run` | feedback file | core | Save request, inputs, outputs |
| `record_decision` | feedback file | core | Save the user's verdict and corrections |
| `list_rules` | no | `rare` | The rules (id, severity, confidence, one-line problem) |
| `find_remote_handlers` | no | `rare` | Remote handlers: parameters, validated or not, price-like parameters, cooldown signal |
| `find_datastore_calls` | no | `rare` | DataStore calls: in pcall/retry, in loops, key expression, BindToClose |
| `compare_reviews` | no | `rare` | New, fixed and changed findings between two reviews (matched by line text, not line number) |
| `search_library` | no | `rare` | Search the example library (generic shared tool) |
| `promote_run` | library file | `rare` | Add a reviewed run to the library (needs `confirm=true` to curate) |

### Tool groups (token discipline)

Most sessions need only the `core` tools. The `rare` group (`list_rules`, `find_remote_handlers`, `find_datastore_calls`, `compare_reviews`, `search_library`, `promote_run`) can be left disabled to save context:
set `LUAUREV_DISABLE_RARE=1` for the bundled server (`python -m luaurev.server`), or disable those names in your client. The CLI always has them. Output is brief by default (one-line summary first, ranked rows, details through `explain_finding`);
the evals in `evals/tasks/output_size.yaml` give every tool a character budget (typical output measured when written: `review_file` on a bad script about 1.9 k characters, `review_folder` over two places 2.3 k, `explain_finding` 1.9 k, `list_rules` 7.3 k, the finders under 1 k),
and a test fails if brief output grows back to the full report. Tool descriptions are capped by a test.

## Evals, precision and recall

`evals/tasks/` holds 188 tasks: one planted bug per file (`planted_bugs.yaml`, 46), clean files and false-positive traps (`clean_files.yaml`, 21), three **known gaps** (`known_gaps.yaml`: planted bugs the heuristics do not catch, counted as misses on purpose),
plus tokenizer, structure, learning, backend, project/place/ruleset, strict-mode, finder, patch, compare, rule-coverage, inline edge-case and output-size tasks. Fixtures live in `examples/` with explanations (`examples/negative/README.md`, `examples/positive/README.md`).
Traps include bug-looking text in strings, long strings and comments, pcall-wrapped DataStore code, validated remote handlers, `if`-expressions, lookalike class names and a fully correct receipt handler.

`python -m luaurev eval-pr` runs the suite and reports **precision** (of the defects and questions reported, how many were expected) and **recall** (of the planted bugs, how many were caught); `--compare BASE` adds deltas and regressions.
Current baseline: precision 1.00, recall 0.96 (76 of 79 planted rule instances; the 3 misses are the documented known gaps). **Read this number carefully:** the fixtures and the detectors were written by the same author in the same session, so precision 1.00 on them says the checker agrees with its own test set, not that it is 100% precise on your scripts.
The expectations were written from the planted bug, not from the checker's output, and the guard test `test_evals_fail_when_the_checker_is_broken` proves the evals fail for a checker that reports nothing and for one that reports everything. Real-world precision is unmeasured.

## What is verified, and what is not

**Tested locally (tests and evals in this repository):** the tokenizer and structure on fixtures and odd input; every own rule on a planted bug and its traps; the backend wrappers against **stub executables on a temporary PATH** (output parsing, missing, crash, timeout, no shell, path mapping);
the ruleset layers, per-project scoping and refusal without a project; the learning similarity (hand-computed); patches (re-analysed, and applied with `patch`); the MCP server in memory and over a real stdio process; skill validity and agent-file sync; the schema; token budgets.
**Not verified:** the real `luau-analyze`, `selene` and `stylua` (never run: output formats are the author's memory, see `references/README.md`); any real game or real script; Roblox Studio; behaviour of any MCP client; whether Roblox's current rules and limits still match the deprecations and numbers in `rules/` (each is flagged to verify);
precision on real code. If one of the three tools is installed where you run `pytest`, an extra test runs it for real (skipped otherwise; in the build environment none was installed).

## Limits (read before trusting a clean report)

- **A heuristic, not a parser.** The tokenizer is not a Luau parser: it ignores comments and strings and recovers block, function and call shape, nothing more. No type information, no data flow, no constant propagation, no cross-file analysis except module cycles in a folder review.
- Known blind spots (documented as eval gaps): handlers that take `...`, DataStore methods called through an alias, `require` of a constant that holds an asset id, validation hidden inside a helper that never receives the argument by name, code generated at runtime.
- Names matter: server and client scripts are told apart by file name, folder or `context`; a DataStore is recognised by `DataStoreService`/`GetDataStore` in the file or a store/data-like receiver name; module cycles match modules by file name.
- Interpolated-string expressions (`` `{expr}` ``) are treated as opaque text. Code inside strings is never analysed.
- Rule thresholds are defaults (`rules/`), not Roblox limits. SEC001 treats an argument as validated when it appears in a type-check call or in an `if`/`assert` line; that is generous on purpose (fewer false alarms, more misses).
- Large files and folders are capped (1 MB per file, 300 files per review).

## Tunable parameters and skill export (guide-core learning layer)

Every tunable number in `rules/*.yaml` and `rules/_settings.yaml` is registered as a named parameter in guide-core's `params` module (`luaurev/learning_params.py`; 51 in all): `rule.<ID>.confidence` (41), `rule.<ID>.params.<name>` (`rule.DAT005.params.min_seconds`, `rule.API007.params.max_per_file`),
`learning.similarity_threshold`, `learning.max_confidence_steps`, `learning.ngram`. **The defaults are read from those same files, so with nothing learned every review is byte-for-byte what it was** (the evals and a test check that); the five numeric `limits` (file size, file count, findings cap, backend timeout, batch size) are registered **locked**.
A value can differ per project or place and carries a version history, a range and a bounded step; changes reach it only through guide-core's propose, gate (all evals, `evals/real/` and past corrections) and an approval by a named person, and a rollback restores the previous version exactly. Nothing is learned automatically and the model's weights never change.
Severity, `strict_mode.escalate` and the confidences that a detector sets per hit are not parameters. **Not wired yet:** nothing in this repository proposes changes on its own; the parameters are registered and honoured by `review_*`, but filling them with real values needs real runs and a person approving.

`python -m luaurev export-skill [--scope global|<project_id>] [--place ID] [--out DIR] [--write]` generates a short skill folder (`SKILL.md` + `references/`): when to use it, the tool list, the workflow, what is verified and not, the current corrections for the scope, and the parameter values with their versions and the knowledge version/date. It is a dry run unless `--write`; it refuses to overwrite a `SKILL.md` it did not generate and refuses an unregistered project. There is no MCP tool for it (it is a maintenance command, and a tool would add to every client's context).

## Shared machinery and `guide-core`

All shared machinery (dry-run plans, the feedback store, retrieval, the eval runner and compare, MCP helpers, style/config loading, path scoping, and the learning layer: parameters, promotion, skill export) comes from the installed **`guide-core`** library.
**Install guide-core first** (`pip install -e /path/to/guide-core`; this package declares it as a dependency, but it is a local repository, not on PyPI). The vendored `luaurev/core/` copy and its tests (`tests/core_suite`, now part of guide-core's own test suite) were removed.
**Every other module imports shared code only through one thin file, `luaurev/guide_adapter.py`** (a test greps the package to enforce this), under the interface names `dryrun`, `feedback`, `retrieval`, `evals`, `mock`, `luau_safety`, `mcpkit`, `config`, `scope`.
`mock` and `luau_safety` are `None` there: this repository needs neither (it reads code, never runs or generates it). History: guide-core was not available when this repository was first built; the adapter was written so that swapping it in meant editing `guide_adapter.py` and nothing else, and that is what happened. Nothing shared is re-implemented here.

## What you still need to supply

- **Your projects list** (`projects.yaml`): alias, place ids, universe, the sub-folder of each place, and your ruleset paths. The shipped entries are synthetic.
- **Your script naming scheme**: `style/style.yaml` and `style/STYLE.md` contain a clearly-labelled **starter** (PascalCase modules, camelCase locals, `.server.luau`/`.client.luau`); it is advisory and no rule checks naming yet.
- **Real good and bad scripts** from your games, to grow `examples/` and the evals and to measure real precision. The bundled examples are synthetic.
- Optionally install `luau-analyze`, `selene` and `stylua` (and their configuration) so the backends run; then please report any output the wrappers do not understand.

## Connecting agents

Canonical: `AGENTS.md` and `skills/luau-reviewer-workflow/SKILL.md`; `scripts/sync_agent_files.py` copies them where clients look (CI checks drift).

| Client | Instructions | Skill location | MCP configuration |
|---|---|---|---|
| Claude Code | `CLAUDE.md` | `.claude/skills/` | `adapters/mcp-clients/claude-code.mcp.json` |
| Codex CLI | `AGENTS.md` | `.agents/skills/` | `adapters/mcp-clients/codex.config.toml` |
| Gemini CLI | `GEMINI.md` | (instructions only) | `adapters/mcp-clients/gemini-settings.json` |
| GitHub Copilot / VS Code | `.github/copilot-instructions.md` | `.github/skills/` | `adapters/mcp-clients/vscode.mcp.json` |
| Cursor | `AGENTS.md` | - | `adapters/mcp-clients/cursor.mcp.json` |
| Claude Desktop | (none) | - | `adapters/mcp-clients/claude-desktop.json` |

These follow each vendor's documented shape as understood when written; none was tested against a live client. The server is local, stdio and unauthenticated, and refuses non-loopback binds. To get a script out of Studio, use the hub's `script_read`, save it, review it.

## How it improves (and what that does not mean)

`record_run`/`record_decision` corrections recalled by `find_past_corrections` -> `mark_false_positive` (per project) lowering confidence on similar code -> suppressions with reasons -> recurring problems turned into rules plus an eval that locks the lesson in (`eval compare`). No step changes model weights.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `project_id is required` / `unknown project_id` | Add the project to `projects.yaml` and pass its id |
| `backends: ... missing` | Install the tool, or pass `backends=[]` to run only the own rules |
| `unparsed` or `error` backend | The tool's output was not understood or it crashed; run it by hand, report the output |
| A finding is wrong | `mark_false_positive` (and, if it is acceptable, `suppress_finding` with a reason) |
| STR004/STR005 never fire | The script's context is unknown: name it `.server.luau`/`.client.luau` or pass `context` |
| Write refused: outside the allowed directories | Rulesets are written under `projects/` and `workspace/`; widen deliberately with `LUAUREV_ALLOWED_PATHS` |

## Repository map

`luaurev/` (`guide_adapter.py` the only door to shared code (`guide-core`, installed separately); `domain/`: `lexer`, `structure`, `detectors`, `review`, `backends`, `learning`, `ruleset`, `projects`, `patch`) | `rules/` | `projects.yaml`, `projects/` | `style/` | `library/` schema |
`examples/` synthetic Luau | `evals/` | `feedback/` | `skills/` | `adapters/mcp-clients/` | `references/` | `tests/` | `scripts/`.

Licence: MIT for code and docs; see `ASSET_LICENSING.md` for your scripts.
