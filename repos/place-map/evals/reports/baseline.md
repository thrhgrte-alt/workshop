# Eval report: baseline

- **Self-written** (written by the builder next to the code; shows the tool agrees with itself): 160/160 passed
- **Real** (examples supplied by the user): 0 cases - no real-world result exists yet; drop examples into `evals/real/`

## Role precision and recall

- **Self-written synthetic places** (written by the builder; shows the tool agrees with its author, not real-world precision): precision 1.000 (35 tp, 0 fp), recall 0.946 (2 missed); decoys selected as confident: none
- **Real (`evals/real/`)**: 0 case(s); no real precision or recall exists until you add examples

  - `mine-main:vendor`: tp 2, fp 0, fn 0
  - `mine-main:upgradable_tool`: tp 1, fp 0, fn 0
  - `mine-main:resource_node`: tp 12, fp 0, fn 0
  - `mine-main:collectible`: tp 8, fp 0, fn 0
  - `mine-main:spawn`: tp 2, fp 0, fn 0
  - `mine-main:zone`: tp 1, fp 0, fn 0
  - `mine-main:currency_display`: tp 1, fp 0, fn 0
  - `mine-main:progression_gate`: tp 1, fp 0, fn 0
  - `role-lab:vendor`: tp 1, fp 0, fn 1 (missed ['role-lab::Workspace/Npcs/Carl'])
  - `role-lab:collectible`: tp 6, fp 0, fn 0
  - `role-lab:spawn`: tp 0, fp 0, fn 1 (missed ['role-lab::Workspace/Start/Pad'])
