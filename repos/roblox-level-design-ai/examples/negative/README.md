# Negative examples (avoid these)

Retrieved separately (the `negative` list of a search). Each is a template with a deliberate defect; the eval suite proves the defect is detected.

| Map | Library id | What is wrong | Detected as |
|---|---|---|---|
| `hub_spawns_see_each_other.png` | `hub-spawns-see-each-other` | doors on the centre line: spawns see each other through the hub | `spawn_exposed`, criterion `spawns_safe` |
| `arena_without_loops.png` | `arena-without-loops` | most of the ring removed: one route, one choke point | `range_loops`, criterion `route_choice` |
| `hub_empty_dead_ends.png` | `hub-empty-dead-ends` | north/south rooms hold nothing | `dead_end`, criterion `no_empty_dead_ends` |
