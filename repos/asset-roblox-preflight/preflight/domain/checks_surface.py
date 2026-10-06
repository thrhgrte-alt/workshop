"""UV and material/texture checks."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .engine import Ctx, implements
from .imageinfo import pixel_means


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


@implements("UV_MISSING", "UV_MISSING_UNTEXTURED", "UV_OUT_OF_RANGE", "UV_OVERLAP", "UV_TEXEL_SPREAD", "UV_WASTED_SPACE", "UV_ZERO_AREA")
def uv_checks(ctx: Ctx):
    oor = ctx.limit("UV_OUT_OF_RANGE", "max_out_of_range_fraction")
    ovl = ctx.limit("UV_OVERLAP", "max_overlap_ratio")
    spread = ctx.limit("UV_TEXEL_SPREAD", "max_spread_ratio")
    cov = ctx.limit("UV_WASTED_SPACE", "min_coverage")
    zmax = ctx.limit("UV_ZERO_AREA", "max_zero_area_triangles")
    for o in ctx.visual:
        m = ctx.metrics[o["id"]]
        src = m["source"]
        has_uv = m.get("has_uv")
        if has_uv is False:
            if ctx.textured(o):
                ctx.add("UV_MISSING", o["name"], "no UV coordinates", ">= 1 UV set", source=src, message="the mesh uses a textured material but has no UV coordinates")
            else:
                ctx.add("UV_MISSING_UNTEXTURED", o["name"], "no UV coordinates", ">= 1 UV set", source=src)
            continue
        if has_uv is None:
            continue
        v = m.get("uv_out_of_range_fraction")
        if v is not None and v > oor:
            ctx.add("UV_OUT_OF_RANGE", o["name"], f"{round(v * 100, 1)}% of vertices", f"{round(oor * 100, 1)}%", source=src, message=f"{round(v * 100, 1)}% of UV vertices are outside 0-1")
        v = m.get("uv_overlap_ratio")
        if v is not None and v > ovl:
            ctx.add("UV_OVERLAP", o["name"], _r(v, 3), ovl, source=src, message=f"{round(v * 100, 1)}% of the covered UV area is covered more than once")
        v = m.get("texel_spread")
        if v is not None and v > spread:
            ctx.add("UV_TEXEL_SPREAD", o["name"], _r(v, 2), spread, source=src, message=f"texel density p95/p5 is {_r(v, 2)}")
        v = m.get("uv_coverage")
        if v is not None and v < cov and m.get("triangles"):
            ctx.add("UV_WASTED_SPACE", o["name"], _r(v, 3), cov, source=src, message=f"UV triangles cover {round(v * 100, 1)}% of the 0-1 square")
        v = m.get("uv_zero_area_tris")
        if v is not None and v > zmax:
            ctx.add("UV_ZERO_AREA", o["name"], v, zmax, source=src, message=f"{v} triangles have zero area in UV space")


def _is_pot(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _img_name(img: dict) -> str:
    return img.get("uri") or img.get("name") or f"image_{img['index']}"


def _bytes(img: dict) -> bytes | None:
    if img.get("embedded"):
        return img.get("_data")
    p = img.get("path")
    if p and Path(p).exists() and Path(p).stat().st_size <= 16 * 1024 * 1024:
        return Path(p).read_bytes()
    return img.get("_data")


def _find_elsewhere(asset: dict, img: dict) -> str | None:
    base = Path(str(img.get("uri") or "")).name
    if not base:
        return None
    for d in asset.get("search_dirs", []):
        root = Path(d)
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, files in os.walk(root):
            if len(Path(dirpath).parts) - base_depth >= 3:
                dirnames[:] = []
            if base in files:
                return str(Path(dirpath) / base)
    return None


def _looks_like_normal(stats: dict, ctx: Ctx) -> bool:
    lo, hi = ctx.limit("TEX_COLORSPACE", "normal_mean_range")
    r, g, b = stats["mean"]
    return lo <= r <= hi and lo <= g <= hi and b >= ctx.limit("TEX_COLORSPACE", "normal_blue_min")


def _name_hit(stem: str, hints: list[str]) -> bool:
    s = stem.lower()
    return any((s.endswith(h) if len(h) <= 2 else h in s) for h in hints)


@implements("MAT_COUNT", "MAT_UNUSED")
def materials(ctx: Ctx):
    used = {m for o in ctx.visual for m in ctx.mesh_material_ids(o)}
    limit = ctx.limit("MAT_COUNT", "max_materials")
    n = len(used) if used or ctx.visual else 0
    if n > limit:
        ctx.add("MAT_COUNT", None, n, limit, limit_key=None, message=f"{n} different materials are used by the visible meshes")
    if ctx.asset.get("geometry_measured", True):
        used_all = {m for o in ctx.meshes for m in ctx.mesh_material_ids(o)}
        for mat in ctx.asset["materials"]:
            if mat["index"] not in used_all:
                ctx.add("MAT_UNUSED", mat.get("name") or f"material_{mat['index']}", "unused", "used by a mesh")


@implements("TEX_MISSING_FILE", "TEX_UNSAFE_PATH", "TEX_FORMAT", "TEX_NON_POT", "TEX_OVERSIZE", "TEX_MIME_MISMATCH")
def texture_files(ctx: Ctx):
    allowed = [x.lower() for x in ctx.limit("TEX_FORMAT", "allowed_formats")]
    pot = ctx.limit("TEX_NON_POT", "require_power_of_two")
    maxpx = ctx.limit("TEX_OVERSIZE", "max_dimension_px")
    for img in ctx.asset["images"]:
        name = _img_name(img)
        if img.get("unsafe_uri"):
            ctx.add("TEX_UNSAFE_PATH", name, img.get("uri"), "relative path inside the project", message=f"'{img.get('uri')}' is an absolute path or leaves the project folder")
            continue
        if not img.get("embedded") and not img.get("exists"):
            elsewhere = _find_elsewhere(ctx.asset, img)
            ctx.add("TEX_MISSING_FILE", name, img.get("uri"), "file exists", message=f"no file at '{img.get('uri')}'" + (f"; a file with that name exists at {elsewhere}" if elsewhere else ""),
                    detail=f"found elsewhere: {elsewhere}" if elsewhere else None)
            continue
        fmt = img.get("format")
        if fmt is None:
            continue
        norm = "jpeg" if fmt == "jpg" else fmt
        if norm == "unknown" or norm not in allowed:
            ctx.add("TEX_FORMAT", name, "unreadable or unknown image" if norm == "unknown" else norm, allowed, limit_key="allowed_formats",
                    message=("the file is not a readable image" if norm == "unknown" else f"{norm} is not in the accepted formats"))
            continue
        w, h = img.get("width"), img.get("height")
        if w and h:
            if pot and not (_is_pot(w) and _is_pot(h)):
                ctx.add("TEX_NON_POT", name, f"{w}x{h}", "power of two", limit_key="require_power_of_two", message=f"{w}x{h} is not a power-of-two size")
            if max(w, h) > maxpx:
                ctx.add("TEX_OVERSIZE", name, f"{w}x{h}", maxpx, limit_key="max_dimension_px", message=f"{w}x{h} is larger than {maxpx} pixels on the long side")
        for issue in img.get("issues", []):
            if "mimeType" in issue:
                ctx.add("TEX_MIME_MISMATCH", name, issue, "declared type equals content", message=issue)


@implements("TEX_COLORSPACE", "TEX_SLOT_HEURISTIC", "TEX_CHANNELS", "TEX_ALPHA_UNUSED")
def texture_slots(ctx: Ctx):
    normal_hints = ctx.limit("TEX_SLOT_HEURISTIC", "normal_name_hints")
    color_hints = ctx.limit("TEX_SLOT_HEURISTIC", "color_name_hints")
    data_slots = ("normal", "metallic_roughness", "occlusion")
    for img in ctx.asset["images"]:
        if not img.get("slots") or img.get("unsafe_uri") or (not img.get("embedded") and not img.get("exists")):
            continue
        name = _img_name(img)
        stem = Path(str(img.get("uri") or img.get("name") or "")).stem
        stats = None
        fmt = img.get("format")
        if fmt in ("png", "jpeg", "jpg", "bmp"):
            data = _bytes(img)
            stats = pixel_means(data) if data else None
        for s in img["slots"]:
            slot = s["slot"]
            decl = img.get("declared_colorspace")
            if decl:
                want_linear = slot in data_slots
                is_linear = decl.lower() in ("non-color", "linear", "raw")
                if want_linear != is_linear:
                    ctx.add("TEX_COLORSPACE", name, f"declared {decl} in the {slot} slot", "Non-Color" if want_linear else "sRGB", source="reported",
                            message=f"{slot} texture is declared {decl}", fix_vars={"measured": f"declared {decl} in the {slot} slot"})
            if stats and slot in ("base_color", "emissive") and _looks_like_normal(stats, ctx):
                ctx.add("TEX_COLORSPACE", name, f"looks like a normal map (mean RGB {stats['mean']}) used as {slot}", "colour image", message=f"looks like a tangent-space normal map but is used as {slot}",
                        fix_vars={"measured": f"it looks like a normal map but is the {slot}"})
            elif stats and slot == "normal" and not _looks_like_normal(stats, ctx):
                ctx.add("TEX_SLOT_HEURISTIC", name, f"normal slot image has mean RGB {stats['mean']}", "mean R,G near 128, B high", message="does not look like a tangent-space normal map",
                        fix_vars={"measured": f"mean RGB {stats['mean']} in the normal slot"})
            if slot in ("base_color", "emissive") and stem and _name_hit(stem, normal_hints):
                ctx.add("TEX_SLOT_HEURISTIC", name, f"normal-style file name in the {slot} slot", "colour name", message=f"the file name looks like a normal map but is in the {slot} slot",
                        fix_vars={"measured": f"normal-style name in the {slot} slot"})
            if slot == "normal" and stem and _name_hit(stem, color_hints):
                ctx.add("TEX_SLOT_HEURISTIC", name, "colour-style file name in the normal slot", "normal name", message="the file name looks like a colour texture but is in the normal slot",
                        fix_vars={"measured": "colour-style name in the normal slot"})
            ch = img.get("channels")
            if ch is not None and slot in ("normal", "metallic_roughness") and ch < 3:
                ctx.add("TEX_CHANNELS", name, ch, 3, message=f"{slot} texture has {ch} channel(s)")
            if slot == "base_color" and img.get("has_alpha"):
                mat = next((m for m in ctx.asset["materials"] if (m.get("name") or f"material_{m['index']}") == s["material"]), None)
                if mat and mat.get("alpha_mode", "OPAQUE") == "OPAQUE":
                    ctx.add("TEX_ALPHA_UNUSED", name, "alpha channel present", "none", message=f"material '{s['material']}' is opaque but the colour texture has alpha")
