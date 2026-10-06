# Negative examples (must be caught)

Each file plants one defect (a few plant two). Library entries have `"polarity": "negative"`, correction notes and the rules a test expects (all severities). Synthetic, not real exports.

| File | Profile | Expected rules |
|---|---|---|
| `assets/bad_summary_prop.json` | prop | COL_PROXY_MISSING, MESH_FLIPPED_NORMALS, MESH_LOOSE_GEOMETRY, MESH_NGONS, MESH_NON_MANIFOLD, MESH_TRI_BUDGET, NAME_INVALID_CHARS, NAME_SCHEME, TEX_COLORSPACE, UPL_REPORTED_ONLY, UV_OUT_OF_RANGE, UV_OVERLAP, UV_TEXEL_SPREAD, UV_WASTED_SPACE, XFM_AXIS_UP, XFM_ORIGIN_PLACEMENT, XFM_UNAPPLIED_SCALE |
| `assets/sample_binary_header.fbx` | prop | UPL_NOT_MEASURED |
| `assets/bad_flipped_normals.glb` | prop | MESH_FLIPPED_NORMALS |
| `assets/bad_inverted_winding.glb` | prop | MESH_FLIPPED_NORMALS |
| `assets/bad_inside_out.glb` | prop | MESH_FLIPPED_NORMALS |
| `assets/bad_unapplied_scale.glb` | prop | XFM_UNAPPLIED_SCALE |
| `assets/bad_unapplied_rotation.glb` | prop | XFM_UNAPPLIED_ROTATION |
| `assets/bad_negative_scale.glb` | prop | XFM_NEGATIVE_SCALE |
| `assets/bad_texture_4k.glb` | prop | TEX_OVERSIZE, UPL_TEXTURE_MEMORY |
| `assets/bad_texture_non_pot.glb` | prop | TEX_NON_POT |
| `assets/bad_missing_texture.gltf` | prop | TEX_MISSING_FILE |
| `assets/bad_absolute_texture_path.gltf` | prop | TEX_UNSAFE_PATH |
| `assets/bad_normal_as_color.glb` | prop | TEX_COLORSPACE |
| `assets/bad_grey_normal.glb` | prop | TEX_CHANNELS, TEX_SLOT_HEURISTIC |
| `assets/bad_duplicate_names.glb` | prop | NAME_DUPLICATE, NAME_DUPLICATE_MATERIAL |
| `assets/bad_names_spaces.glb` | prop | NAME_BLENDER_SUFFIX, NAME_INVALID_CHARS, NAME_SCHEME |
| `assets/bad_spin_bone_name.glb` | tool | RIG_SPIN_SETUP |
| `assets/bad_spin_missing.glb` | tool | RIG_SPIN_SETUP |
| `assets/bad_spin_wrong_parent.glb` | tool | RIG_SPIN_SETUP |
| `assets/bad_spin_blended.glb` | tool | RIG_SPIN_SETUP |
| `assets/bad_rig_two_roots.glb` | tool | RIG_ROOT_SINGLE |
| `assets/bad_rig_too_many_bones.glb` | tool | RIG_BONE_COUNT |
| `assets/bad_anim_names_fps.glb` | tool | RIG_ANIM_FPS, RIG_ANIM_NAMES |
| `assets/bad_too_many_tris.glb` | prop | COL_PROXY_MISSING, MESH_TRI_BUDGET |
| `assets/bad_tris_10800.glb` | prop | COL_PROXY_MISSING, MESH_TRI_BUDGET |
| `assets/bad_no_uvs.glb` | prop | UV_MISSING |
| `assets/bad_uv_out_of_range.glb` | prop | UV_OUT_OF_RANGE |
| `assets/bad_uv_overlap.glb` | prop | UV_OVERLAP, UV_WASTED_SPACE |
| `assets/bad_uv_density.glb` | prop | UV_TEXEL_SPREAD, UV_WASTED_SPACE |
| `assets/bad_uv_zero_area.glb` | prop | UV_ZERO_AREA |
| `assets/bad_open_mesh.glb` | prop | MESH_LOOSE_GEOMETRY, MESH_NON_MANIFOLD |
| `assets/bad_loose_geometry.glb` | prop | MESH_LOOSE_GEOMETRY |
| `assets/bad_units_cm.glb` | prop | MESH_BBOX_PROFILE, XFM_UNITS_SUSPECT |
| `assets/bad_units_tiny.glb` | prop | MESH_SCALE_RANGE, XFM_UNITS_SUSPECT |
| `assets/bad_origin_center.glb` | prop | XFM_ORIGIN_PLACEMENT |
| `assets/bad_root_offset.glb` | prop | XFM_ROOT_OFFSET |
| `assets/bad_axis_rotated_root.glb` | prop | XFM_AXIS_UP |
| `assets/bad_proxy_heavy.glb` | prop | COL_PROXY_BUDGET |
| `assets/bad_proxy_bounds.glb` | prop | COL_PROXY_BOUNDS |
| `assets/bad_many_materials.glb` | prop | MAT_COUNT |
| `assets/bad_unused_material.glb` | prop | MAT_UNUSED |
| `assets/bad_no_geometry.glb` | prop | MESH_NO_GEOMETRY |
| `assets/bad_unsupported_features.glb` | prop | UPL_UNSUPPORTED_FEATURE |
| `assets/bad_draco_compressed.glb` | prop | (load error) |
| `assets/bad_truncated.glb` | prop | (load error) |
| `assets/bad_ngon_obj.obj` | prop | MESH_NGONS, MESH_NON_MANIFOLD, UV_MISSING_UNTEXTURED |
| `assets/bad_obj_missing_texture.obj` | prop | TEX_MISSING_FILE |

Edge cases (`kind: export_case`): `bad_draco_compressed.glb` and `bad_truncated.glb` must be refused with a clear message; `sample_binary_header.fbx` is an FBX header only and is never ready.
