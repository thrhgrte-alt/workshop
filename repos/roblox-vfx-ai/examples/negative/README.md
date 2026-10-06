# Negative examples (avoid these)

Negative examples come back in the separate `negative` list of a search so an agent can steer away from them. These are
the same recipes with deliberate defects (built with `hooks.apply_mutations`), and the eval suite proves each defect is caught.

| Sheet | Library id | What is wrong | Caught by | Fix dimension |
|---|---|---|---|---|
| `burst_too_heavy.png` | `burst-too-heavy` | 400 sparks in one burst (402 peak) | `particles_peak` over the mobile budget | `density`, `performance` |
| `aura_pops_out.png` | `aura-pops-out` | embers stay solid until they vanish | `abrupt_end` | `timing` |
| `burst_too_small.png` | `burst-too-small` | nothing bigger than ~0.15 studs | `apparent_height_fraction` below 0.008 | `size`, `readability` |
