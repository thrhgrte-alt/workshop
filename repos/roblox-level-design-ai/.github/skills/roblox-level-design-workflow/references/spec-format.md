# Level spec format

```yaml
id: castle_ruins            # letters, digits, underscore (it ends up in generated code)
profile: arena              # arena | hub | linear (selects the route-choice requirements in style.yaml)
bounds: {width: 128, depth: 96, ceiling: 16}
player: {height: 5.0, eye: 4.5, max_step: 1.5}      # optional overrides; defaults come from style.yaml (ASSUMPTIONS: verify)
rooms:                       # rect = [x, z, width, depth] OUTER boundary; floor = y of the floor; walls sit inside the rect
  - {id: gate, rect: [0, 0, 32, 32], floor: 0, height: 16, tags: [spawn]}
connections:                 # rooms must share a wall; at = position along the wall; width defaults per kind
  - {id: c1, from: gate, to: hall, kind: door}      # door | opening | stairs | ramp (stairs/ramp need different floors)
spawns:     [{id: spawn_a, at: [16, 16], team: a}]
objectives: [{id: core, at: [64, 48], kind: capture}]
landmarks:  [{id: tower, at: [60, 40], height: 36}]
encounters: [{id: fight, room: hall, intensity: 4}]   # intensity 1-5
locked: {rooms: [gate], connections: [c1], bounds: true, spawns: [spawn_a]}   # also objectives, landmarks
open_questions: ["Should the east wing connect to the courtyard?"]
```

Rules enforced by `create_level_spec`: rooms do not overlap, fit the bounds, are at least 12 studs, have ceilings at least 2.4x the player height;
connections lie on a shared wall with a 2-stud frame at each end; doors/openings need equal floors; ramps are at most 40 degrees; stairs steps do not
exceed the player's max step; ramps/stairs fit inside the lower room; placed items lie inside a room's free area. Everything is snapped to the 4-stud grid
(reported as a note). Ramps are built as 1-stud steps: walkable blockout stand-ins, not final art.

Tags: `spawn`, `objective`, `loot`, `secret`, `hub`, `safe` mark rooms that may be dead ends.
Template expressions: strings beginning with `=` are arithmetic over the template's parameters (`+ - * / // %`, `min max round int abs`).
