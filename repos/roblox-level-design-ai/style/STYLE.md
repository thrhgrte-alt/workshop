# Style guide (human-editable)

Companion to `style.yaml`. **The shipped content is a starter template**, not your design philosophy.

## Template direction
- **Readable:** landmarks along the main routes; no long featureless stretches; doors staggered so sightlines stay short.
- **Fair:** teams reach the objective in similar walking distance; enemy spawns cannot see each other and are far apart.
- **Choice:** contested spaces have two or more independent routes; dead ends hold something (loot, secret, safe room).
- **Paced:** encounters spaced along the route, intensity rising toward the objective; no ambush at the spawn.
- **Buildable:** 4-stud grid, 2-stud walls, 16-stud ceilings, 8-stud doors; blockouts under 800 parts.

## Character assumptions (verify!)
`player` in `style.yaml`: height 5, eye 4.5, width 2, max step 1.5, jump height 7.2. These are defaults, not facts about your game: set your own values, and
check them against your Humanoid/character settings. Every evaluation reports which values it assumed.

## Calibrating
Run `python -m rbxlevel evaluate <spec.json>` on layouts you consider good; set `ranges` just outside what they measure; add an eval task. Thresholds are smoke alarms.

## Failure vocabulary (becomes negative examples)
| Failure | Looks like | Dimension |
|---|---|---|
| Exposed spawns | doors on the centre line | `sightlines`, `spawns` |
| No flanking | one route through one room | `routes` |
| Empty dead ends | rooms with nothing in them | `density`, `routes` |
| Lost player | no landmark in view for 50+ studs | `landmarks`, `readability` |
| Unfair | one team 40% closer | `fairness` |
| Wall of fights | no rest between encounters | `pacing` |

Corrections use: `scale`, `routes`, `sightlines`, `landmarks`, `pacing`, `fairness`, `verticality`, `spawns`, `readability`, `density`.
