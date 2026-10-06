# Translating the user's words into recipe parameters

Use the recipe's declared ranges (`list_recipes`). When the user's wording is a correction dimension from
`style/style.yaml`, use that dimension name when you record feedback.

| User says | Correction dimension | `stylized_stone_wall` | `hand_painted_wood_planks` | `stylized_pebble_ground` |
|---|---|---|---|---|
| chunkier / bigger stones | `shape_scale` | lower `brick_columns`, `brick_rows` | lower `plank_count` | lower `pebble_density` |
| smaller / finer shapes | `shape_scale` | raise `brick_columns`, `brick_rows` | raise `plank_count` | raise `pebble_density` |
| rounder / softer edges | `bevel_roundness` | raise `bevel_softness` | raise `edge_softness` | raise `pebble_size` (softer rim: raise `rim_width`) |
| sharper edges | `bevel_roundness` | lower `bevel_softness` | lower `edge_softness` | lower `rim_width` |
| less noise / calmer | `detail_density` | lower `surface_variation` | lower `grain_warp` | lower `grain_strength` |
| deeper gaps / more contrast | `contrast` | raise `height_contrast` | raise `gap_depth` | raise `pebble_size` |
| cooler / warmer / different colour | `palette` | `stone_color_light`, `stone_color_dark` | `wood_color_light`, `wood_color_dark` | `ground_color_a/b`, `rim_color` |
| shinier / matter | `roughness` | `roughness_level` | `roughness_level` | `roughness_level` |

## Rules of thumb
- Change one or two parameters per iteration so the user can tell what caused the difference.
- Colours are `[r, g, b, a]` in 0-1. Pull hex values from retrieved examples' `style.palette` and
  convert; do not invent palettes outside the style's families unless the brief asks.
- Express a change as a number relative to the previous value (for example "bevel_softness 2.0 -> 3.5") so
  the user can reverse it. `set_validated_parameters` returns the diff.
- A request outside the recipe's reach ("add moss", "cracks") needs a new recipe, not a hack.
