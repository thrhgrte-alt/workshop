# Feedback

Structured decisions are stored **locally** in `workspace/feedback/` (git-ignored):

- `runs.jsonl` - one record per task: request, constraints, retrieved reference IDs, tools and recipes
  used, output paths, preview.
- `decisions.jsonl` - the user's verdict (`accept`/`reject`/`revise`), reason, and structured corrections.

`schema.json` documents both record shapes. The implementation is `guide_core/feedback.py` in the `guide-core` library (rows now also carry `"schema": 1`; a derived `index.sqlite` sits next to the JSONL files once there is feedback).

Nothing is learned automatically. Feedback changes future behaviour in three explicit ways:

1. `find_past_corrections` returns relevant old corrections so the agent reads them before planning.
2. `promote_run` (with `confirm=true`) can add a reviewed result to the example library. Unconfirmed
   promotion only creates a *candidate*, which default searches ignore.
3. Recurring corrections should be turned into style rules / recipe changes by a person or agent,
   plus an eval task that locks the lesson in.
