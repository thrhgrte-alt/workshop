# evals/real/ - examples that were NOT written by the builder

This folder is empty on purpose. The tasks in `evals/tasks/` were written by the same hands that wrote the code they test, so they
only show that guide-core agrees with itself. Put examples here that come from real work: a real script that your game's code review
should block, a real script that must pass, a real correction you gave that must keep holding.

Reports keep the two sets apart (`self_written` and `real`), and an empty `real` set is reported as **0 cases / no real-world result**,
never as a pass.

## How to add one

1. Create `evals/real/<anything>.yaml` (one task or a list). Same format as `evals/tasks/*.yaml`:

   ```yaml
   - id: real-my-shop-script-blocked          # unique, and different from every id in evals/tasks/
     tags: [real, lint]
     source_note: "ServerScriptService/Shop.server.luau from <game>, copied on 2026-10-06"   # where it came from (free text)
     input:
       op: check
       project_id: demo-a
       place_id: main
       code: |
         -- paste the REAL script text here
     checks:
       - {type: equals, path: blocked, value: true}      # what YOU know the right answer is
       - {type: has_finding, code_or_text: HttpGet}
   ```

2. Do not paste secrets or private asset ids. Paths and key-like strings are redacted in run logs, but task files are stored as written.
3. Run `python evals/run_evals.py run --label mine`. The report prints `Real: n/m passed` separately from the self-written count.
4. For a repository built on guide-core, the same layout applies: its own `evals/real/` takes tasks in that repository's task format and the
   improvement gate (`guide_core.gate`) runs them before any proposal reaches you.

A real example that fails is the most valuable result this folder can produce. Do not "fix" the expectation to make it pass.
