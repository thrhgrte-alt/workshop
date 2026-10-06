# evals/real/: examples you supply (empty on purpose)

Everything in `evals/tasks/` was written by the builder next to the code, so it only shows that the tool **agrees with itself**. Real precision and recall need examples the builder did not write.
Reports keep the two sets apart (`python -m placemap eval-report`): an empty `real/` folder is reported as **0 real cases**, never as a pass.

## How to add one

1. Collect real data: run `plan_refresh` (see `samples/README.md`), run the Luau with the hub, save the result under `samples/hub/` (or anywhere under `workspace/`). Register the place in your own `projects.yaml`
   (point `PLACEMAP_PROJECTS` at it).
2. Decide the answer yourself, from the game: which instances are the vendors, which script fires a remote, what depends on a module.
3. Add a YAML file here (any name ending `.yaml`), same format as `evals/tasks/`. Use `input.ingest` to load your capture into a throwaway workspace and the `tool` op to ask a question:

```yaml
- id: real-vendors-in-my-game
  tags: [real, roles]
  input:
    op: tool
    ingest: [{project_id: my_game, place_id: main, file: samples/hub/execute_luau-collect.json}]
    tool: find_in_place
    args: {query: vendors, role: vendor, project_id: my_game, place_id: main}
  checks:
    - {type: pluck_equals, path: result.selected, field: path, items: ["main::Workspace/Shops/Mara", "main::Workspace/Shops/Tobias"]}
```

Task ids must not clash with the self-written ones. Checks available: `equals`, `list_equals`, `list_contains`, `pluck_equals`, `pluck_contains`, `approx`, `refused`, `text_contains` ... (see `placemap/evalkit.py`).
Rejecting a landmark or correcting an answer with `record_decision` is also stored per place and used as a regression case by guide-core's gate.
