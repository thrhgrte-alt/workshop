"""Synthetic test assets, built by script: a tiny GLB/glTF writer, primitive meshes, PNG textures, OBJ and summary files.

Nothing here is a real Blender export. These files exercise the reader and the rules; they say nothing about what Blender,
Studio or Roblox produce or accept. ``scripts/make_examples.py`` writes the catalogue into ``examples/assets``.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import zlib
from pathlib import Path

# ----------------------------------------------------------------------------------------------------------- PNG
def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(width: int, height: int, rgb=(200, 120, 60), *, color_type: int = 2, extra_chunks: list[tuple[bytes, bytes]] | None = None, gradient: bool = False) -> bytes:
    """A valid 8-bit PNG. color_type 0 grey, 2 RGB, 4 grey+alpha, 6 RGBA. Solid colour (or a small deterministic gradient)."""
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    if color_type in (0, 4):
        px = bytes([rgb[0]]) + (b"\xff" if color_type == 4 else b"")
    elif color_type == 2:
        px = bytes(rgb)
    else:
        px = bytes(rgb) + b"\xff"
    if gradient:
        raw = bytearray()
        for y in range(height):
            raw.append(0)
            for x in range(width):
                g = (x * 255 // max(1, width - 1) + y * 3) % 256
                raw += bytes([(rgb[0] + g) % 256, (rgb[1] + g // 2) % 256, rgb[2]][:3]) if ch >= 3 else bytes([g])
                if ch in (2, 4):
                    pass
                if color_type == 6:
                    raw.append(255)
                if color_type == 4:
                    raw.append(255)
        body = bytes(raw)
    else:
        row = b"\x00" + px * width
        body = row * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
    for tag, data in extra_chunks or []:
        out += _chunk(tag, data)
    return out + _chunk(b"IDAT", zlib.compress(body, 9)) + _chunk(b"IEND", b"")


NORMAL_FLAT = (128, 128, 255)


# ------------------------------------------------------------------------------------------------------- meshes
def _faces():
    X, Y, Z = (1, 0, 0), (0, 1, 0), (0, 0, 1)
    nx, ny, nz = (-1, 0, 0), (0, -1, 0), (0, 0, -1)
    # (normal, u axis, v axis) with u x v = normal, so the CCW winding seen from outside has the outward normal
    return [(X, Y, Z), (nx, Z, Y), (Y, Z, X), (ny, X, Z), (Z, X, Y), (nz, Y, X)]


def box(dims=(1.0, 1.0, 1.0), *, sub: int = 1, origin: str = "bottom", flip_normals: bool = False, invert_winding: bool = False, uv: str = "atlas") -> dict:
    """An axis-aligned box. 6*(sub+1)^2 vertices, 12*sub^2 triangles, per-face vertices, outward CCW winding, atlas UVs (3x2 cells)."""
    pos, nor, uvs, idx = [], [], [], []
    hx, hy, hz = (d / 2 for d in dims)
    off = (0.0, hy if origin == "bottom" else 0.0, 0.0)
    for f, (n, u, v) in enumerate(_faces()):
        cell = (f % 3, f // 3)
        base = len(pos)
        for j in range(sub + 1):
            for i in range(sub + 1):
                a, b = -1 + 2 * i / sub, -1 + 2 * j / sub
                p = tuple((n[k] * (hx, hy, hz)[k]) + a * u[k] * (hx, hy, hz)[k] + b * v[k] * (hx, hy, hz)[k] + off[k] for k in range(3))
                pos.append(p)
                nor.append(tuple(-c if flip_normals else c for c in n))
                uvs.append(((cell[0] + i / sub) / 3.0, (cell[1] + j / sub) / 2.0) if uv == "atlas" else (i / sub, j / sub))
        w = sub + 1
        for j in range(sub):
            for i in range(sub):
                a, b, c, d = base + j * w + i, base + j * w + i + 1, base + (j + 1) * w + i + 1, base + (j + 1) * w + i
                idx += [a, b, c, a, c, d]
    if invert_winding:
        idx = [i for t in range(0, len(idx), 3) for i in (idx[t], idx[t + 2], idx[t + 1])]
    return {"positions": pos, "normals": nor, "uvs": None if uv == "none" else uvs, "indices": idx}


def cylinder(radius=0.5, height=1.0, segments=12, *, y0=0.0, uv_box=(0.0, 0.0, 0.5, 1.0)) -> dict:
    """Closed cylinder along +Y from y0 with caps. UV islands sit inside uv_box=(u0, v0, u1, v1): side strip in the upper half, caps as discs in the lower half."""
    u0, v0, u1, v1 = uv_box
    W, H = u1 - u0, v1 - v0
    pos, nor, uvs, idx = [], [], [], []
    for k in range(segments + 1):
        a = 2 * math.pi * k / segments
        c, s = math.cos(a), math.sin(a)
        for yy, vv in ((y0, 0.5), (y0 + height, 1.0)):
            pos.append((radius * c, yy, radius * s))
            nor.append((c, 0.0, s))
            uvs.append((u0 + W * k / segments, v0 + H * vv))
    for k in range(segments):
        a, b, c, d = 2 * k, 2 * k + 1, 2 * (k + 1) + 1, 2 * (k + 1)
        idx += [a, c, d, a, b, c]
    for top, ny_, centre_uv in ((True, 1.0, (u0 + W * 0.25, v0 + H * 0.25)), (False, -1.0, (u0 + W * 0.75, v0 + H * 0.25))):
        yy = y0 + height if top else y0
        cidx = len(pos)
        pos.append((0.0, yy, 0.0))
        nor.append((0.0, ny_, 0.0))
        uvs.append(centre_uv)
        rim = len(pos)
        rr = min(W, H) * 0.24
        for k in range(segments):
            a = 2 * math.pi * k / segments
            pos.append((radius * math.cos(a), yy, radius * math.sin(a)))
            nor.append((0.0, ny_, 0.0))
            uvs.append((centre_uv[0] + rr * math.cos(a), centre_uv[1] + rr * math.sin(a)))
        for k in range(segments):
            a, b = rim + k, rim + (k + 1) % segments
            idx += [cidx, b, a] if top else [cidx, a, b]
    return {"positions": pos, "normals": nor, "uvs": uvs, "indices": idx}


def merge(*meshes: dict) -> dict:
    out = {"positions": [], "normals": [], "uvs": [], "indices": []}
    for m in meshes:
        base = len(out["positions"])
        out["positions"] += m["positions"]
        out["normals"] += m["normals"]
        out["uvs"] += m["uvs"]
        out["indices"] += [i + base for i in m["indices"]]
    return out


def triangle_mesh(tris: list[tuple], uvs: list[tuple] | None = None) -> dict:
    pos, idx, uv = [], [], []
    for k, t in enumerate(tris):
        pos += list(t)
        idx += [3 * k, 3 * k + 1, 3 * k + 2]
        if uvs:
            uv += list(uvs[k])
    nor = []
    for k in range(len(tris)):
        a, b, c = tris[k]
        n = ((b[1] - a[1]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[1] - a[1]), (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2]),
             (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        ln = math.sqrt(sum(x * x for x in n)) or 1.0
        nor += [tuple(x / ln for x in n)] * 3
    return {"positions": pos, "normals": nor, "uvs": uv if uvs else None, "indices": idx}


# ----------------------------------------------------------------------------------------------------- glTF writer
class GltfBuilder:
    def __init__(self, generator: str = "preflight synthetic builder (not Blender)"):
        self.doc: dict = {"asset": {"version": "2.0", "generator": generator}, "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [],
                          "images": [], "textures": [], "accessors": [], "bufferViews": [], "buffers": [{"byteLength": 0}]}
        self.bin = bytearray()
        self.external: dict[str, bytes] = {}

    # buffers
    def _view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.doc["bufferViews"].append(v)
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, values: list, ctype: int, typ: str, *, target: int | None = None, normalized: bool = False, minmax: bool = False) -> int:
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[typ]
        fmt = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}[ctype]
        flat = [values[i] if n == 1 else values[i // n][i % n] for i in range(len(values) * n)] if n > 1 else list(values)
        data = struct.pack("<" + fmt * len(flat), *flat)
        a = {"bufferView": self._view(data, target), "componentType": ctype, "count": len(values), "type": typ}
        if normalized:
            a["normalized"] = True
        if minmax and values:
            cols = list(zip(*values)) if n > 1 else [values]
            a["min"], a["max"] = [min(c) for c in cols], [max(c) for c in cols]
        self.doc["accessors"].append(a)
        return len(self.doc["accessors"]) - 1

    # materials
    def image(self, png: bytes, name: str, *, external: str | None = None) -> int:
        if external:
            self.external[external] = png
            self.doc["images"].append({"name": name, "uri": external})
        else:
            self.doc["images"].append({"name": name, "mimeType": "image/png", "bufferView": self._view(png)})
        return len(self.doc["images"]) - 1

    def image_ref(self, uri: str, name: str) -> int:
        """An external image reference with no file behind it (a missing texture)."""
        self.doc["images"].append({"name": name, "uri": uri})
        return len(self.doc["images"]) - 1

    def material(self, name: str, *, base: int | None = None, normal: int | None = None, mr: int | None = None, alpha: str = "OPAQUE") -> int:
        def tex(img):
            self.doc["textures"].append({"source": img})
            return {"index": len(self.doc["textures"]) - 1}

        m: dict = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}
        if base is not None:
            m["pbrMetallicRoughness"]["baseColorTexture"] = tex(base)
        if mr is not None:
            m["pbrMetallicRoughness"]["metallicRoughnessTexture"] = tex(mr)
        if normal is not None:
            m["normalTexture"] = tex(normal)
        if alpha != "OPAQUE":
            m["alphaMode"] = alpha
        self.doc["materials"].append(m)
        return len(self.doc["materials"]) - 1

    # meshes and nodes
    def mesh(self, name: str, geo: dict, material: int | None = None, *, joints=None, weights=None) -> int:
        at = {"POSITION": self.accessor([tuple(p) for p in geo["positions"]], 5126, "VEC3", target=34962, minmax=True)}
        if geo.get("normals"):
            at["NORMAL"] = self.accessor([tuple(n) for n in geo["normals"]], 5126, "VEC3", target=34962)
        if geo.get("uvs"):
            at["TEXCOORD_0"] = self.accessor([tuple(u) for u in geo["uvs"]], 5126, "VEC2", target=34962)
        if joints is not None:
            at["JOINTS_0"] = self.accessor(joints, 5123, "VEC4", target=34962)
            at["WEIGHTS_0"] = self.accessor(weights, 5126, "VEC4", target=34962)
        prim = {"attributes": at, "mode": 4}
        if geo["indices"]:
            ict = 5123 if max(geo["indices"]) < 65535 else 5125
            prim["indices"] = self.accessor(geo["indices"], ict, "SCALAR", target=34963)
        if material is not None:
            prim["material"] = material
        self.doc["meshes"].append({"name": name, "primitives": [prim]})
        return len(self.doc["meshes"]) - 1

    def node(self, name: str | None, *, mesh: int | None = None, skin: int | None = None, t=None, r=None, s=None, children=None, extras=None, root: bool = True) -> int:
        n: dict = {}
        if name is not None:
            n["name"] = name
        if mesh is not None:
            n["mesh"] = mesh
        if skin is not None:
            n["skin"] = skin
        if t:
            n["translation"] = list(t)
        if r:
            n["rotation"] = list(r)
        if s:
            n["scale"] = list(s)
        if children:
            n["children"] = list(children)
        if extras:
            n["extras"] = extras
        self.doc["nodes"].append(n)
        i = len(self.doc["nodes"]) - 1
        if root:
            self.doc["scenes"][0]["nodes"].append(i)
        return i

    def attach(self, parent: int, child: int) -> None:
        self.doc["nodes"][parent].setdefault("children", []).append(child)
        if child in self.doc["scenes"][0]["nodes"]:
            self.doc["scenes"][0]["nodes"].remove(child)

    def skin(self, joints: list[int], name: str = "rig") -> int:
        self.doc.setdefault("skins", []).append({"name": name, "joints": joints})
        return len(self.doc["skins"]) - 1

    def animation(self, name: str, node: int, fps: float, frames: int, axis=(0, 1, 0), turns: float = 1.0) -> None:
        times = [k / fps for k in range(frames + 1)]
        quats = []
        for k in range(frames + 1):
            a = 2 * math.pi * turns * k / frames
            sa, ca = math.sin(a / 2), math.cos(a / 2)
            quats.append((axis[0] * sa, axis[1] * sa, axis[2] * sa, ca))
        t = self.accessor(times, 5126, "SCALAR", minmax=True)
        q = self.accessor(quats, 5126, "VEC4")
        anim = {"name": name, "samplers": [{"input": t, "output": q, "interpolation": "LINEAR"}], "channels": [{"sampler": 0, "target": {"node": node, "path": "rotation"}}]}
        self.doc.setdefault("animations", []).append(anim)

    def extras(self, extras: dict) -> None:
        self.doc["extras"] = extras

    def finish(self) -> dict:
        d = dict(self.doc)
        d["buffers"] = [{"byteLength": len(self.bin)}]
        for k in ("images", "textures", "materials", "skins", "animations", "meshes", "accessors", "bufferViews"):
            if k in d and not d[k]:
                del d[k]
        return d

    def glb(self) -> bytes:
        d = self.finish()
        js = json.dumps(d, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        binary = bytes(self.bin) + b"\x00" * ((4 - len(self.bin) % 4) % 4)
        body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
        return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body

    def write_glb(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.glb())
        for uri, data in self.external.items():
            (path.parent / uri).parent.mkdir(parents=True, exist_ok=True)
            (path.parent / uri).write_bytes(data)
        return path

    def write_gltf(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        d = self.finish()
        d["buffers"] = [{"byteLength": len(self.bin), "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(self.bin)).decode()}]
        path.write_text(json.dumps(d, indent=1), encoding="utf-8")
        for uri, data in self.external.items():
            (path.parent / uri).parent.mkdir(parents=True, exist_ok=True)
            (path.parent / uri).write_bytes(data)
        return path


def write_obj(path: Path, objects: dict[str, dict], *, mtl: str | None = None, mtl_text: str | None = None, ngon_quad_as_one: bool = False) -> Path:
    """OBJ with per-object geometry from the glTF-style mesh dicts. With ngon_quad_as_one a 5-vertex polygon face is added to the first object."""
    lines = ["# synthetic OBJ written by preflight.domain.synth"]
    if mtl:
        lines.append(f"mtllib {mtl}")
    vbase = 1
    for name, g in objects.items():
        lines.append(f"o {name}")
        for p in g["positions"]:
            lines.append("v %.6f %.6f %.6f" % tuple(p))
        if g.get("uvs"):
            for u in g["uvs"]:
                lines.append("vt %.6f %.6f" % tuple(u))
        if g.get("normals"):
            for n in g["normals"]:
                lines.append("vn %.6f %.6f %.6f" % tuple(n))
        if g.get("material"):
            lines.append(f"usemtl {g['material']}")
        idx = g["indices"]
        for t in range(0, len(idx), 3):
            ids = [vbase + i for i in idx[t:t + 3]]
            has_uv, has_n = bool(g.get("uvs")), bool(g.get("normals"))
            tok = (lambda i: f"{i}/{i}/{i}" if has_uv and has_n else f"{i}/{i}" if has_uv else f"{i}//{i}" if has_n else str(i))
            lines.append("f " + " ".join(tok(i) for i in ids))
        if ngon_quad_as_one:
            n0 = len(g["positions"])
            lines.append("f " + " ".join(str(vbase + k) for k in range(5 if n0 >= 5 else 3)))
            ngon_quad_as_one = False
        vbase += len(g["positions"])
    if mtl_text is not None:
        (path.parent / mtl).write_text(mtl_text, encoding="utf-8")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
