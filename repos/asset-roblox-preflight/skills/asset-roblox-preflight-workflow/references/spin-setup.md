# The one-bone `spin` setup (drill tips)

A spinning part is driven by exactly one bone named `spin`: a direct child of the single root bone, with no child bones, owning every vertex of the spinning part at weight 1.0, animated by a clip
(for example `spin`). Required when the asset name matches `drill|spin`; also checked whenever a bone looks like a spin bone.
Findings (rule `RIG_SPIN_SETUP`): near-miss name (`Spin_Bone`, safe rename to `spin`), missing, several, wrong parent, child bones, drives no vertices, blended weights (manual fix).
Only the tool and character_accessory profiles run it. Weights can be checked from GLB, not from a summary.
