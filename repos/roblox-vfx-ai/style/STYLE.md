# Style guide (human-editable)

Companion to `style.yaml`. **The shipped content is a starter template**, not your game's style. Replace it with how
your effects actually look and feel.

## Template direction
- **Readability first:** at gameplay distance the effect must be recognisable in a glance, and never hide the player,
  targets or UI.
- **Clean timing:** fast attack, eased fade, no popping. One-shots finish in under ~2.5 s.
- **Colour:** two or three related colours; a bright core, a more saturated accent; glow (`LightEmission`) on cores and
  sparks, less on smoke.
- **Layering:** core + body + accents. Prefer few well-shaped particles over many tiny ones.
- **Budget:** respect the platform numbers in `style.yaml` (heuristic defaults; tune on real devices).

## Calibrating
1. Collect effects you consider on style; record them as recipes or variants.
2. `python -m rbxvfx analyze <recipe> --platform mobile` shows the numbers the checks use.
3. Adjust `ranges` and `platforms` in `style.yaml` to just outside what your best effects measure; add an eval task.

## Failure vocabulary (becomes negative examples)
| Failure | Looks like | Fix dimension |
|---|---|---|
| Too busy | hundreds of tiny identical particles | `density` |
| Pops out | particles vanish while still visible | `timing` |
| Invisible | nothing bigger than a few pixels at distance | `size`, `readability` |
| Muddy | same hue and value as the ground | `colour`, `glow` |
| Heavy | exceeds the mobile budget | `performance` |
| Smothering | covers the player or target | `size`, `density` |

Corrections use: `timing`, `size`, `colour`, `glow`, `density`, `silhouette`, `performance`, `readability`, `layering`.
