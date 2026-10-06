# Workflow details

## Reading a report
```
[example-obby/stage-1] ERRORS: 1 error, 2 warning, 0 info, 1 question(s), 1 suppressed in 3 file(s); backends ran: selene; MISSING: luau-analyze, stylua
```
- `findings`: defects, ranked by severity then file and line. Each row: file, place, line, rule_id, severity, confidence, source (`own`, `luau-analyze`, `selene`, `stylua`), problem, fix, fp (a short id used by `compare_reviews`).
- `questions`: low-confidence findings. Ask, do not assert.
- `backends`: status per tool: `ran`, `missing`, `error`, `timeout`, `unparsed`, `not_requested`. Only `ran` means it ran.
- `checked` / `not_checked`: rule count, categories and everything that was not covered.
- `overrides`: the ruleset layers in effect (global, project, place, explicit), suppressions that matched (with their reason) and ones that matched nothing.
- `learned`: how many findings had their confidence lowered by saved false positives.
- `detail="full"` returns everything (fingerprints, snippets, the markdown table). Use it only when needed.

## Strict mode
`strict=true` enables strict-only rules (API007 magic numbers, STR002 untyped public functions), turns warnings into errors and infos into warnings.

## Context (server or client)
STR004 and STR005 need to know where a script runs. It comes from the file name (`.server.luau`, `.client.luau`), a folder name (ServerScriptService, StarterPlayerScripts, ...) or the `context` argument. A plain `.luau` module has no context unless you give one.

## Typical order for a big folder
1. `review_folder` with `backends=[]` first (fast, own rules only), fix the errors, then a full run with the installed backends.
2. Save the report (`python -m luaurev review <folder> --project <id> --json --out before.json`), fix, review again, `compare_reviews`.
