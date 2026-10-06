# Reading the rubric

`compare_material_to_rubric` returns `measurements`, `metrics`, `findings` and a `rubric` block.

| Criterion | How it is decided | What a failure usually means |
|---|---|---|
| `maps_present` | auto | An output usage in `style.required_maps` was not exported |
| `tileable` | auto: `*_seam_score` <= 2.0 | A gradient or edge does not wrap (clean tiles score ~1-1.5, a hard seam ~9) |
| `normal_valid` | auto: decoded length and facing | Normal map is flat grey, wrong format, or inverted |
| `value_contrast` | auto | p95-p5 luminance outside the style range |
| `palette_match` | auto: Lab-space similarity to `traits.palette_hex` | Colours outside the style's families |
| `detail_level` | auto: share of high-frequency energy | Too noisy for a low-detail style |
| `roughness_range` | auto | Roughness mean/spread outside the band |
| `reads_as_intended` | manual | Does it look like the requested material at a glance? |
| `matches_style` | hybrid: measured ranges AND a manual verdict (lower wins) | The look is off even if numbers pass |
| `graph_quality` | manual | Organisation and exposed parameters |

## Rules
- `rubric.complete` is false until every criterion has a score. Report `unscored` plainly; do not fill
  manual criteria with optimistic guesses.
- `rubric.passed` requires complete + all `required` criteria passing + score >= `pass_at`.
- Metrics are *smoke alarms calibrated on a few examples*. Passing them does not make a material good.
  Failing one means "look closer", with the metric name telling you where.
- When the user disagrees with the rubric, the user wins. Record the correction; if a metric was wrong,
  propose a change to `style/style.yaml` ranges (with measurements) and an eval task.
