# Reading the metrics

| Metric / criterion | Meaning | Fix |
|---|---|---|
| `unreachable_rooms`, `disconnected` | rooms no spawn can walk to | add a connection |
| `loops` | independent cycles (edges - rooms + components) | arenas want >= 1 so players can flank |
| `alternates_min` | routes within 1.5x of the shortest, per spawn/objective | add a parallel route |
| `independent_routes_min` | routes sharing no room (max-flow) | add a route that avoids shared rooms |
| `chokepoints_max` | rooms every route must cross | add a bypass (fine for linear levels) |
| `fairness_ratio` | shortest team route / longest | move spawns or doors |
| `spawn_exposure_count` | enemy spawns visible within 80 studs in plan view | stagger doors, add walls |
| `spawn_separation` | walking distance between enemy spawns (heuristic min 60) | move spawns apart |
| `longest_sightline` | longest straight free line inside a room (limit 80) | stagger doors, add cover |
| `landmark_visibility` | share of the main route (every 8 studs) with a landmark in view | taller or better placed landmarks |
| `rest_stretch_max`, `first_encounter_distance` | pacing along the primary route | move or add encounters |
| `dead_ends` | empty dead-end rooms | tag loot/secret/safe or connect |
| `part_count` | blockout parts (limit 800) | simplify |
| geometry walkability | a character of the configured size can walk spawn to objective in the BUILT parts | builder bug or step/door/clearance mismatch |

Limits of the approximation: distances are door-to-door plan distances (not a navmesh); line of sight is plan-view at eye height and floors,
ceilings and props do not occlude; thresholds are heuristic defaults in `style/style.yaml`. Thresholds are smoke alarms: a layout can pass every
number and still play badly, which is why the three manual criteria exist.
