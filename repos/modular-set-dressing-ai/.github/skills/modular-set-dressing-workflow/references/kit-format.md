# Kit format

```yaml
kit: {id: tavern_kit, units: studs, template_root: ReplicatedStorage.Kit}
defaults: {pivot: bottom_center, collision: true, scale: {min: 0.9, max: 1.15}}   # stated kit-wide, so not reported as unresolved
modules:
  - id: chair
    footprint: [2, 2]          # width (x), depth (z) in studs, required
    height: 3.5                # required
    pivot: bottom_center       # bottom_center | center | corner
    tags: [furniture, seat]
    material: wood
    color: "#a8743a"
    priority: 1                # 0-5; focal points ask for a minimum
    collision: true
    scale: {min: 0.9, max: 1.15}
    sockets:
      - {name: front, pos: [0, 0, 1], yaw: 180, type: seat}   # pos in module space, yaw = outward direction, mates defaults to [type]
```

`kit_report` lists every module whose pivot, collision, material, colour, tags or scale were **not stated** (they are defaulted). Treat defaulted values as assumptions and fix the kit.
Set `SETDRESS_KIT=/path/to/kit.yaml` to use your own. Bad footprints, heights, pivots, scales and colours are errors; modules without tags and sockets that mate with nothing are warnings.
