# Positive examples (follow these)

**Synthetic fixtures** drawn by `scripts/make_examples.py` from a direction's palette and axes. They are *not* generated art and say nothing about what a model can do; they give the
measurement and retrieval code deterministic cases. Their measurements are stored in `examples/library/assets.jsonl` and re-checked by a test.

| Image | Library id | What to follow |
|---|---|---|
| `env_lighthouse_golden.png` | `env-lighthouse-golden-001` | Wide value range (> 0.6), warm analogous palette that matches its direction (adherence ~0.8), a dark mass against a light sky, an accent at the focal point |
| `prop_lantern_round.png` | `prop-lantern-round-001` | One bulky silhouette that reads against the background, complementary palette with a single accent, edge density inside the style range |

Replace with your own accepted concepts in your private library (`CONCEPTAI_ASSET_ROOT`). Briefs for these cases are in `examples/briefs/`.
