"""Plan-view renderer for scenes (Pillow): footprints coloured by module, paths, sightline corridors, focal points, problems."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from . import geom, kit as K, scene as SC

BG, GRID, TEXT = (24, 26, 32), (38, 41, 50), (230, 232, 238)
SCALE = 12


def render(scene: dict, kit: dict, path: str | Path, *, show_labels: bool = True) -> str:
    x0, z0, w, d = SC.region_rect(scene)
    img = Image.new("RGB", (int(w * SCALE) + 24, int(d * SCALE) + 24), BG)
    dr = ImageDraw.Draw(img, "RGBA")

    def P(x, z):
        return (12 + (x - x0) * SCALE, 12 + (z - z0) * SCALE)

    dr.rectangle([*P(x0, z0), *P(x0 + w, z0 + d)], outline=(120, 124, 140), fill=(30, 32, 40))
    for gx in range(int(w) + 1):
        if gx % 5 == 0:
            dr.line([P(x0 + gx, z0), P(x0 + gx, z0 + d)], fill=GRID)
    for gz in range(int(d) + 1):
        if gz % 5 == 0:
            dr.line([P(x0, z0 + gz), P(x0 + w, z0 + gz)], fill=GRID)
    for ex in scene["exclusions"]:
        dr.rectangle([*P(ex[0], ex[1]), *P(ex[0] + ex[2], ex[1] + ex[3])], fill=(200, 60, 60, 60), outline=(220, 90, 90))
    for p in scene["paths"]:
        pts = [tuple(q) for q in p["points"]]
        for a, b in zip(pts, pts[1:]):
            dr.polygon([P(*c) for c in geom.segment_rect(a, b, p["width"])], fill=(80, 200, 120, 55), outline=(80, 200, 120, 160))
    for c in scene["corridors"]:
        dr.polygon([P(*q) for q in geom.segment_rect(tuple(c["from"]), tuple(c["to"]), c["width"])], fill=(90, 150, 255, 40), outline=(90, 150, 255, 140))
    for f_ in scene["focal"]:
        cx, cz = P(*f_["at"])
        r = f_["radius"] * SCALE
        dr.ellipse([cx - r, cz - r, cx + r, cz + r], outline=(250, 210, 60, 180), width=2)
    mods = K.index(kit)
    bad = {f["where"] for f in SC.validate(scene, kit) if f["severity"] == "error"}
    ordered = sorted(scene["instances"], key=lambda i: (mods.get(i["module"], {}).get("collision", True), i["id"]))  # decor underneath
    for i in ordered:
        m = mods.get(i["module"])
        if not m:
            continue
        poly = SC.poly_of(i, m)
        color = tuple(int(m.get("color", "#9a9a9a")[k : k + 2], 16) for k in (1, 3, 5))
        dr.polygon([P(*c) for c in poly], fill=color + (120 if not m["collision"] else 230,), outline=(255, 70, 70) if i["id"] in bad else (20, 20, 24))
        cx, cz = P(*i["at"][:2])
        fx, fz = geom.forward(i["yaw"])
        dr.line([(cx, cz), (cx + fx * 10, cz + fz * 10)], fill=(255, 255, 255, 220), width=2)
        if i.get("locked"):
            dr.ellipse([cx - 3, cz - 3, cx + 3, cz + 3], fill=(250, 210, 60))
        if show_labels and m["footprint"][0] * i["scale"] >= 3:
            dr.text((cx - 12, cz - 5), i["module"][:6], fill=TEXT)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return str(out)
