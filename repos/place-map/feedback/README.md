# Feedback

Structured decisions are stored **locally** and **per place** in `workspace/projects/<project_id>/<place_id>/feedback/` (git-ignored); a record marked `is_global` goes to `workspace/projects/_global/_global/feedback/`:

- `runs.jsonl` - one record per task: request, constraints (including project and place), retrieved ids, tools used, outputs.
- `decisions.jsonl` - the user's verdict (`accept`/`reject`/`revise`), reason, and structured corrections (dimensions: `role`, `landmark`, `naming`, `search`, `summary`, `scope`).

`schema.json` documents both record shapes. The implementation is guide-core's `feedback` module (via `placemap/guide_adapter.py`). Landmark labels are separate: `labels.jsonl` in the same place folder, one row per
confirm, reject or undo, with the feature values and the weight changes it caused. Learned role weights live in `workspace/learning/params.json` (guide-core `params`: versioned, bounded, undoable).
