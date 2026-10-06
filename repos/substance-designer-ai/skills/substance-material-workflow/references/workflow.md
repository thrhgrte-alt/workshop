# Worked example (condensed)

**User:** "I need a stone wall for the castle ruins. Chunkier than last time, softer edges, a bit cooler."

1. `inspect_environment` -> catalog not yet verified on this machine (probe never run). Mention once.
2. `get_style_brief(focus=["bevel","palette"])`; `find_past_corrections("stone wall castle ruins chunkier softer cooler")`
   -> a past correction: *bevel_roundness: round the bevels more on stone walls*. Treat as a requirement.
3. `search_material_library(query="chunky stone wall rounded bevels cool", material_type="stone")`
   -> positive: `stone-wall-chunky-001` (recipe `stylized_stone_wall`); avoid: `stone-wall-too-noisy`,
   `stone-wall-neon-palette`.
4. `list_recipes` -> `stylized_stone_wall`. Parameters: `brick_columns 6 -> 4`, `brick_rows 10 -> 7`
   (chunkier), `bevel_softness 2.0 -> 3.5` (softer; past correction), colours from the reference palette with
   a cooler light colour `[0.58, 0.64, 0.72, 1]`.
5. `validate_graph_spec` -> valid, warning `unverified_node` x N. `create_graph_from_recipe(dry_run=true)` ->
   plan shown; then `dry_run=false` -> script + result path.
6. User (or an agent with Designer access) runs the script. `read_build_result` -> `status: ok`.
7. `render_preview(dry_run=true)` shows commands; the user exports maps instead. `compare_material_to_rubric`
   -> no auto failures; subjective criteria unscored. Look at the baseColor: shapes read well, palette is
   cooler. Manual scores: `reads_as_intended 0.8`, `matches_style` left unscored (needs the user's eye).
8. Report: references used, parameters changed (with before/after), measured results, what is unverified,
   preview path. Suggest one next step ("try `bevel_softness 4.5` if the corners still feel hard").
9. `record_run`. User: "better, but the gaps are too dark." -> `record_decision(revise, corrections=[
   {dimension: contrast, direction: less, note: "gaps too dark on castle wall"}])`.
