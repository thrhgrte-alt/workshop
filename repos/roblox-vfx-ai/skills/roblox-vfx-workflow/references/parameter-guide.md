# Translating the user's words into recipe parameters

| User says | Dimension | `arcane_burst` | `ember_aura` | `energy_beam` | `sprint_trail` |
|---|---|---|---|---|---|
| bigger / smaller | `size` | `size` | `size` | `width` | `width` |
| more / fewer, denser, busier | `density` | `intensity` | `rate` | `segments` (smoothness only) | - |
| faster / slower | `timing` | `speed` | `rise_speed` | - | `lifetime` (shorter = snappier) |
| brighter / glowier / flatter | `glow` | `glow` | `glow`, `light_brightness`, `light_range` | `glow` | `glow` |
| different colour | `colour` | `color_primary`, `color_secondary` | `color_hot`, `color_cool` | `color_core`, `color_edge` | `color_a`, `color_b` |
| wider / narrower spread | `silhouette` | `spread` | - | `curve` | `width` |
| longer reach | `size` | - | - | `length` | - |
| cheaper (mobile) | `performance` | lower `intensity` | lower `rate`, `light_range` | lower `segments` | shorter `lifetime` |
| clearer / more readable | `readability` | raise `size`, raise `glow` | raise `size` | raise `width` | raise `width` |

Rules of thumb
- One or two parameters per iteration; report them as `old -> new`.
- "Cheaper" never means removing the fade-out; reduce counts, not polish.
- Colours: `[r, g, b]` in 0-1 (or `#rrggbb` inside a recipe). Prefer palette colours from retrieved examples.
- A request beyond the recipe's reach ("add a shockwave ring", "lightning forks") needs a new recipe.
- `texture` accepts the user's own `rbxassetid://<id>` (or a `rbxasset://` path). Leave empty for Roblox's default particle.
