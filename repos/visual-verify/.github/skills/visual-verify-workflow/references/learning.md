# How accept and reject tune the thresholds

1. `record_run` re-runs the named measurement and stores every check (id, parameter, operator, limit, value, result, dimension), image file names and hashes, never folders or pixels.
2. `record_decision` stores the verdict, the user's reason and corrections (`{dimension, note}`), then logs signals for the improvement loop:
   - **accept** of a result with a FAILED check -> that threshold may be too strict (signal to relax it);
   - **reject / revise** naming a dimension -> among the checks of that dimension that PASSED, the one with the narrowest margin may be too lenient (signal to tighten it);
   - a reject that names no dimension gives no signal (it is not known what to tighten).
3. `python -m visualverify learn propose --project-id P [--place-id L]` groups repeated signals (default: >= 3 runs and >= 50% of the runs that evaluated the threshold) into ONE bounded step per threshold.
   Contradicting evidence (some runs push up, others down) is shown, not merged. Nothing is applied.
4. `learn gate PROPOSAL` runs the proposal against the self-written evals, `evals/real/`, and every past accepted or revised case. Any case that passed before and fails now rejects it automatically (the cases are listed).
5. `learn promote PROPOSAL --approved-by NAME --confirm` makes a new parameter version for that project/place only (an automatic name such as "claude" is refused). `learn rollback PARAM` restores the previous version.
   `learn monitor --since TIME` compares the runs after a change with the runs before it.

Records are per project and place; `record_decision(is_global=true)` marks a correction as applying everywhere (only the user decides that). A threshold learned in one project is not used in another.
This improves only as far as the decisions it is given: with few decisions every value stays at its placeholder default.
