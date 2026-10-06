# Worked example (condensed)

**User:** "Wizard's frost spell: a burst on hit, icy blue, not too busy. Mobile players matter."

1. `get_style_brief(focus=["colour","timing"])`; `find_past_corrections("frost spell burst icy blue")` -> past correction
   *size: make the central flash smaller on arcane bursts*. Requirement.
2. `search_effect_library(query="icy burst magic", effect_kind="burst")` -> `arcane-burst-purple-001` (follow: two layers,
   full fade) and avoid `burst-too-heavy`, `burst-too-small`.
3. Recipe `arcane_burst`. Parameters: `color_primary [0.7,0.9,1.0]`, `color_secondary [0.3,0.6,1.0]`, `size 0.8` (smaller flash,
   per the past correction), `intensity 0.8` (not too busy), `speed 12`.
4. `create_effect_from_recipe(dry_run=true, name="frost_hit")` -> plan OK. `check_performance_budget(platform="mobile")` -> ~21 particles,
   within the heuristic budget. `evaluate_effect` -> no auto failures; `reads_in_motion`, `matches_style`, `silhouette` unscored.
   `simulate_effect` -> ok on the mock.
5. `create_effect_from_recipe(dry_run=false, target_path="workspace.Effects")`; send the Luau to Studio's `execute_luau` (Edit);
   `parse_studio_report` -> `status: ok`, 2 instances created.
6. `preview_effect(dry_run=false)`; run its Luau; `screen_capture` at the three cameras. In the gameplay shot the burst
   reads against the grass; the close shot shows a crisp ring. Manual scores: `silhouette 0.8`; others left to the user.
7. Report with the before/after parameters and the estimates marked as estimates. `record_run`.
   User: "nice, but it lingers." -> `record_decision(revise, corrections=[{dimension: timing, direction: less, note: "burst lingers; shorten"}])`.
