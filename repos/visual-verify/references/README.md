# References

No documentation page was read while building this repository, and the numbers below were not checked against Roblox documentation.

| Subject | Source used | Status |
|---|---|---|
| sRGB to Lab (D65), CIE76 delta-E | standard formulas (IEC 61966-2-1 transfer function; CIE 1976 L*a*b*); checked against the well-known Lab values of sRGB red (53.24, 80.09, 67.20) and blue (32.30, 79.19, -107.86) in the evals | standard, hand-checked |
| Rec.709 luma weights, Otsu threshold, k-means, circular autocorrelation by FFT | textbook definitions, written out in `visualverify/domain/pixels.py` and `tiling.py` | standard |
| PBR channel conventions (albedo bounds, roughness/metalness as grayscale, tangent-space normals of length 1 with +Z out) | general metallic-roughness practice, NOT a Roblox document | placeholder: `verify_against_current_docs: true` on the `pbr.*` bounds |
| Roblox `SurfaceAppearance` map expectations, texture size limits, normal-map green-channel convention | not read | unknown: nothing here claims them |
| The hub's `screen_capture` result shape | MCP specification's image content block (from memory) and guesses | `schema_unverified` (see `samples/README.md`) |
