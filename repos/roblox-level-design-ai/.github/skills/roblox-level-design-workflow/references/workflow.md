# Worked example (condensed)

**Brief:** "Two-team arena for the castle ruins, central capture point. The gatehouse (west) and its entrance are locked from the old layout."

1. Locks: gatehouse room + its connection + bounds. `find_past_corrections` -> *spawns: keep enemy spawns out of each other's sight*.
2. `search_level_library("two team arena central objective")` -> positive `arena-two-team-loops-001`; avoid `arena-without-loops`, `hub-spawns-see-each-other`.
3. `create_level_spec(template_id="loop_arena", params={cell_w: 32, cell_d: 32}, dry_run=true)`; edit the spec so `r10` is the gatehouse with the old rect;
   add `locked: {rooms: [r10], connections: [spoke_w], bounds: true}`; `dry_run=false` saves revision 1.
4. `evaluate_level` -> no errors; warning `range_landmark_visibility 0.42 < 0.5`. Move the beacon, add a second landmark; `save_level_revision` (dry run: diff shows
   one landmark moved, locks intact) then save.
5. `build_blockout(dry_run=true)` -> 104 parts, walkable, assumptions listed. `simulate_blockout` ok. `dry_run=false`; send to Studio; `parse_studio_report` -> ok.
6. `inspect_blockout` -> in sync. `capture_review_views`; `screen_capture` each route view. From `route_02` the player faces a long blank wall: note it.
7. Report numbers, locks checked, assumptions (character size from defaults), open question ("east wing?"), captures. User: "good, but the centre feels cramped" ->
   `record_decision(revise, corrections=[{dimension: scale, note: "centre room larger"}])`.
