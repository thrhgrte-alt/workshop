# Style: starter pacing bands (PLACEHOLDER - replace)

Human-readable companion to `style.yaml` (the machine-readable source). **The user has not supplied target pacing.** Everything here is a starter value chosen only so the tools have something to compare against.
Replace the global bands, or give a place its own in `projects/<project_id>/<place_id>/bands.yaml` (which replaces `target_bands` and may override `ranges` and `learning` for that place only).

## Target bands (active minutes from the previous purchase to the next, reference archetype `regular`)
| Band | Tiers | Minutes | Status |
|---|---|---|---|
| early | 1-3 | 5-15 | PLACEHOLDER |
| mid | 4-6 | 15-45 | PLACEHOLDER |
| late | 7 and up | 45-120 | PLACEHOLDER |

## Thresholds (all PLACEHOLDER)
- No cost step above 5x the previous step in a track; no step more than 4x slower than the one before.
- A step slower than 3x its band maximum is a **wall** (an error). Where no band covers a tier, 240 active minutes.
- Any archetype: at most 7 calendar days for one step.
- An upgrade should repay its cost within 240 active minutes for at least one archetype.
- Content should last at least 7 calendar days for the reference archetype; after it runs out, at most 80 percent of income may be left unspent in the final quarter of the horizon.
- Boosts speed a tier by at most 3x; free players stay inside 2x the band maximum where boosted play is inside the band.
- A choice is dominated when one option repays at least 10 percent faster than every alternative for every archetype.

## Measured vs judged
Everything above is **measured** by the simulator. Whether the pacing feels right, whether the bands and archetypes describe your players, and whether a boost is fair are **judged by a person**
(`bands_confirmed`, `archetypes_confirmed`, `matches_design_intent` stay unscored in `evals/rubric.yaml` until someone scores them).

## Learning
`learning.min_votes` (2) playtest reports are needed before `suggest_band_adjustments` proposes a band change; "too slow" lowers a band maximum to (1 - 0.2) x the simulated step, "too fast" raises its minimum to (1 + 0.25) x.
