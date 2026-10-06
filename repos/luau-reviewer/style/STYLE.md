# Style: Luau review policy and naming (starter template)

Human-readable companion to `style.yaml` (the machine-readable source). **A starting point, not your conventions.** Your script naming scheme was not supplied, so the naming
rules below are common Roblox community defaults, labelled STARTER, and **no rule enforces them yet**. Replace them with your own and, if you want them checked, add a rule to `rules/`.

## How the reviewer behaves
- **Order:** security, data, performance and leaks, deprecated API, structure.
- **Severity:** `error` = broken or exploitable, `warning` = likely wrong or fragile, `info` = style or possible improvement.
- **Confidence:** high and medium are reported as defects. Low is reported as a **question** for the user, never as a bug.
- **Strict mode** (`--strict`): enables strict-only rules, turns warnings into errors and infos into warnings.
- **Honesty:** every report names the tool that produced each finding, lists the backends that ran and the ones that are missing, and lists what was not checked.

## Naming (STARTER, advisory)
- Modules: `PascalCase.luau`. Locals, parameters, functions: `camelCase`. Constants: `UPPER_SNAKE_CASE`.
- Scripts: `Name.server.luau` (server), `Name.client.luau` (client), `Name.luau` (ModuleScript). The reviewer uses these suffixes (and folder names such as `ServerScriptService`) to tell
  server from client; pass `context` when the file name does not say.
- Remotes: verb first (`RequestPurchase`, `NotifyLevelUp`).

## Hard rules
Never edit the user's files (patches are diff text). Never claim a missing backend ran. Low confidence means a question. A clean review lists what was and was not checked. Suppressions carry a reason.

## Avoid
Presenting heuristics as proof, findings inside strings or comments, patches that silently change behaviour, reading "no findings" as "no bugs".

## What is enforced vs advisory
Everything in `rules/*.yaml` is enforced by the detectors. Everything in this file about naming is advisory. Heuristic thresholds (for example the 30 second autosave floor) live in `rules/` and are defaults to tune.
