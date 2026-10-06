# Reading the estimates

All numbers come from `analysis.analyze` and the heuristic thresholds in `style/style.yaml`. They are estimates, not
measurements, and the thresholds are not Roblox limits. When something matters, profile on the target device.

| Metric | Meaning | If flagged |
|---|---|---|
| `particles_peak` | steady-state `Rate x average Lifetime` + burst counts, summed over emitters | lower rate/intensity; shorten lifetimes |
| `beam_segments_total`, `trails`, `lights` | counts against the platform budget | fewer / simpler |
| `screen_coverage` | estimated fraction of the screen covered at peak at gameplay distance | smaller/sparser/more transparent |
| `apparent_height_fraction` | largest element's height as a fraction of screen height (pinhole camera, FOV 70) | raise size or move the intended distance |
| `color_separation_min` | worst-case perceptual (Lab) difference between the effect's best colour and the test backgrounds | change hue/brightness, add glow |
| `luminance_contrast_min` | informational luminance ratio (ignores hue) | - |
| `attack_fraction` | fraction of life before transparency reaches 0.9 | make the fade-in quicker |
| `duration_seconds` | one-shot length until fully faded | keep ~0.25-2.5 s unless intended |
| `abrupt_end` | still visible at the end of life | fade transparency to ~1 and/or shrink to ~0 |
| `theme_distance` | Lab distance of the main colour to the nearest palette colour | pick palette colours |

`assumptions` lists properties the recipe did not set and the value the analysis assumed (Roblox's defaults are not
in the snapshot). Set them explicitly in the recipe to remove the assumption.
