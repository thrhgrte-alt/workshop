# Directions

A direction picks one option per axis. Environments: **silhouette** (tall_vertical, wide_horizontal, layered_diagonal, central_mass), **palette_scheme** (analogous_warm, analogous_cool, complementary,
triadic_muted, monochrome_accent), **lighting** (high_noon, golden_hour, overcast_soft, night_lit, backlit_fog), **composition** (thirds_left, centered_symmetry, leading_lines, framed_depth),
**materials** (weathered_stone, aged_wood, overgrown, metal_industrial, painted_plaster), **mood** (serene, ominous, whimsical, grand).
Props: **silhouette** (round_bulky, tall_slender, angular_wedge, asymmetric_hooked), **palette_scheme**, **material_focus** (wood, metal, stone, cloth_leather, mixed), **detail_level** (simple_chunky, medium, ornate),
**view** (three_quarter, front_side, top_down), **wear** (pristine, worn, damaged).

- Distance between two directions is the weighted number of axes on which they differ (palette and lighting count most for environments; silhouette for props). The set is chosen greedily so each new
  direction is as far as possible from the nearest one already chosen. Combinations that contradict each other are never produced.
- `min_axes_differing` >= 3 is the target. A warning means the locks leave too little room: say which axes are locked and offer to unlock one.
- A locked palette (3-8 `#rrggbb`) is used verbatim for every direction (first five become dominant, secondary, accent, shadow, highlight); otherwise each direction derives one from its scheme and a seeded hue.
- Option names are labels for the tool. Describe directions to the user in words ("low golden light, long shadows, warm stone").
