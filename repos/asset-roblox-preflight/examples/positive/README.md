# Positive examples (must pass cleanly)

Synthetic exports built by `scripts/make_examples.py` (see `preflight/domain/fixtures.py`). They are test inputs, **not real Blender exports**. Library entries (`kind: good_export`) are in
`examples/library/assets.jsonl`; their expected results are re-checked by a test.

| File | Profile | What to follow |
|---|---|---|
| `assets/good_prop_crate.glb` | prop | Applied transforms, bottom-centre origin, outward normals, UVs filling 0-1 without overlap, 256 px power-of-two textures, starter-scheme name `prop_crate` |
| `assets/good_prop_crate_with_proxy.glb` | prop | The same plus a cheap collision proxy `prop_crate_col` |
| `assets/good_prop_barrel.gltf` | prop | glTF with external textures under `assets/textures/` |
| `assets/good_tool_drill.glb` | tool | Skeleton `root` > `spin`, tip weighted 1.0 to `spin`, clip `spin` at 30 fps |
| `assets/good_accessory_hat.glb` | character_accessory | Small static accessory |
| `assets/good_terrain_slab.glb` | terrain_piece | 16x4x16 slab, base-centre origin |
| `assets/good_prop_crate_obj.obj` | prop | OBJ + MTL + PNG |
| `assets/good_summary_prop.json` | prop | A hub summary of a clean crate (schema `preflight-summary/1`) |

Replace with your own approved exports in your private library (`PREFLIGHT_ASSET_ROOT`).
