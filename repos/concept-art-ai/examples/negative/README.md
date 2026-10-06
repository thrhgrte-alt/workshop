# Negative examples (avoid these)

Also **synthetic fixtures**. Each is stored with `"polarity": "negative"` and correction notes; search returns them as avoid-examples. A test confirms each one really fails the check its notes name.

| Image | Why it is bad | What fixes it |
|---|---|---|
| `env_flat_low_contrast.png` | One narrow value band: value range 0.02 against a minimum of 0.35 | Widen the value range; make the focal point the lightest or darkest area |
| `env_off_palette.png` | Cool overcast colours for a warm golden-hour direction: adherence to the intended palette ~0.37 | Follow the direction's palette |
| `env_near_duplicate.png` | Same layout and palette as the positive example with a tiny change: novelty ~0.001 | Change silhouette, lighting or composition, not a detail |
| `prop_fragmented_noise.png` | Noise everywhere: 19 silhouette components, edge density 0.58 | One clear mass; group detail near one area |
