"""A real GLB / glTF 2.0 reader: JSON + binary chunks, accessors, nodes, materials, images, skins and animations.

Pure standard library. It reads what the preflight rules need and fails loudly (ValueError with the cause) on files it cannot
read - compressed geometry (Draco, meshopt) in particular - instead of guessing.
"""

from __future__ import annotations

import base64
import json
import statistics
import struct
import urllib.parse
from pathlib import Path
from typing import Callable

from .imageinfo import image_info
from .mathx import IDENTITY, mat_decompose, mat_from_trs, mat_mul

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
COMP_FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
COMP_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
TYPE_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
NORM_DIV = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
UNSUPPORTED_GEOMETRY_EXT = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression")
MAX_BYTES = 256 * 1024 * 1024

Resolver = Callable[[str], "Path | None"]


def split_glb(data: bytes) -> tuple[dict, bytes | None]:
    if len(data) < 20:
        raise ValueError("not a GLB: file is shorter than the 20-byte header")
    magic, version, total = struct.unpack("<III", data[:12])
    if magic != GLB_MAGIC:
        raise ValueError("not a GLB: missing 'glTF' magic bytes")
    if version != 2:
        raise ValueError(f"unsupported GLB version {version} (only glTF 2.0 / GLB version 2)")
    if total > len(data):
        raise ValueError(f"truncated GLB: header says {total} bytes, file has {len(data)}")
    pos, js, binary = 12, None, None
    while pos + 8 <= total:
        clen, ctype = struct.unpack("<II", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + clen]
        if len(body) != clen:
            raise ValueError("truncated GLB chunk")
        if ctype == CHUNK_JSON and js is None:
            js = body
        elif ctype == CHUNK_BIN and binary is None:
            binary = body
        pos += 8 + clen + ((4 - clen % 4) % 4)
    if js is None:
        raise ValueError("GLB has no JSON chunk")
    try:
        doc = json.loads(js.decode("utf-8").rstrip("\x00 "))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"GLB JSON chunk is not valid JSON: {exc}") from exc
    return doc, binary


class _Doc:
    def __init__(self, doc: dict, glb_bin: bytes | None, resolve: Resolver):
        self.doc = doc
        self.glb_bin = glb_bin
        self.resolve = resolve
        self._buffers: dict[int, bytes] = {}
        self.warnings: list[str] = []

    def buffer(self, i: int) -> bytes:
        if i in self._buffers:
            return self._buffers[i]
        bufs = self.doc.get("buffers", [])
        if i >= len(bufs):
            raise ValueError(f"buffer {i} does not exist")
        b = bufs[i]
        uri = b.get("uri")
        if uri is None:
            if i != 0 or self.glb_bin is None:
                raise ValueError(f"buffer {i} has no uri and there is no GLB binary chunk")
            data = self.glb_bin
        else:
            data = self.uri_bytes(uri, f"buffer {i}")
        if len(data) < b.get("byteLength", 0):
            raise ValueError(f"buffer {i} is shorter ({len(data)}) than its byteLength ({b['byteLength']})")
        self._buffers[i] = data
        return data

    def uri_bytes(self, uri: str, what: str) -> bytes:
        if uri.startswith("data:"):
            try:
                head, payload = uri.split(",", 1)
                return base64.b64decode(payload) if ";base64" in head else urllib.parse.unquote_to_bytes(payload)
            except Exception as exc:
                raise ValueError(f"{what}: invalid data URI ({exc})") from exc
        path = self.resolve(uri)
        if path is None or not path.exists():
            raise ValueError(f"{what}: referenced file '{uri}' was not found next to the asset or in the texture folder")
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f"{what}: '{uri}' is larger than {MAX_BYTES // (1024 * 1024)} MB")
        return path.read_bytes()

    def view(self, i: int) -> tuple[bytes, int, int, int | None]:
        views = self.doc.get("bufferViews", [])
        if i >= len(views):
            raise ValueError(f"bufferView {i} does not exist")
        v = views[i]
        buf = self.buffer(v["buffer"])
        off, ln = v.get("byteOffset", 0), v["byteLength"]
        if off + ln > len(buf):
            raise ValueError(f"bufferView {i} runs past the end of its buffer")
        return buf, off, ln, v.get("byteStride")

    def accessor(self, i: int) -> list:
        accs = self.doc.get("accessors", [])
        if i >= len(accs):
            raise ValueError(f"accessor {i} does not exist")
        a = accs[i]
        ct, n, tn = a["componentType"], a["count"], TYPE_N[a["type"]]
        if ct not in COMP_FMT:
            raise ValueError(f"accessor {i}: unknown componentType {ct}")
        csize = COMP_SIZE[ct]
        fmt = "<" + COMP_FMT[ct] * tn
        elem = csize * tn
        values: list
        if "bufferView" in a:
            buf, off, ln, stride = self.view(a["bufferView"])
            base = off + a.get("byteOffset", 0)
            stride = stride or elem
            if n and base + (n - 1) * stride + elem > off + ln:
                raise ValueError(f"accessor {i} reads past the end of its bufferView")
            if stride == elem and tn == 1:
                values = list(struct.unpack_from("<" + COMP_FMT[ct] * n, buf, base)) if n else []
            else:
                values = [struct.unpack_from(fmt, buf, base + k * stride) for k in range(n)]
                if tn == 1:
                    values = [v[0] for v in values]
        else:
            values = [0] * n if tn == 1 else [tuple([0] * tn)] * n
        sp = a.get("sparse")
        if sp:
            cnt = sp["count"]
            ib, ioff, iln, _ = self.view(sp["indices"]["bufferView"])
            ict = sp["indices"]["componentType"]
            idx = struct.unpack_from("<" + COMP_FMT[ict] * cnt, ib, ioff + sp["indices"].get("byteOffset", 0))
            vb, voff, vln, _ = self.view(sp["values"]["bufferView"])
            vbase = voff + sp["values"].get("byteOffset", 0)
            values = list(values)
            for k, ix in enumerate(idx):
                v = struct.unpack_from(fmt, vb, vbase + k * elem)
                values[ix] = v[0] if tn == 1 else v
        if a.get("normalized") and ct in NORM_DIV:
            d = NORM_DIV[ct]
            conv = (lambda x: max(x / d, -1.0)) if ct in (5120, 5122) else (lambda x: x / d)
            values = [conv(v) for v in values] if tn == 1 else [tuple(conv(c) for c in v) for v in values]
        return values

    def accessor_meta(self, i: int) -> dict:
        return self.doc.get("accessors", [])[i]


def _node_matrix(n: dict) -> list[float]:
    if "matrix" in n:
        if len(n["matrix"]) != 16:
            raise ValueError("node matrix must have 16 numbers")
        return [float(x) for x in n["matrix"]]
    return mat_from_trs(n.get("translation", (0, 0, 0)), n.get("rotation", (0, 0, 0, 1)), n.get("scale", (1, 1, 1)))


def _tri_indices(mode: int, idx: list[int]) -> tuple[list[int], bool]:
    if mode == 4:
        return idx[: len(idx) - len(idx) % 3], True
    if mode == 5:
        out = []
        for k in range(len(idx) - 2):
            out += [idx[k], idx[k + 1], idx[k + 2]] if k % 2 == 0 else [idx[k + 1], idx[k], idx[k + 2]]
        return out, True
    if mode == 6:
        out = []
        for k in range(1, len(idx) - 1):
            out += [idx[0], idx[k], idx[k + 1]]
        return out, True
    return [], False


def read_gltf_document(doc: dict, glb_bin: bytes | None, resolve: Resolver, *, fmt: str, path: str, file_size: int) -> dict:
    """Turn a parsed glTF JSON document into the normalised asset dict used by the checks."""
    d = _Doc(doc, glb_bin, resolve)
    asset_info = doc.get("asset", {})
    if str(asset_info.get("version", "")).split(".")[0] != "2":
        raise ValueError(f"unsupported glTF version '{asset_info.get('version')}' (need 2.x)")
    used = list(doc.get("extensionsUsed", []))
    required = list(doc.get("extensionsRequired", []))
    for ext in UNSUPPORTED_GEOMETRY_EXT:
        if ext in required or any(ext in (p.get("extensions") or {}) for m in doc.get("meshes", []) for p in m.get("primitives", [])):
            raise ValueError(f"geometry is compressed with {ext}; this reader cannot decode it. Re-export without mesh compression for preflight (and for Roblox).")
    asset: dict = {"format": fmt, "path": path, "file_size": file_size, "reader": "gltf", "generator": asset_info.get("generator"),
                   "extensions_used": used, "extensions_required": required, "extras": doc.get("extras") or {}, "warnings": d.warnings,
                   "not_measurable": [], "up_axis": "Y", "unit": "meter (glTF 2.0 defines metres, +Y up, -Z forward)"}
    nodes_json = doc.get("nodes", [])
    names = []
    for i, n in enumerate(nodes_json):
        names.append(n.get("name") if isinstance(n.get("name"), str) and n.get("name") != "" else None)
    parent = {}
    for i, n in enumerate(nodes_json):
        for c in n.get("children", []):
            if c >= len(nodes_json):
                raise ValueError(f"node {i} lists child {c} which does not exist")
            parent[c] = i
    scenes = doc.get("scenes", [])
    roots = scenes[doc.get("scene", 0)].get("nodes", []) if scenes else [i for i in range(len(nodes_json)) if i not in parent]
    reach: list[int] = []
    seen = set()
    stack = list(reversed(roots))
    while stack:
        i = stack.pop()
        if i in seen:
            raise ValueError(f"node {i} is reachable twice: the scene graph has a cycle or shared node")
        seen.add(i)
        reach.append(i)
        stack += list(reversed(nodes_json[i].get("children", [])))
    world: dict[int, list[float]] = {}
    for i in reach:
        local = _node_matrix(nodes_json[i])
        world[i] = mat_mul(world[parent[i]], local) if i in parent and parent[i] in world else local
    skins_json = doc.get("skins", [])
    joint_nodes: dict[int, int] = {}
    for si, s in enumerate(skins_json):
        for j in s.get("joints", []):
            joint_nodes[j] = si
    # materials / images
    images: list[dict] = []
    for ii, im in enumerate(doc.get("images", [])):
        rec = {"index": ii, "name": im.get("name"), "uri": im.get("uri"), "embedded": False, "exists": True, "path": None,
               "mime": im.get("mimeType"), "slots": [], "byte_size": None, "issues": []}
        data = None
        if im.get("uri") is not None and not im["uri"].startswith("data:"):
            p = resolve(im["uri"]) if not _is_unsafe_uri(im["uri"]) else None
            rec["unsafe_uri"] = _is_unsafe_uri(im["uri"])
            rec["path"] = str(p) if p else None
            rec["exists"] = bool(p and p.exists())
            if rec["exists"]:
                rec["byte_size"] = p.stat().st_size
                with p.open("rb") as fh:
                    data = fh.read(2 * 1024 * 1024)
        elif im.get("uri") is not None:
            data = d.uri_bytes(im["uri"], f"image {ii}")
            rec["embedded"], rec["byte_size"] = True, len(data)
        elif "bufferView" in im:
            buf, off, ln, _ = d.view(im["bufferView"])
            data = buf[off:off + ln]
            rec["embedded"], rec["byte_size"] = True, ln
        else:
            raise ValueError(f"image {ii} has neither uri nor bufferView")
        info = image_info(data) if data else {"format": None}
        rec.update({k: info.get(k) for k in ("format", "width", "height", "channels", "bit_depth", "color_type", "has_alpha", "chunks")})
        rec["_data"] = data
        if rec["mime"] and info.get("format") and not (rec["mime"].endswith(info["format"]) or (info["format"] == "jpeg" and rec["mime"].endswith("jpg"))):
            rec["issues"].append(f"mimeType '{rec['mime']}' does not match the file content ({info['format']})")
        images.append(rec)
    textures = doc.get("textures", [])
    ext_src = {"EXT_texture_webp": "webp", "KHR_texture_basisu": "ktx2"}

    def tex_image(tinfo: dict | None) -> int | None:
        if not tinfo or "index" not in tinfo or tinfo["index"] >= len(textures):
            return None
        t = textures[tinfo["index"]]
        if "source" in t:
            return t["source"]
        for ext in ext_src:
            if ext in (t.get("extensions") or {}):
                return t["extensions"][ext].get("source")
        return None

    materials: list[dict] = []
    for mi, m in enumerate(doc.get("materials", [])):
        pbr = m.get("pbrMetallicRoughness", {})
        slots = {"base_color": pbr.get("baseColorTexture"), "metallic_roughness": pbr.get("metallicRoughnessTexture"), "normal": m.get("normalTexture"),
                 "occlusion": m.get("occlusionTexture"), "emissive": m.get("emissiveTexture")}
        rec = {"index": mi, "name": m.get("name"), "alpha_mode": m.get("alphaMode", "OPAQUE"), "double_sided": bool(m.get("doubleSided")),
               "textures": {}, "texcoords": {}}
        for slot, t in slots.items():
            img = tex_image(t)
            if img is not None and img < len(images):
                rec["textures"][slot] = img
                rec["texcoords"][slot] = (t or {}).get("texCoord", 0)
                images[img]["slots"].append({"material": m.get("name") or f"material_{mi}", "slot": slot})
        materials.append(rec)
    asset["images"], asset["materials"] = images, materials
    # objects
    objects: list[dict] = []
    obj_of_node: dict[int, dict] = {}
    for i in reach:
        n = nodes_json[i]
        t, q, s = mat_decompose(_node_matrix(n))
        obj = {"id": f"n{i}", "node": i, "name": names[i] or f"node_{i}", "unnamed": names[i] is None, "parent": None, "kind": "empty",
               "translation": t, "rotation": q, "scale": s, "world": world[i], "extras": n.get("extras") or {}, "mesh": None,
               "is_joint": i in joint_nodes, "has_camera": "camera" in n, "has_light": "KHR_lights_punctual" in (n.get("extensions") or {})}
        objects.append(obj)
        obj_of_node[i] = obj
    for i in reach:
        if i in parent and parent[i] in obj_of_node:
            obj_of_node[i]["parent"] = obj_of_node[parent[i]]["name"]
            obj_of_node[i]["parent_id"] = obj_of_node[parent[i]]["id"]
    meshes_json = doc.get("meshes", [])
    for i in reach:
        n = nodes_json[i]
        obj = obj_of_node[i]
        if obj["is_joint"]:
            obj["kind"] = "bone"
        if "mesh" in n:
            if n["mesh"] >= len(meshes_json):
                raise ValueError(f"node {i} uses mesh {n['mesh']} which does not exist")
            obj["kind"] = "mesh"
            obj["skin"] = n.get("skin")
            _read_mesh(d, meshes_json[n["mesh"]], obj)
    # armature: an empty parent of a root joint
    for s in skins_json:
        for j in s.get("joints", []):
            if j in obj_of_node:
                pid = parent.get(j)
                if pid is not None and pid not in joint_nodes and pid in obj_of_node and obj_of_node[pid]["kind"] == "empty":
                    obj_of_node[pid]["kind"] = "armature"
    asset["objects"] = objects
    skins = []
    for si, s in enumerate(skins_json):
        joints = [j for j in s.get("joints", []) if j in obj_of_node]
        jset = set(joints)
        roots_j = [j for j in joints if parent.get(j) not in jset]
        skins.append({"index": si, "name": s.get("name"), "joint_nodes": joints, "joint_names": [obj_of_node[j]["name"] for j in joints],
                      "root_joints": [obj_of_node[j]["name"] for j in roots_j],
                      "parent_of": {obj_of_node[j]["name"]: (obj_of_node[parent[j]]["name"] if parent.get(j) in jset else None) for j in joints}})
    asset["skins"] = skins
    anims = []
    for ai, a in enumerate(doc.get("animations", [])):
        durations, deltas, targets = [], [], set()
        for smp in a.get("samplers", []):
            acc = d.accessor_meta(smp["input"])
            if acc.get("min") and acc.get("max"):
                durations.append(acc["max"][0] - acc["min"][0])
            times = d.accessor(smp["input"])
            deltas += [b - c for b, c in zip(times[1:], times[:-1]) if b - c > 1e-9]
        for ch in a.get("channels", []):
            t = ch.get("target", {})
            if "node" in t and t["node"] in obj_of_node:
                targets.add(obj_of_node[t["node"]]["name"])
        fps = round(1.0 / statistics.median(deltas), 3) if deltas else None
        anims.append({"index": ai, "name": a.get("name"), "duration": round(max(durations), 6) if durations else None, "frame_rate": fps,
                      "frame_rate_note": "estimated from keyframe spacing (glTF stores times in seconds, not a frame rate)", "targets": sorted(targets)})
    asset["animations"] = anims
    asset["not_measurable"] += [{"what": "n-gons", "why": "glTF stores only triangles; quads and n-gons are lost at export (use the .blend summary to check them)"}]
    return asset


def _is_unsafe_uri(uri: str) -> bool:
    u = urllib.parse.unquote(uri)
    return u.startswith(("/", "\\")) or ":" in u.split("/")[0] or ".." in Path(u.replace("\\", "/")).parts


def _read_mesh(d: _Doc, mesh: dict, obj: dict) -> None:
    pos: list = []
    nor: list = []
    uv: list = []
    joints: list = []
    weights: list = []
    tris: list[int] = []
    prims = []
    has_n = has_uv = has_j = False
    uv_sets = 0
    nontri = 0
    colors = False
    morphs = bool(mesh.get("primitives") and any(p.get("targets") for p in mesh["primitives"]))
    for p in mesh.get("primitives", []):
        at = p.get("attributes", {})
        if "POSITION" not in at:
            continue
        base = len(pos)
        pp = d.accessor(at["POSITION"])
        pos += [tuple(float(c) for c in v) for v in pp]
        n = len(pp)
        if "NORMAL" in at:
            nn = d.accessor(at["NORMAL"])
            nor += [tuple(float(c) for c in v) for v in nn]
            has_n = True
        else:
            nor += [None] * n
        if "TEXCOORD_0" in at:
            uu = d.accessor(at["TEXCOORD_0"])
            uv += [(float(v[0]), float(v[1])) for v in uu]
            has_uv = True
        else:
            uv += [None] * n
        uv_sets = max(uv_sets, sum(1 for k in at if k.startswith("TEXCOORD_")))
        colors = colors or any(k.startswith("COLOR_") for k in at)
        if "JOINTS_0" in at and "WEIGHTS_0" in at:
            joints += [tuple(int(c) for c in v) for v in d.accessor(at["JOINTS_0"])]
            weights += [tuple(float(c) for c in v) for v in d.accessor(at["WEIGHTS_0"])]
            has_j = True
        else:
            joints += [None] * n
            weights += [None] * n
        idx = d.accessor(p["indices"]) if "indices" in p else list(range(n))
        if any(i >= n or i < 0 for i in idx):
            raise ValueError(f"mesh '{mesh.get('name')}' has an index outside its {n} vertices")
        t, ok = _tri_indices(p.get("mode", 4), list(idx))
        if not ok:
            nontri += 1
        start = len(tris) // 3
        tris += [base + i for i in t]
        prims.append({"material": p.get("material"), "start": start, "count": len(t) // 3, "mode": p.get("mode", 4)})
    obj["mesh"] = {"name": mesh.get("name"), "positions": pos, "normals": nor if has_n else None, "uvs": uv if has_uv else None, "triangles": tris,
                   "primitives": prims, "joints": joints if has_j else None, "weights": weights if has_j else None, "uv_sets": uv_sets,
                   "non_triangle_primitives": nontri, "vertex_colors": colors, "morph_targets": morphs, "ngons": None, "quads": None}


def read_glb_or_gltf(path: Path, resolve: Resolver) -> dict:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_BYTES // (1024 * 1024)} MB; preflight does not load files that large")
    data = path.read_bytes()
    if data[:4] == b"glTF":
        doc, binary = split_glb(data)
        return read_gltf_document(doc, binary, resolve, fmt="glb", path=str(path), file_size=size)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} is neither a GLB nor valid glTF JSON ({exc})") from exc
    if not isinstance(doc, dict) or "asset" not in doc:
        raise ValueError(f"{path.name} is JSON but not a glTF document (no 'asset' object)")
    return read_gltf_document(doc, None, resolve, fmt="gltf", path=str(path), file_size=size)
