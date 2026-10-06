# Workflow notes

- **Brief file** (YAML/JSON): `subject`, `kind` (`environment` | `prop`), `locked` {`axes`, `palette`, `composition` {`focal_box`, `min_focal_contrast`, `centroid_tolerance`}}, `avoid`, `notes`, `reference_ids`.
  Examples: `examples/briefs/`.
- **Run record** (`workspace/generations/<gen_id>/run.json`): brief, adapter + declared model (`model_verified: false`), directions, the exact request per direction (prompt, negative, size, seed, count, settings),
  outputs with sha256, reference ids, feedback. Reproduce a run by re-sending the same requests to the same model; nothing guarantees a hosted model returns the same image.
- **Seeds:** direction `i` of a run uses `seed + 1000*i`; image `j` of a direction adds `j`. Recorded per output.
- **Iterating:** change one thing per round (a different direction, a different seed, or one locked axis). After a revise verdict, call `find_past_corrections` before the next round.
- **When metrics and eyes disagree**, trust your eyes and say so; then consider whether the style range is wrong (a correction for the user to decide).
- **What a modeller still needs:** orthographic or turnaround views, back and underside, exact proportions, material breakdown, scale reference. Concepts rarely give them; list what is missing.
