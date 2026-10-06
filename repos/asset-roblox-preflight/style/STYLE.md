# Style: naming and conventions (STARTER scheme)

> **This is a starter scheme, not yours.** The naming scheme and limits for your game were not supplied when this repository was built.
> Everything here is a default chosen by the tool's author so the checks have something to compare against. Edit `style/style.yaml`
> (names) and `rules/profiles/*.yaml` (numbers) to your own conventions, then run `python -m preflight eval run --label mine` and
> `python -m pytest` to see what moved.

## Names

| Thing | Starter rule | Example |
|---|---|---|
| Visible prop mesh | `prop_` + lower snake_case | `prop_crate`, `prop_ore_chunk_a` |
| Visible tool mesh | `tool_` + lower snake_case | `tool_drill` |
| Visible accessory mesh | `acc_` + lower snake_case | `acc_miner_helmet` |
| Visible terrain piece | `terrain_` + lower snake_case | `terrain_cliff_slab` |
| Collision proxy | the mesh name plus `_col` | `prop_crate_col` |
| Bones | lower snake_case, one root bone named `root` | `root`, `spin` |
| Animation clips | lower snake_case, never `Action` or `Take 001` | `spin`, `idle` |

Allowed characters in object names: letters, digits, underscore, dot, dash. Spaces and other characters are flagged (`NAME_INVALID_CHARS`);
a trailing `.001` (Blender's copy suffix) is flagged (`NAME_BLENDER_SUFFIX`). Renames of those two kinds are the "safe" fixes.

## The one-bone `spin` setup (drill tips)

A spinning part is driven by exactly one bone named `spin`, a direct child of the root bone, with no child bones, and every vertex of the
spinning part is weighted fully (1.0) to it. The rule applies to assets whose name matches `drill|spin` (see `RIG_SPIN_SETUP` limits) or that
have a bone that looks like a spin bone.

## Transforms and origin

Apply scale and rotation before export. Props and terrain pieces put the origin at the centre of the base; tools and accessories are not checked
for origin (the grip or attachment point is your choice). Mirrored (negative) scale is an error.

## Limits

Triangle, texture, bone and file-size numbers live in `rules/*.yaml` and `rules/profiles/*.yaml`. All are labelled defaults with
`verify_against_current_docs`. Replace them with your own verified numbers.
