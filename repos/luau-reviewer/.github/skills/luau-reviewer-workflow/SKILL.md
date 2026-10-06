---
name: luau-reviewer-workflow
description: Reviews Roblox Luau scripts for security (remote handlers trusting the client, loadstring, require of asset ids), DataStore and purchase handling, performance, leaks, deprecated API and structure, folding luau-analyze, selene and stylua output into one report with the tool named per finding. Use when the user asks to review, audit, lint or check a Luau script, module or folder, to find exploits or data-loss bugs, to explain or patch a finding, to suppress or mark a false positive, or to compare two reviews.
compatibility: Needs Python 3.10+ with this repository installed (pip install -e .). Tools come from the MCP server "luau-reviewer" or `python -m luaurev call`. Optional external tools luau-analyze, selene and stylua are used when installed and reported as missing otherwise. Script source from Studio comes from the hub's script_read saved to disk; nothing here connects to Studio.
metadata:
  version: "0.2.0"
  domain: luau-review
---

# Luau reviewer workflow

Follow in order. Detail is in `references/`; open one only when needed.

## 1. Scope first
- Ask which **project** (and place) the scripts belong to if you do not know: every review, suppression and learning step needs `project_id` from `projects.yaml`. Never guess.
- `get_style_brief`; `find_past_corrections` with the request text (saved false positives and corrections are in there: treat them as requirements).

## 2. Get the code on disk
A file or folder of `.lua`/`.luau`. For code in Studio, use the hub's `script_read`, save the text to a file, review the file. Name server and client scripts `X.server.luau` / `X.client.luau` (or pass `context`).

## 3. Review
- `review_file` or `review_folder` (folder reviews also find module cycles and label each finding with its place). Use `strict=true` when asked for a strict pass.
- Read the one-line `summary`, then `findings` (ranked: severity, then file and line). Then `questions`: these are LOW confidence. Ask the user about them; never present them as bugs.
- Read `backends` and `not_checked`. A missing backend did not run. State exactly what was and was not checked, especially for a clean result.

## 4. Explain and fix
- `explain_finding(rule_id, file, line)` for the why, the code around it and any saved false positives. Quote the rule's explanation; do not invent reasons.
- `suggest_patch` returns a unified diff as text (never an edit). Say what behaviour it changes (for example that `wait` becomes `task.wait`). Some rules have no safe patch; say so and describe the fix.
- Security and data findings first: they can cost players their data or let exploiters in. `find_remote_handlers` and `find_datastore_calls` list the spots to look at.

## 5. Teach it
- The user says a finding is wrong: `mark_false_positive` (dry run first; give the reason in their words). Later reviews of similar code in that project drop one confidence step. Use `apply_globally` only if they say it applies to every project.
- The user accepts a finding but wants it silenced: `suppress_finding` with a real reason. Suppressions are listed in every report.

## 6. Report and record
Report: one-line summary, defects ranked, questions, what ran and what did not, what was suppressed (and where from), one proposed next step. `record_run`; `record_decision` with the user's words and a correction dimension
(false_positive, missed_bug, severity_wrong, confidence_wrong, wrong_fix, noisy_rule, naming, other). `compare_reviews` after fixes shows what is new and what is fixed.

## Hard rules
- Never edit a user's file, never connect to Studio from here, never publish.
- Never say a backend ran unless its status is `ran`. Never call a question a defect. Never call "no findings" "no bugs".
- Never suppress or mark a false positive without the user's say-so and a reason.

References: `references/workflow.md`, `references/rules-and-confidence.md`, `references/backends.md`, `references/projects-and-places.md`.
