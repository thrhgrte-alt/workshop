"""Offline preview: a timeline sheet showing size, transparency and colour over each emitter's lifetime.

This is a *diagnostic picture of the plan's curves*, not a render of how the effect looks in Studio. For
the real look, run the preview Luau and capture the viewport with Studio's ``screen_capture`` tool using
the camera setups from ``analysis.camera_setups``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

W, PLOT_W, PLOT_H, PAD = 960, 280, 90, 14
BG, GRID, INK = (24, 26, 32), (60, 64, 74), (225, 228, 235)
SIZE_C, TRANSP_C = (110, 200, 255), (255, 170, 90)


def _curve(draw: ImageDraw.ImageDraw, box: tuple, keys: list, vmax: float, color: tuple) -> None:
    x0, y0, x1, y1 = box
    draw.rectangle(box, outline=GRID)
    pts = [(x0 + k[0] * (x1 - x0), y1 - min(k[1] / vmax, 1.0) * (y1 - y0)) for k in keys]
    if len(pts) > 1:
        draw.line(pts, fill=color, width=2)
    for p in pts:
        draw.ellipse((p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2), fill=color)


def _gradient(draw: ImageDraw.ImageDraw, box: tuple, keys: list) -> None:
    x0, y0, x1, y1 = box
    for x in range(int(x0), int(x1)):
        t = (x - x0) / max(x1 - x0, 1)
        for a, b in zip(keys, keys[1:]):
            if a[0] <= t <= b[0]:
                f = (t - a[0]) / max(b[0] - a[0], 1e-9)
                rgb = tuple(int((a[1][i] + (b[1][i] - a[1][i]) * f) * 255) for i in range(3))
                draw.line((x, y0, x, y1), fill=rgb)
                break
    draw.rectangle(box, outline=GRID)


def render_timeline(plan: dict, path: str | Path) -> str:
    rows = [i for i in plan["instances"] if i["class"] in ("ParticleEmitter", "Beam", "Trail")]
    height = PAD + max(1, len(rows)) * (PLOT_H + 2 * PAD + 14)
    img = Image.new("RGB", (W, height), BG)
    d = ImageDraw.Draw(img)
    y = PAD
    for inst in rows:
        p = inst["properties"]
        d.text((PAD, y), f"{inst['id']}  ({inst['class']})", fill=INK)
        top = y + 16
        size_key = {"ParticleEmitter": "Size", "Beam": None, "Trail": "WidthScale"}[inst["class"]]
        if size_key and size_key in p:
            keys = p[size_key]["keys"]
            vmax = max(max(k[1] for k in keys), 1e-6)
            d.text((PAD, top - 2), f"{size_key} (max {vmax:.2f})", fill=SIZE_C)
            _curve(d, (PAD, top + 12, PAD + PLOT_W, top + 12 + PLOT_H - 12), keys, vmax, SIZE_C)
        if "Transparency" in p:
            x = PAD * 2 + PLOT_W
            d.text((x, top - 2), "Transparency (0 solid .. 1 clear)", fill=TRANSP_C)
            _curve(d, (x, top + 12, x + PLOT_W, top + 12 + PLOT_H - 12), p["Transparency"]["keys"], 1.0, TRANSP_C)
        if "Color" in p and p["Color"]["t"] == "ColorSequence":
            x = PAD * 3 + PLOT_W * 2
            d.text((x, top - 2), "Colour over life", fill=INK)
            _gradient(d, (x, top + 12, x + PLOT_W, top + 12 + 30), p["Color"]["keys"])
        extra = []
        if "Lifetime" in p and isinstance(p["Lifetime"], dict):
            extra.append(f"lifetime {p['Lifetime']['min']:.2f}-{p['Lifetime']['max']:.2f}s")
        if "Rate" in p:
            extra.append(f"rate {p['Rate']:.0f}/s")
        if inst["id"] in plan.get("emit", {}):
            extra.append(f"burst {plan['emit'][inst['id']]}")
        if extra:
            d.text((PAD * 3 + PLOT_W * 2, top + 52), "  ".join(extra), fill=INK)
        y += PLOT_H + 2 * PAD + 14
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return str(out)
