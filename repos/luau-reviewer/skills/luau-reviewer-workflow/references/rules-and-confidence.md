# Rules, severity and confidence

Rules live in `rules/*.yaml`: `id`, `severity` (error, warning, info), `confidence` (high, medium, low), `problem`, `explanation`, `example`, `good`, `fix`, optional `params`, `strict_only`, `supersedes`.

| Category | Ids | What |
|---|---|---|
| security | SEC001-SEC006 | remote handlers trusting arguments, client prices/currency/damage, no cooldown (question), loadstring, require of asset ids, InvokeClient |
| data | DAT001-DAT009 | DataStore without pcall, no retry, SetAsync vs UpdateAsync, no BindToClose, saving too often, no session lock (question), ProcessReceipt status, key from player name |
| performance | PERF001-PERF005 | loop without yield, while true + wait, lookups and Instance.new per frame, Touched without debounce (question) |
| leaks | LEAK001-LEAK004 | connections never disconnected, unbounded tables, per-player tables, per-player instances (question) |
| api | API001-API011 | wait/spawn/delay, Instance.new parent argument, Humanoid:LoadAnimation, camera caching (question), magic numbers (strict), lower-case connect, tick, removed table functions, body movers |
| structure | STR001-STR005 | `--!strict`, untyped public functions (strict), module cycles (folder), server API in client script, client API in server script |
| correctness | COR001 | pcall result ignored |

**Confidence** means "how likely a human would agree this is a defect". High and medium findings are reported as defects. Low are reported as questions. A saved false positive lowers a rule's confidence for similar code by one step (at most two).
**Heuristics, not proofs.** Detection is by pattern over a tokenizer. A guard inside a helper the checker cannot see, aliased method calls, varargs handlers and constants passed to `require` are known blind spots (see `evals/tasks/known_gaps.yaml`).
Numeric limits (for example the 30 second autosave floor in DAT005) are defaults flagged `verify_against_current_docs`: Roblox changes its limits.
