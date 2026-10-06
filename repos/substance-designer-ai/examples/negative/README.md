# Negative examples (avoid these)

Negative examples are retrieved separately (the `negative` list of a search) so an agent can steer away from them.
Each one explains *what is wrong* and *which correction dimension fixes it*.

| File | Library id | What is wrong | Correction |
|---|---|---|---|
| `stone_wall_too_noisy.png` | `stone-wall-too-noisy` | Many small stones plus per-pixel noise: reads photographic, not stylized (`detail_frequency` ~0.9 vs. the style's 0.45 limit). | `detail_density`: drop the fine noise; `shape_scale`: fewer, larger stones |
| `stone_wall_neon_palette.png` | `stone-wall-neon-palette` | Good shape, saturated green/magenta palette (palette adherence ~0.16 vs. the 0.35 minimum). | `palette`: stay in the cool-grey family |
| `stone_wall_visible_seam.png` | `stone-wall-visible-seam` | A brightness ramp does not wrap: `seam_score` ~9 vs. clean tiles ~1-1.5. | `tiling`: remove non-wrapping gradients |

The numbers come from `python -m sdai measure` on the synthetic images and are what the eval tasks
(`rubric-catches-*`) lock in.
