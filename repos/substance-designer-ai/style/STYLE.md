# Style guide (human-editable)

This file is the human companion to `style.yaml`. Edit both when your style changes; tests check that
`style.yaml` stays valid, and the evals lock behaviour in.

> **The shipped content is a starter template**, not your style. It describes a chunky, readable,
> low-detail look. Replace it with what your own approved materials actually look like.

## What "my style" means here (template)

- **Shapes:** big, readable forms. A stone wall is a handful of chunky blocks, not hundreds of small stones.
- **Bevels:** soft and rounded. If an edge could cut you, it is too sharp.
- **Detail:** low. Gentle surface unevenness is welcome; micro-scratches and photo-grunge are not.
- **Colour:** two-tone palettes with a clear light/dark separation (raised faces vs crevices).
- **Roughness:** high and fairly uniform. No mirror patches.
- **Graph hygiene:** frames per stage, a few well-named exposed parameters.

## Too realistic / too noisy / too generic / unlike my style

Fill these in with your own words and pictures. They become negative examples in
`examples/negative/` and corrections in the feedback log.

| Failure | What it looks like | What to change |
|---|---|---|
| Too realistic | Fine cracks, photographic colour variation | Fewer layers; lower detail; palette from `style.yaml` |
| Too noisy | Salt-and-pepper detail visible at 25% size | Blur/level the height; drop the fine noise blend |
| Too generic | Default tile + default noise, no character | Pick a recipe, push shape scale and bevel roundness |
| Unlike my style | (describe) | (describe) |

## Calibrating the ranges

The numeric `ranges` in `style.yaml` are measurements of exported maps. To calibrate them to your work:

1. Export maps for 5-10 materials you consider *on style*.
2. `python -m sdai measure --maps baseColor=... normal=... roughness=... height=...` for each.
3. Set each range to just outside the spread you see (and re-run `python -m sdai eval run`).

A range is a smoke alarm, not a judge: a material can pass every range and still look wrong, and the
rubric keeps the "does it look right" criteria as explicit manual judgments.

## Correction vocabulary

Corrections use these dimensions so feedback can be retrieved later:
`shape_scale`, `bevel_roundness`, `edge_wear`, `palette`, `contrast`, `roughness`, `detail_density`,
`tiling`, `graph_organization`. Example: `edge_wear=less wear on the top edges of the stones`.
