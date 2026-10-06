"""Wavefront OBJ (+ MTL) reader. Cheap and honest: geometry, UVs, normals, n-gon counts, material/texture references.

OBJ has no transforms, hierarchy, rig, or units: every object is reported with identity transform, no skin, and the
asset's up axis and unit are marked as unknown.
"""

from __future__ import annotations

from pathlib import Path

from .imageinfo import image_info
from .mathx import IDENTITY
from .gltf import MAX_BYTES, Resolver, _is_unsafe_uri

MAP_KEYS = {"map_kd": "base_color", "map_bump": "normal", "bump": "normal", "norm": "normal", "map_kn": "normal", "map_ke": "emissive",
            "map_ks": "metallic_roughness", "map_pm": "metallic_roughness", "map_pr": "metallic_roughness"}


def _idx(tok: str, n: int) -> int | None:
    if tok == "":
        return None
    i = int(tok)
    j = i - 1 if i > 0 else n + i
    if j < 0 or j >= n:
        raise ValueError(f"OBJ face references element {i} but only {n} exist")
    return j


def read_obj(path: Path, resolve: Resolver) -> dict:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path.name} is too large")
    text = path.read_text(encoding="utf-8", errors="replace")
    V: list = []
    VT: list = []
    VN: list = []
    objects: list[dict] = []
    cur: dict | None = None
    mtllibs: list[str] = []
    cur_mat: str | None = None

    def start(name: str) -> dict:
        o = {"name": name, "faces": [], "ngons": 0, "quads": 0, "materials": []}
        objects.append(o)
        return o

    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, rest = line.partition(" ")
        rest = rest.strip()
        try:
            if key == "v":
                V.append(tuple(float(x) for x in rest.split()[:3]))
            elif key == "vt":
                p = rest.split()
                VT.append((float(p[0]), float(p[1]) if len(p) > 1 else 0.0))
            elif key == "vn":
                VN.append(tuple(float(x) for x in rest.split()[:3]))
            elif key in ("o", "g"):
                if key == "o" or cur is None or not cur["faces"]:
                    cur = start(rest or f"object_{len(objects)}")
                elif key == "g" and cur["faces"]:
                    cur = start(rest or f"group_{len(objects)}")
            elif key == "mtllib":
                mtllibs.append(rest)
            elif key == "usemtl":
                cur_mat = rest
            elif key == "f":
                if cur is None:
                    cur = start(path.stem)
                verts = []
                for tok in rest.split():
                    parts = tok.split("/")
                    vi = _idx(parts[0], len(V))
                    ti = _idx(parts[1], len(VT)) if len(parts) > 1 else None
                    ni = _idx(parts[2], len(VN)) if len(parts) > 2 else None
                    verts.append((vi, ti, ni))
                if len(verts) < 3:
                    raise ValueError("a face needs at least 3 vertices")
                if len(verts) > 4:
                    cur["ngons"] += 1
                elif len(verts) == 4:
                    cur["quads"] += 1
                cur["faces"].append((verts, cur_mat))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"{path.name}:{ln}: {exc}") from exc
    if not V:
        raise ValueError(f"{path.name} has no vertices")
    asset: dict = {"format": "obj", "path": str(path), "file_size": size, "reader": "obj", "generator": None, "extensions_used": [], "extensions_required": [],
                   "extras": {}, "warnings": [], "up_axis": None, "unit": None, "skins": [], "animations": [],
                   "not_measurable": [{"what": "units and up axis", "why": "OBJ does not declare them; the file's raw numbers are used"},
                                      {"what": "rig and animation", "why": "OBJ cannot carry them"}]}
    materials: list[dict] = []
    images: list[dict] = []
    mat_index: dict[str, int] = {}
    for lib in mtllibs:
        p = resolve(lib)
        if p is None or not p.exists():
            asset["warnings"].append(f"material library '{lib}' was not found")
            continue
        cur_m = None
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            key, _, rest = line.partition(" ")
            k = key.lower()
            if k == "newmtl":
                cur_m = {"index": len(materials), "name": rest.strip(), "alpha_mode": "OPAQUE", "double_sided": False, "textures": {}, "texcoords": {}}
                mat_index[cur_m["name"]] = len(materials)
                materials.append(cur_m)
            elif cur_m is not None and k in MAP_KEYS:
                fname = rest.split()[-1] if rest.split() else ""
                if not fname:
                    continue
                existing = next((im for im in images if im["uri"] == fname), None)
                if existing is None:
                    safe = _is_unsafe_uri(fname)
                    ip = None if safe else resolve(fname)
                    rec = {"index": len(images), "name": Path(fname).name, "uri": fname, "embedded": False, "path": str(ip) if ip else None, "unsafe_uri": safe,
                           "exists": bool(ip and ip.exists()), "mime": None, "slots": [], "byte_size": None, "issues": []}
                    data = None
                    if rec["exists"]:
                        rec["byte_size"] = ip.stat().st_size
                        data = ip.read_bytes()[: 2 * 1024 * 1024]
                    info = image_info(data) if data else {}
                    rec.update({kk: info.get(kk) for kk in ("format", "width", "height", "channels", "bit_depth", "color_type", "has_alpha", "chunks")})
                    rec["_data"] = data
                    images.append(rec)
                    existing = rec
                slot = MAP_KEYS[k]
                cur_m["textures"][slot] = existing["index"]
                cur_m["texcoords"][slot] = 0
                existing["slots"].append({"material": cur_m["name"], "slot": slot})
    asset["materials"], asset["images"] = materials, images
    out_objects = []
    for o in objects:
        if not o["faces"]:
            continue
        key_to_i: dict = {}
        pos, nor, uv, tris, prims = [], [], [], [], []
        has_n = has_uv = False
        run_mat, run_start = object(), 0
        for verts, mat in o["faces"]:
            ids = []
            for v in verts:
                if v not in key_to_i:
                    key_to_i[v] = len(pos)
                    pos.append(V[v[0]])
                    nor.append(VN[v[2]] if v[2] is not None else None)
                    uv.append(VT[v[1]] if v[1] is not None else None)
                    has_n = has_n or v[2] is not None
                    has_uv = has_uv or v[1] is not None
                ids.append(key_to_i[v])
            for k in range(1, len(ids) - 1):
                if mat != run_mat:
                    if prims:
                        prims[-1]["count"] = len(tris) // 3 - prims[-1]["start"]
                    prims.append({"material": mat_index.get(mat) if mat else None, "start": len(tris) // 3, "count": 0, "mode": 4})
                    run_mat = mat
                tris += [ids[0], ids[k], ids[k + 1]]
        if prims:
            prims[-1]["count"] = len(tris) // 3 - prims[-1]["start"]
        out_objects.append({"id": f"o{len(out_objects)}", "node": len(out_objects), "name": o["name"], "unnamed": False, "parent": None, "kind": "mesh",
                            "translation": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0, 1.0), "scale": (1.0, 1.0, 1.0), "world": list(IDENTITY), "extras": {},
                            "is_joint": False, "skin": None,
                            "mesh": {"name": o["name"], "positions": pos, "normals": nor if has_n else None, "uvs": uv if has_uv else None, "triangles": tris,
                                     "primitives": prims, "joints": None, "weights": None, "uv_sets": 1 if has_uv else 0, "non_triangle_primitives": 0,
                                     "vertex_colors": False, "morph_targets": False, "ngons": o["ngons"], "quads": o["quads"]}})
    asset["objects"] = out_objects
    return asset
