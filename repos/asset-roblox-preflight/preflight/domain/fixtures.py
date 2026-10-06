"""The catalogue of synthetic test assets (built by scripts/make_examples.py). Each entry says what the file is meant to demonstrate."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from . import synth
from .synth import GltfBuilder, box, cylinder, merge, png_bytes, triangle_mesh

ALBEDO = png_bytes(256, 256, (200, 120, 60))
NORMAL = png_bytes(256, 256, synth.NORMAL_FLAT)
CATALOG: dict[str, dict] = {}


def register(name: str, profile: str, kind: str, summary: str):
    def deco(fn):
        CATALOG[name] = {"fn": fn, "profile": profile, "kind": kind, "summary": summary}
        return fn

    return deco


def _crate(name="prop_crate", *, geo=None, textures="embedded", albedo=ALBEDO, normal=NORMAL, node_kw=None, mat_name="crate_mat", gen=None):
    b = GltfBuilder()
    geo = geo or box((1.0, 1.0, 1.0))
    mat = None
    if textures == "embedded":
        mat = b.material(mat_name, base=b.image(albedo, f"{name}_albedo"), normal=b.image(normal, f"{name}_normal") if normal else None)
    elif textures == "none":
        mat = b.material(mat_name)
    m = b.mesh(name, geo, mat)
    b.node(name, mesh=m, **(node_kw or {}))
    return b


def _tex_files(out: Path, stem: str):
    t = out / "textures"
    t.mkdir(parents=True, exist_ok=True)
    (t / f"{stem}_albedo.png").write_bytes(ALBEDO)
    (t / f"{stem}_normal.png").write_bytes(NORMAL)


def _drill(*, spin_name="spin", root_name="root", extra_bones=0, spin_parent="root", weights_tip=1.0, clip="spin", fps=30.0, two_roots=False, with_spin=True, name="tool_drill"):
    """A drill: handle and tip cylinders skinned to root and spin. spin_parent 'arm' puts spin under the last extra bone."""
    b = GltfBuilder()
    handle = cylinder(0.15, 1.2, 12, y0=0.0, uv_box=(0.0, 0.0, 0.5, 1.0))
    tip = cylinder(0.1, 0.5, 12, y0=1.2, uv_box=(0.5, 0.0, 1.0, 1.0))
    geo = merge(handle, tip)
    nh, nt = len(handle["positions"]), len(tip["positions"])
    mat = b.material("drill_mat", base=b.image(ALBEDO, "drill_albedo"))

    def child_of(parent: int, node: int):
        b.doc["nodes"][parent].setdefault("children", []).append(node)

    root = b.node(root_name, root=False)
    bones = [root]
    last = root
    for i in range(extra_bones):
        n = b.node(f"bone_{i + 1}", root=False)
        child_of(last, n)
        bones.append(n)
        last = n
    rig_children = [root]
    if two_roots:
        r2 = b.node("root_2", root=False)
        bones.append(r2)
        rig_children.append(r2)
    spin_idx = 0
    if with_spin:
        spin = b.node(spin_name, root=False)
        child_of(last if spin_parent == "arm" else root, spin)
        bones.append(spin)
        spin_idx = len(bones) - 1
    b.node("rig", children=rig_children)
    sk = b.skin(bones)
    joints = [(0, 0, 0, 0)] * nh + [(spin_idx, 0, 0, 0)] * nt
    weights = [(1.0, 0.0, 0.0, 0.0)] * nh + [(weights_tip, 1.0 - weights_tip, 0.0, 0.0)] * nt
    m = b.mesh(name, geo, mat, joints=joints, weights=weights)
    b.node(name, mesh=m, skin=sk)
    if clip and with_spin:
        b.animation(clip, bones[spin_idx], fps, 30)
    return b


@register("good_prop_crate", "prop", "good", "A textured unit crate: applied transforms, bottom-centre origin, clean UVs, 256px albedo and normal maps. Must pass cleanly.")
def _(out):
    return _crate().write_glb(out / "good_prop_crate.glb")


@register("good_prop_crate_with_proxy", "prop", "good", "The crate plus a low-poly collision proxy named prop_crate_col.")
def _(out):
    b = _crate()
    pm = b.mesh("prop_crate_col", box((1.0, 1.0, 1.0), uv="none"))
    b.node("prop_crate_col", mesh=pm)
    return b.write_glb(out / "good_prop_crate_with_proxy.glb")


@register("good_prop_barrel", "prop", "good", "A barrel as .gltf with external textures in textures/ (exercises relative paths).")
def _(out):
    b = GltfBuilder()
    geo = cylinder(0.4, 1.0, 16, uv_box=(0.0, 0.0, 1.0, 1.0))
    mat = b.material("barrel_mat", base=b.image(ALBEDO, "barrel_albedo", external="textures/barrel_albedo.png"), normal=b.image(NORMAL, "barrel_normal", external="textures/barrel_normal.png"))
    b.node("prop_barrel", mesh=b.mesh("prop_barrel", geo, mat))
    return b.write_gltf(out / "good_prop_barrel.gltf")


@register("good_tool_drill", "tool", "good", "A drill with root > spin bones (one-bone spin setup), a 30 fps clip named spin, rigid weights. Must pass cleanly.")
def _(out):
    return _drill().write_glb(out / "good_tool_drill.glb")


@register("good_accessory_hat", "character_accessory", "good", "A small static hat-sized accessory with one texture.")
def _(out):
    b = _crate("acc_hat", geo=box((0.6, 0.4, 0.6)))
    return b.write_glb(out / "good_accessory_hat.glb")


@register("good_terrain_slab", "terrain_piece", "good", "A 16x4x16 slab with a bottom-centre origin and subdivided faces.")
def _(out):
    b = _crate("terrain_slab", geo=box((16.0, 4.0, 16.0), sub=4))
    return b.write_glb(out / "good_terrain_slab.glb")


@register("good_prop_crate_obj", "prop", "good", "The crate as OBJ + MTL + PNG textures (OBJ reader path).")
def _(out):
    _tex_files(out, "crate_obj")
    g = box((1.0, 1.0, 1.0))
    g["material"] = "crate_mat"
    mtl = "newmtl crate_mat\nKd 1 1 1\nmap_Kd textures/crate_obj_albedo.png\nmap_Bump textures/crate_obj_normal.png\n"
    return synth.write_obj(out / "good_prop_crate_obj.obj", {"prop_crate_obj": g}, mtl="good_prop_crate_obj.mtl", mtl_text=mtl)


@register("good_summary_prop", "prop", "good", "A hub summary (preflight-summary/1) of a clean crate, with the texture files on disk.")
def _(out):
    _tex_files(out, "summary_crate")
    doc = {"format": "preflight-summary/1", "source": "blender_get_objects_summary (hand-written synthetic example)", "file": "prop_crate.fbx", "up_axis": "Y", "unit_scale": 1.0,
           "objects": [{"name": "prop_crate", "type": "MESH", "parent": None, "location": [0, 0, 0], "rotation_euler_deg": [0, 0, 0], "scale": [1, 1, 1], "dimensions": [1, 1, 1],
                        "triangles": 12, "vertices": 24, "ngons": 0, "quads": 6, "loose_vertices": 0, "degenerate_faces": 0, "non_manifold_edges": 0, "flipped_normal_faces": 0,
                        "uv_layers": 1, "uv_out_of_range_fraction": 0.0, "uv_overlap_ratio": 0.0, "uv_coverage": 0.95, "texel_density_spread": 1.1, "origin_offset": [0, 0, 0], "materials": ["crate_mat"]}],
           "materials": [{"name": "crate_mat", "textures": [{"slot": "base_color", "path": "textures/summary_crate_albedo.png", "colorspace": "sRGB"},
                                                            {"slot": "normal", "path": "textures/summary_crate_normal.png", "colorspace": "Non-Color"}]}]}
    p = out / "good_summary_prop.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return p


@register("bad_summary_prop", "prop", "bad", "A hub summary reporting a heavy mesh with n-gons, unapplied scale, a duplicate name and a normal map declared sRGB.")
def _(out):
    _tex_files(out, "summary_bad")
    doc = {"format": "preflight-summary/1", "source": "asset_validate (hand-written synthetic example)", "file": "crate.fbx", "up_axis": "Z", "unit_scale": 1.0,
           "objects": [{"name": "Crate One", "type": "MESH", "parent": None, "location": [0, 0, 0], "rotation_euler_deg": [0, 0, 0], "scale": [2, 2, 2], "dimensions": [1, 1, 1],
                        "triangles": 14000, "vertices": 9000, "ngons": 6, "quads": 100, "loose_vertices": 4, "degenerate_faces": 0, "non_manifold_edges": 8, "flipped_normal_faces": 300,
                        "uv_layers": 1, "uv_out_of_range_fraction": 0.2, "uv_overlap_ratio": 0.3, "uv_coverage": 0.1, "texel_density_spread": 9.0, "origin_offset": [0, 0.5, 0],
                        "materials": ["crate_mat"]}],
           "materials": [{"name": "crate_mat", "textures": [{"slot": "base_color", "path": "textures/summary_bad_albedo.png", "colorspace": "sRGB"},
                                                            {"slot": "normal", "path": "textures/summary_bad_normal.png", "colorspace": "sRGB"}]}]}
    p = out / "bad_summary_prop.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return p


@register("sample_binary_header", "prop", "bad", "A file that starts with the binary FBX magic and version 7400 and nothing else. Only the header can be read; the preflight must say so and never say ready.")
def _(out):
    p = out / "sample_binary_header.fbx"
    p.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400) + b"\x00" * 64)
    return p


@register("bad_flipped_normals", "prop", "bad", "Crate whose vertex normals all point inward (winding unchanged).")
def _(out):
    return _crate(geo=box((1, 1, 1), flip_normals=True)).write_glb(out / "bad_flipped_normals.glb")


@register("bad_inverted_winding", "prop", "bad", "Crate with reversed triangle winding but outward normals (winding and normals disagree).")
def _(out):
    return _crate(geo=box((1, 1, 1), invert_winding=True)).write_glb(out / "bad_inverted_winding.glb")


@register("bad_inside_out", "prop", "bad", "Crate with reversed winding AND flipped normals: they agree with each other but the closed mesh has negative volume.")
def _(out):
    return _crate(geo=box((1, 1, 1), invert_winding=True, flip_normals=True)).write_glb(out / "bad_inside_out.glb")


@register("bad_unapplied_scale", "prop", "bad", "A 0.5 m crate with object scale 2 (not applied): it looks 1 m big but the object says scale 2.")
def _(out):
    return _crate(geo=box((0.5, 0.5, 0.5)), node_kw={"s": (2, 2, 2)}).write_glb(out / "bad_unapplied_scale.glb")


@register("bad_unapplied_rotation", "prop", "bad", "Crate with a 45 degree rotation left on the object.")
def _(out):
    a = math.radians(45)
    return _crate(node_kw={"r": (0, math.sin(a / 2), 0, math.cos(a / 2))}).write_glb(out / "bad_unapplied_rotation.glb")


@register("bad_negative_scale", "prop", "bad", "Crate mirrored by a negative X scale.")
def _(out):
    return _crate(node_kw={"s": (-1, 1, 1)}).write_glb(out / "bad_negative_scale.glb")


@register("bad_texture_4k", "prop", "bad", "Crate whose albedo is a real 4096x4096 PNG (solid colour so the file stays small).")
def _(out):
    return _crate(albedo=png_bytes(4096, 4096, (200, 120, 60))).write_glb(out / "bad_texture_4k.glb")


@register("bad_texture_non_pot", "prop", "bad", "Crate with a 300x200 albedo.")
def _(out):
    return _crate(albedo=png_bytes(300, 200, (200, 120, 60))).write_glb(out / "bad_texture_non_pot.glb")


@register("bad_missing_texture", "prop", "bad", "A .gltf referencing textures/missing_albedo.png, which does not exist.")
def _(out):
    b = GltfBuilder()
    mat = b.material("crate_mat", base=b.image_ref("textures/missing_albedo.png", "missing_albedo"))
    b.node("prop_crate", mesh=b.mesh("prop_crate", box((1, 1, 1)), mat))
    return b.write_gltf(out / "bad_missing_texture.gltf")


@register("bad_absolute_texture_path", "prop", "bad", "A .gltf whose texture path is an absolute Windows path.")
def _(out):
    b = GltfBuilder()
    mat = b.material("crate_mat", base=b.image_ref("C:/Users/builder/Desktop/crate_albedo.png", "crate_albedo"))
    b.node("prop_crate", mesh=b.mesh("prop_crate", box((1, 1, 1)), mat))
    return b.write_gltf(out / "bad_absolute_texture_path.gltf")


@register("bad_normal_as_color", "prop", "bad", "A flat-blue normal map plugged into the base colour slot.")
def _(out):
    return _crate(albedo=NORMAL, normal=None).write_glb(out / "bad_normal_as_color.glb")


@register("bad_grey_normal", "prop", "bad", "A greyscale (one channel) PNG used as the normal map.")
def _(out):
    return _crate(normal=png_bytes(256, 256, (128, 0, 0), color_type=0)).write_glb(out / "bad_grey_normal.glb")


@register("bad_duplicate_names", "prop", "bad", "Two nodes named prop_crate and two materials named crate_mat.")
def _(out):
    b = GltfBuilder()
    m1, m2 = b.material("crate_mat"), b.material("crate_mat")
    b.node("prop_crate", mesh=b.mesh("a", box((1, 1, 1)), m1))
    b.node("prop_crate", mesh=b.mesh("b", box((1, 1, 1)), m2))
    return b.write_glb(out / "bad_duplicate_names.glb")


@register("bad_names_spaces", "prop", "bad", "A node called 'My Crate.001'.")
def _(out):
    return _crate("My Crate.001").write_glb(out / "bad_names_spaces.glb")


@register("bad_spin_bone_name", "tool", "bad", "A drill whose spin bone is named Spin_Bone instead of spin.")
def _(out):
    return _drill(spin_name="Spin_Bone").write_glb(out / "bad_spin_bone_name.glb")


@register("bad_spin_missing", "tool", "bad", "A drill (name matches the spin hint) with a rig but no spin bone at all.")
def _(out):
    return _drill(with_spin=False, clip=None).write_glb(out / "bad_spin_missing.glb")


@register("bad_spin_wrong_parent", "tool", "bad", "root > bone_1 > spin: the spin bone is not a direct child of the root.")
def _(out):
    return _drill(extra_bones=1, spin_parent="arm").write_glb(out / "bad_spin_wrong_parent.glb")


@register("bad_spin_blended", "tool", "bad", "The spin bone shares its vertices with the root bone (weights 0.5 / 0.5).")
def _(out):
    b = _drill(weights_tip=0.5)
    return b.write_glb(out / "bad_spin_blended.glb")


@register("bad_rig_two_roots", "tool", "bad", "A skeleton with two root bones (root and root_2).")
def _(out):
    return _drill(two_roots=True).write_glb(out / "bad_rig_two_roots.glb")


@register("bad_rig_too_many_bones", "tool", "bad", "A tool with 11 bones (limit for the tool profile is 8 by default).")
def _(out):
    return _drill(extra_bones=8, spin_parent="root").write_glb(out / "bad_rig_too_many_bones.glb")


@register("bad_anim_names_fps", "tool", "bad", "A drill whose clip is called Action and runs at 12 fps.")
def _(out):
    return _drill(clip="Action", fps=12.0).write_glb(out / "bad_anim_names_fps.glb")


@register("bad_too_many_tris", "prop", "bad", "A box with 19200 triangles and no collision proxy.")
def _(out):
    return _crate(geo=box((1, 1, 1), sub=40)).write_glb(out / "bad_too_many_tris.glb")


@register("bad_tris_10800", "prop", "bad", "A crate with 10800 triangles: over the global prop budget (10000) but inside the synthetic-mining-tycoon project's 12000.")
def _(out):
    return _crate(geo=box((1, 1, 1), sub=30)).write_glb(out / "bad_tris_10800.glb")


@register("bad_no_uvs", "prop", "bad", "A crate with a textured material but no UV coordinates.")
def _(out):
    return _crate(geo=box((1, 1, 1), uv="none")).write_glb(out / "bad_no_uvs.glb")


def _uv_variant(fn):
    g = box((1, 1, 1))
    uv = [list(u) for u in g["uvs"]]
    fn(uv, g)
    g["uvs"] = [tuple(u) for u in uv]
    return g


@register("bad_uv_out_of_range", "prop", "bad", "Crate UVs scaled to 0-2 (tiling by accident).")
def _(out):
    g = _uv_variant(lambda uv, g: [u.__setitem__(slice(0, 2), [u[0] * 2, u[1] * 2]) for u in uv])
    return _crate(geo=g).write_glb(out / "bad_uv_out_of_range.glb")


@register("bad_uv_overlap", "prop", "bad", "All six crate faces mapped onto the same UV cell.")
def _(out):
    def f(uv, g):
        for i in range(len(uv)):
            face = i // 4
            cell = (face % 3, face // 3)
            uv[i] = [uv[i][0] * 3 - cell[0], uv[i][1] * 2 - cell[1]]
            uv[i] = [uv[i][0] / 3, uv[i][1] / 2]
    return _crate(geo=_uv_variant(f)).write_glb(out / "bad_uv_overlap.glb")


@register("bad_uv_density", "prop", "bad", "One face gets a huge UV cell and the others tiny ones: very uneven texel density.")
def _(out):
    def f(uv, g):
        for i in range(len(uv)):
            face = i // 4
            local = [uv[i][0] * 3 - (face % 3), uv[i][1] * 2 - (face // 2 if False else face // 3)]
            if face == 0:
                uv[i] = [0.02 + local[0] * 0.5, 0.02 + local[1] * 0.5]
            else:
                col = (face - 1) % 5
                uv[i] = [0.55 + col * 0.08 + local[0] * 0.06, 0.02 + local[1] * 0.06]
    return _crate(geo=_uv_variant(f)).write_glb(out / "bad_uv_density.glb")


@register("bad_uv_zero_area", "prop", "bad", "One crate face collapsed to a single point in UV space.")
def _(out):
    def f(uv, g):
        for i in range(0, 4):
            uv[i] = [0.1, 0.1]
    return _crate(geo=_uv_variant(f)).write_glb(out / "bad_uv_zero_area.glb")


@register("bad_open_mesh", "prop", "bad", "A crate with the top face deleted (open boundary edges).")
def _(out):
    g = box((1, 1, 1))
    # face order: +X, -X, +Y(top), -Y, +Z, -Z -> faces have 2 triangles each (6 indices); drop the +Y face
    idx = g["indices"][:12] + g["indices"][18:]
    g["indices"] = idx
    return _crate(geo=g).write_glb(out / "bad_open_mesh.glb")


@register("bad_loose_geometry", "prop", "bad", "A crate with three unused vertices and one zero-area triangle.")
def _(out):
    g = box((1, 1, 1))
    n = len(g["positions"])
    g["positions"] += [(5, 0, 0), (5, 1, 0), (5, 2, 0)]
    g["normals"] += [(0, 1, 0)] * 3
    g["uvs"] += [(0.5, 0.5)] * 3
    g["positions"] += [(0.1, 0.1, 0.1)] * 3
    g["normals"] += [(0, 1, 0)] * 3
    g["uvs"] += [(0.1, 0.1)] * 3
    g["indices"] += [n + 3, n + 4, n + 5]
    return _crate(geo=g).write_glb(out / "bad_loose_geometry.glb")


@register("bad_units_cm", "prop", "bad", "A crate exported 100 times too big (centimetres read as metres).")
def _(out):
    return _crate(geo=box((100, 100, 100))).write_glb(out / "bad_units_cm.glb")


@register("bad_units_tiny", "prop", "bad", "A crate exported 1000 times too small.")
def _(out):
    return _crate(geo=box((0.001, 0.001, 0.001))).write_glb(out / "bad_units_tiny.glb")


@register("bad_origin_center", "prop", "bad", "A crate whose origin is at its centre instead of the base.")
def _(out):
    return _crate(geo=box((1, 1, 1), origin="center")).write_glb(out / "bad_origin_center.glb")


@register("bad_root_offset", "prop", "bad", "A crate exported 50 units away from the world origin.")
def _(out):
    return _crate(node_kw={"t": (50, 0, 0)}).write_glb(out / "bad_root_offset.glb")


@register("bad_axis_rotated_root", "prop", "bad", "A crate under a root with a leftover -90 degree rotation about X (Z-up conversion).")
def _(out):
    b = GltfBuilder()
    mat = b.material("crate_mat", base=b.image(ALBEDO, "a"))
    m = b.mesh("prop_crate", box((1, 1, 1)), mat)
    a = math.radians(-90)
    b.node("prop_crate", mesh=m, r=(math.sin(a / 2), 0, 0, math.cos(a / 2)))
    return b.write_glb(out / "bad_axis_rotated_root.glb")


@register("bad_proxy_heavy", "prop", "bad", "A crate with a collision proxy of 1200 triangles.")
def _(out):
    b = _crate()
    b.node("prop_crate_col", mesh=b.mesh("prop_crate_col", box((1, 1, 1), sub=10, uv="none")))
    return b.write_glb(out / "bad_proxy_heavy.glb")


@register("bad_proxy_bounds", "prop", "bad", "A crate with a collision proxy half its size.")
def _(out):
    b = _crate()
    b.node("prop_crate_col", mesh=b.mesh("prop_crate_col", box((0.5, 0.5, 0.5), uv="none")))
    return b.write_glb(out / "bad_proxy_bounds.glb")


@register("bad_many_materials", "prop", "bad", "Six small parts with six different materials.")
def _(out):
    b = GltfBuilder()
    for i, c in enumerate("abcdef"):
        mat = b.material(f"mat_{c}")
        b.node(f"prop_part_{c}", mesh=b.mesh(f"p{c}", box((0.2, 0.2, 0.2)), mat), t=(i * 0.15, 0, 0))
    return b.write_glb(out / "bad_many_materials.glb")


@register("bad_unused_material", "prop", "bad", "A crate plus a material no mesh uses.")
def _(out):
    b = _crate()
    b.material("leftover_mat")
    return b.write_glb(out / "bad_unused_material.glb")


@register("bad_no_geometry", "prop", "bad", "A file with one empty node and no meshes.")
def _(out):
    b = GltfBuilder()
    b.node("prop_nothing")
    return b.write_glb(out / "bad_no_geometry.glb")


@register("bad_unsupported_features", "prop", "bad", "A crate plus a camera node and a punctual light.")
def _(out):
    b = _crate()
    b.doc["cameras"] = [{"type": "perspective", "perspective": {"yfov": 0.8, "znear": 0.1}}]
    b.doc["extensionsUsed"] = ["KHR_lights_punctual"]
    b.doc["extensions"] = {"KHR_lights_punctual": {"lights": [{"type": "point", "intensity": 10}]}}
    n = b.node("camera_main")
    b.doc["nodes"][n]["camera"] = 0
    n2 = b.node("light_main")
    b.doc["nodes"][n2]["extensions"] = {"KHR_lights_punctual": {"light": 0}}
    return b.write_glb(out / "bad_unsupported_features.glb")


@register("bad_draco_compressed", "prop", "bad", "A GLB that requires KHR_draco_mesh_compression: the reader must refuse it with a clear message.")
def _(out):
    b = _crate()
    b.doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]
    b.doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    return b.write_glb(out / "bad_draco_compressed.glb")


@register("bad_truncated", "prop", "bad", "A GLB cut off in the middle of its binary chunk.")
def _(out):
    p = _crate().write_glb(out / "bad_truncated.glb")
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 2])
    return p


@register("bad_ngon_obj", "prop", "bad", "An OBJ with one five-sided face.")
def _(out):
    g = box((1, 1, 1), uv="none")
    g["normals"] = None
    return synth.write_obj(out / "bad_ngon_obj.obj", {"prop_crate": g}, ngon_quad_as_one=True)


@register("bad_obj_missing_texture", "prop", "bad", "An OBJ whose MTL points at a texture that is not there.")
def _(out):
    g = box((1, 1, 1))
    g["material"] = "crate_mat"
    mtl = "newmtl crate_mat\nmap_Kd textures/not_here.png\n"
    return synth.write_obj(out / "bad_obj_missing_texture.obj", {"prop_crate": g}, mtl="bad_obj_missing_texture.mtl", mtl_text=mtl)


def build_all(out: Path) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return {name: entry["fn"](out) for name, entry in CATALOG.items()}
