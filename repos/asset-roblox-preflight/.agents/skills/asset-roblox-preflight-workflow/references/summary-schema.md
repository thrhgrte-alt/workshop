# Hub summary schema `preflight-summary/1`

Our own schema: the real `blender_get_objects_summary` / `asset_validate` output shapes were not available, so map the hub output into this (a few lines). Numbers are **reported, not measured**.
Authoritative description with every field: the docstring of `preflight/domain/summary.py`. Examples: `examples/assets/good_summary_prop.json`, `bad_summary_prop.json`.

```json
{"format": "preflight-summary/1", "source": "blender_get_objects_summary", "file": "crate.fbx", "up_axis": "Y", "unit_scale": 1.0,
 "objects": [{"name": "prop_crate", "type": "MESH", "location": [0,0,0], "rotation_euler_deg": [0,0,0], "scale": [1,1,1], "dimensions": [1,1,1],
              "triangles": 12, "vertices": 24, "ngons": 0, "loose_vertices": 0, "non_manifold_edges": 0, "flipped_normal_faces": 0,
              "uv_layers": 1, "uv_out_of_range_fraction": 0.0, "uv_overlap_ratio": 0.0, "uv_coverage": 0.95, "texel_density_spread": 1.1,
              "origin_offset": [0,0,0], "materials": ["crate_mat"]}],
 "materials": [{"name": "crate_mat", "textures": [{"slot": "base_color", "path": "textures/a.png", "colorspace": "sRGB"}]}],
 "armatures": [{"name": "rig", "bones": [{"name": "root", "parent": null}, {"name": "spin", "parent": "root"}]}],
 "animations": [{"name": "spin", "frame_rate": 30, "frame_start": 1, "frame_end": 30}]}
```
Pass it as `summary_path` (a .json inside the allowed folders) or inline `summary`. Texture files are still looked up on disk. With an FBX path the summary supplies the objects; with a GLB/glTF/OBJ the file wins.
