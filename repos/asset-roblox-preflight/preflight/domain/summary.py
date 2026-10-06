"""Hub-provided summary input (and the honest FBX story).

FBX is a closed, versioned binary format. This repository does NOT parse FBX geometry. What it does:

* read the FBX header (binary magic + version) and the file size - verified, tiny;
* accept a JSON summary that the hub produced from Blender (``blender_get_objects_summary`` / ``asset_validate``) and that
  has been mapped into the schema below. The real hub tools' output shapes were not available while writing this, so the
  schema is OURS: map the hub output into it (a few lines), nothing is auto-detected.

Schema ``preflight-summary/1`` (JSON):

    {"format": "preflight-summary/1", "source": "blender_get_objects_summary", "file": "crate.fbx",
     "up_axis": "Y", "unit_scale": 1.0,                        # optional
     "objects": [{"name": "prop_crate", "type": "MESH|EMPTY|ARMATURE|BONE",
                  "parent": null, "location": [0,0,0], "rotation_euler_deg": [0,0,0], "scale": [1,1,1],
                  "dimensions": [1,1,1],                       # world bounding-box size, Blender units
                  "triangles": 12, "vertices": 24, "ngons": 0, "quads": 6, "loose_vertices": 0, "degenerate_faces": 0,
                  "non_manifold_edges": 0, "flipped_normal_faces": 0, "uv_layers": 1, "uv_out_of_range_fraction": 0.0,
                  "uv_overlap_ratio": 0.0, "uv_coverage": 0.9, "texel_density_spread": 1.2,
                  "origin_offset": [0,0,0],                    # origin minus bbox bottom-centre, if known
                  "materials": ["wood"], "is_collision": false}],
     "materials": [{"name": "wood", "textures": [{"slot": "base_color|normal|metallic_roughness|occlusion|emissive",
                                                 "path": "textures/wood_albedo.png", "colorspace": "sRGB|Non-Color"}]}],
     "armatures": [{"name": "rig", "bones": [{"name": "root", "parent": null}, {"name": "spin", "parent": "root"}]}],
     "animations": [{"name": "spin", "frame_rate": 30, "frame_start": 1, "frame_end": 30}]}

Every number in it is *reported*, not measured here; findings built from it say so ("reported by summary"). Texture files
are still looked up on disk.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from .gltf import MAX_BYTES, Resolver, _is_unsafe_uri
from .imageinfo import image_info
from .mathx import IDENTITY

SUMMARY_FORMAT = "preflight-summary/1"
FBX_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
REPORTED_KEYS = ("triangles", "vertices", "ngons", "quads", "loose_vertices", "degenerate_faces", "non_manifold_edges", "flipped_normal_faces", "uv_layers",
                 "uv_out_of_range_fraction", "uv_overlap_ratio", "uv_coverage", "texel_density_spread", "origin_offset", "dimensions")
KINDS = {"MESH": "mesh", "EMPTY": "empty", "ARMATURE": "armature", "BONE": "bone"}


def fbx_header(path: Path) -> dict:
    """What can honestly be verified about an FBX without parsing it."""
    with path.open("rb") as fh:
        head = fh.read(64)
    if head.startswith(FBX_MAGIC):
        version = struct.unpack("<I", head[23:27])[0]
        return {"flavour": "binary", "version": version}
    if head.lstrip().startswith(b"; FBX"):
        return {"flavour": "ascii", "version": None}
    raise ValueError(f"{path.name} does not look like an FBX file (no FBX header)")


def load_fbx(path: Path) -> dict:
    hdr = fbx_header(path)
    return {"format": "fbx", "path": str(path), "file_size": path.stat().st_size, "reader": "fbx-header", "generator": None, "extensions_used": [], "extensions_required": [],
            "extras": {}, "warnings": [], "objects": [], "materials": [], "images": [], "skins": [], "animations": [], "up_axis": None, "unit": None,
            "fbx": hdr, "geometry_measured": False,
            "not_measurable": [{"what": "everything inside the FBX", "why": "this repository reads only the FBX header (flavour, version) and file size; "
                                "geometry, UVs, materials, rig are NOT parsed. Pass a hub summary (summary / summary_path) to check them."}]}


def validate_summary(doc: dict) -> list[str]:
    problems = []
    if doc.get("format") != SUMMARY_FORMAT:
        problems.append(f"'format' must be '{SUMMARY_FORMAT}'")
    objs = doc.get("objects")
    if not isinstance(objs, list):
        problems.append("'objects' must be a list")
        return problems
    names = []
    for i, o in enumerate(objs):
        if not isinstance(o, dict) or not isinstance(o.get("name"), str) or not o["name"]:
            problems.append(f"objects[{i}] needs a non-empty string 'name'")
            continue
        names.append(o["name"])
        if o.get("type", "MESH").upper() not in KINDS:
            problems.append(f"objects[{i}] type must be one of {sorted(KINDS)}")
        for key in ("location", "rotation_euler_deg", "scale", "dimensions", "origin_offset"):
            v = o.get(key)
            if v is not None and not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
                problems.append(f"objects[{i}].{key} must be three numbers")
        for key in REPORTED_KEYS:
            v = o.get(key)
            if v is not None and key not in ("origin_offset", "dimensions") and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                problems.append(f"objects[{i}].{key} must be a non-negative number")
    for i, m in enumerate(doc.get("materials", [])):
        if not isinstance(m, dict) or not m.get("name"):
            problems.append(f"materials[{i}] needs a 'name'")
            continue
        for t in m.get("textures", []):
            if not isinstance(t, dict) or not t.get("path") or t.get("slot") not in ("base_color", "normal", "metallic_roughness", "occlusion", "emissive"):
                problems.append(f"materials[{i}].textures entries need 'path' and a valid 'slot'")
    return problems


def merge_summary(asset: dict, doc: dict, resolve: Resolver) -> dict:
    """Fill an asset (typically the FBX header record) with the reported objects, materials and textures of a summary."""
    problems = validate_summary(doc)
    if problems:
        raise ValueError("summary is invalid: " + "; ".join(problems[:6]))
    asset = dict(asset)
    asset["reader"] = f"{asset.get('reader', 'none')}+summary"
    asset["summary_source"] = doc.get("source", "unspecified")
    asset["up_axis"] = doc.get("up_axis")
    asset["unit"] = f"unit_scale {doc['unit_scale']}" if "unit_scale" in doc else None
    asset["geometry_measured"] = False
    asset["not_measurable"] = [{"what": "geometry-derived metrics not in the summary", "why": "values come from the summary as reported by Blender; this repository did not measure the FBX itself"}]
    materials, images = [], []
    mat_index = {}
    for m in doc.get("materials", []):
        rec = {"index": len(materials), "name": m["name"], "alpha_mode": "OPAQUE", "double_sided": False, "textures": {}, "texcoords": {}}
        mat_index[m["name"]] = rec["index"]
        for t in m.get("textures", []):
            ex = next((im for im in images if im["uri"] == t["path"]), None)
            if ex is None:
                unsafe = _is_unsafe_uri(t["path"])
                p = None if unsafe else resolve(t["path"])
                ex = {"index": len(images), "name": Path(t["path"]).name, "uri": t["path"], "embedded": False, "path": str(p) if p else None, "unsafe_uri": unsafe,
                      "exists": bool(p and p.exists()), "mime": None, "slots": [], "byte_size": None, "issues": [], "declared_colorspace": t.get("colorspace")}
                data = None
                if ex["exists"]:
                    ex["byte_size"] = p.stat().st_size
                    data = p.read_bytes()[: 2 * 1024 * 1024]
                info = image_info(data) if data else {}
                ex.update({k: info.get(k) for k in ("format", "width", "height", "channels", "bit_depth", "color_type", "has_alpha", "chunks")})
                ex["_data"] = data
                images.append(ex)
            rec["textures"][t["slot"]] = ex["index"]
            rec["texcoords"][t["slot"]] = 0
            if t.get("colorspace"):
                ex["declared_colorspace"] = t["colorspace"]
            ex["slots"].append({"material": m["name"], "slot": t["slot"]})
        materials.append(rec)
    asset["materials"], asset["images"] = materials, images
    objects = []
    for i, o in enumerate(doc["objects"]):
        kind = KINDS[o.get("type", "MESH").upper()]
        import math

        from .mathx import mat_from_trs

        rot = [math.radians(a) for a in o.get("rotation_euler_deg", [0, 0, 0])]
        cx, cy, cz = (math.cos(a / 2) for a in rot)
        sx, sy, sz = (math.sin(a / 2) for a in rot)
        q = (sx * cy * cz - cx * sy * sz, cx * sy * cz + sx * cy * sz, cx * cy * sz - sx * sy * cz, cx * cy * cz + sx * sy * sz)
        t, s = tuple(o.get("location", [0, 0, 0])), tuple(o.get("scale", [1, 1, 1]))
        obj = {"id": f"s{i}", "node": i, "name": o["name"], "unnamed": False, "parent": o.get("parent"), "kind": kind, "translation": t, "rotation": q, "scale": s,
               "world": mat_from_trs(t, q, s), "extras": {}, "is_joint": kind == "bone", "skin": None, "mesh": None,
               "reported": {k: o[k] for k in REPORTED_KEYS if o.get(k) is not None}, "is_collision_declared": o.get("is_collision"),
               "material_names": list(o.get("materials", []))}
        if kind == "mesh":
            obj["mesh"] = {"name": o["name"], "positions": [], "normals": None, "uvs": None, "triangles": [], "primitives": [], "joints": None, "weights": None,
                           "uv_sets": o.get("uv_layers", 0), "non_triangle_primitives": 0, "vertex_colors": False, "morph_targets": False, "ngons": o.get("ngons"),
                           "quads": o.get("quads"), "reported_only": True,
                           "primitives_reported": [{"material": mat_index.get(mn)} for mn in o.get("materials", []) if mn in mat_index]}
        objects.append(obj)
    skins = []
    for arm in doc.get("armatures", []):
        bones = arm.get("bones", [])
        names = [b["name"] for b in bones]
        parent_of = {b["name"]: (b.get("parent") if b.get("parent") in names else None) for b in bones}
        skins.append({"index": len(skins), "name": arm.get("name"), "joint_nodes": [], "joint_names": names,
                      "root_joints": [n for n in names if parent_of[n] is None], "parent_of": parent_of})
        for b in bones:
            if not any(o["name"] == b["name"] and o["kind"] == "bone" for o in objects):
                objects.append({"id": f"b{len(objects)}", "node": len(objects), "name": b["name"], "unnamed": False, "parent": b.get("parent") or arm.get("name"),
                                "kind": "bone", "translation": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0, 1.0), "scale": (1.0, 1.0, 1.0), "world": list(IDENTITY),
                                "extras": {}, "is_joint": True, "skin": None, "mesh": None})
    asset["objects"], asset["skins"] = objects, skins
    asset["animations"] = [{"index": i, "name": a.get("name"), "duration": ((a.get("frame_end", 0) - a.get("frame_start", 0)) / a["frame_rate"]) if a.get("frame_rate") else None,
                            "frame_rate": a.get("frame_rate"), "frame_rate_note": "reported by the summary", "targets": []} for i, a in enumerate(doc.get("animations", []))]
    return asset


def read_summary_file(path: Path) -> dict:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError(f"{path.name} is too large for a summary")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("a summary must be a JSON object")
    return doc


def summary_only_asset(doc: dict, path: str, resolve: Resolver) -> dict:
    """A summary with no export file next to it (e.g. the user passed only the JSON)."""
    base = {"format": "summary", "path": path, "file_size": None, "reader": "summary", "generator": None, "extensions_used": [], "extensions_required": [],
            "extras": {}, "warnings": [], "objects": [], "materials": [], "images": [], "skins": [], "animations": [], "up_axis": None, "unit": None}
    return merge_summary(base, doc, resolve)
