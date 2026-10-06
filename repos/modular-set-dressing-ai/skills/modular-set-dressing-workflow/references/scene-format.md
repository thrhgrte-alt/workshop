# Scene format

JSON (or YAML). Units are studs. `x`/`z` are horizontal, `y` is up.

```json
{"id": "tavern_corner",
 "region": {"x": 0, "z": 0, "w": 40, "d": 30, "floor_y": 0},
 "grid": {"step": 1.0, "yaw_step": 90.0},
 "exclusions": [[17, 0, 6, 3]],
 "paths": [{"id": "entrance", "points": [[20, 3], [20, 14], [34, 14]], "width": 5}],
 "corridors": [{"id": "bar_view", "from": [20, 28], "to": [31, 5], "width": 2.5, "eye_height": 4.5}],
 "focal": [{"id": "hearth", "at": [2, 15], "radius": 10, "min_priority": 4}],
 "instances": [{"id": "hearth_001", "module": "fireplace", "at": [2, 15], "y": 0, "yaw": 90, "scale": 1.0, "locked": true, "why": "locked hero piece"}],
 "locked": {"instances": []}}
```

- `region`: the dressing rectangle `[x, z]` to `[x+w, z+d]`; every footprint must fit inside.
- `exclusions`: rectangles `[x, z, w, d]` nothing may overlap.
- `paths`: polylines that must stay walkable at `width`; anything solid below 5 studs above the floor blocks them.
- `corridors`: sightlines that must stay open at `eye_height`; a solid whose height range contains it blocks the line.
- `focal`: each wants a module with `priority >= min_priority` within `radius`.
- `instances`: `id` and `module` must match `[A-Za-z][A-Za-z0-9_]*` and the module must be in the kit. `at` is `[x, z]` of the module pivot; `yaw` in degrees
  (`CFrame.Angles(0, rad(yaw), 0)`; local +Z is forward, yaw turns +Z toward +X); `scale` must lie in the module's range.
- Locked: `locked: true` on an instance, or ids under `locked.instances`. The first saved version is the baseline.

Plans: `{"adds": [instance...], "moves": [{"id", "from", "to"}], "removes": [id...]}`; `invert` swaps them. Scene hashes ignore instance order.
