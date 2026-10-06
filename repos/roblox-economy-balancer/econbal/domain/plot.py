"""Progression curves as a PNG, drawn with Pillow (matplotlib is used only if you ask for it and it is installed).

Top panel: net income per active minute of the main currency over active time (log scale), one step line per archetype.
Bottom panel: purchases made over active time (staircase), one line per archetype, with a tick at every purchase.
All plotted numbers are the simulator's; the plot adds no estimates.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

PALETTE = ["#1f77b4", "#d95f02", "#1b9e77", "#7570b3", "#e7298a", "#666666"]
BG, FG, GRID = "#ffffff", "#222222", "#dddddd"


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def _ticks(lo: float, hi: float, n: int = 6) -> list[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(t)
        t += step
    return out


def series(sims: dict[str, dict], currency: str | None = None) -> dict[str, dict]:
    out = {}
    for name, sm in sims.items():
        cur = currency or sm["currencies"][0]
        rate = [(t["minute"], t["rates"][cur]) for t in sm["rate_trace"]]
        steps = [(0, 0)] + [(p["minute"], i + 1) for i, p in enumerate(sm["purchases"])]
        out[name] = {"currency": cur, "rate": rate, "purchases": steps, "horizon_minutes": sm["horizon_minutes"]}
    return out


def render(sims: dict[str, dict], path: Path, *, title: str = "", currency: str | None = None, log_y: bool = True, size: tuple[int, int] = (960, 640),
           backend: str = "pillow") -> dict:
    data = series(sims, currency)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backend == "matplotlib":
        return _render_mpl(data, path, title, log_y, size)
    if backend != "pillow":
        raise ValueError("backend must be 'pillow' or 'matplotlib'")
    from PIL import Image, ImageDraw

    W, H = size
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_small, f_med = _font(12), _font(14)
    horizon = max(s["horizon_minutes"] for s in data.values())
    ml, mr, mt, mb = 70, 20, 40, 36
    ph = (H - mt - mb - 50) // 2
    panels = [(mt, "Net income per active minute" + (" (log scale)" if log_y else ""), "rate"), (mt + ph + 50, "Purchases made", "purchases")]
    d.text((ml, 10), title or "Progression curves", fill=FG, font=f_med)

    def X(m: float) -> float:
        return ml + (W - ml - mr) * (m / horizon if horizon else 0)

    for top, label, kind in panels:
        bottom = top + ph
        d.rectangle([ml, top, W - mr, bottom], outline=GRID)
        d.text((ml, top - 16), label, fill=FG, font=f_small)
        if kind == "rate":
            vals = [v for s in data.values() for _, v in s["rate"] if v > 0]
            lo, hi = (min(vals), max(vals)) if vals else (1.0, 10.0)
            if log_y:
                lo_l, hi_l = math.floor(math.log10(max(lo, 1e-9))), math.ceil(math.log10(max(hi, lo * 1.01)))
                hi_l = max(hi_l, lo_l + 1)
                Y = lambda v: bottom - (bottom - top) * ((math.log10(max(v, 10 ** lo_l)) - lo_l) / (hi_l - lo_l))
                for k in range(lo_l, hi_l + 1):
                    y = Y(10 ** k)
                    d.line([ml, y, W - mr, y], fill=GRID)
                    d.text((6, y - 6), f"{10 ** k:g}", fill=FG, font=f_small)
            else:
                hi2 = hi * 1.05
                Y = lambda v: bottom - (bottom - top) * (v / hi2 if hi2 else 0)
                for t in _ticks(0, hi2):
                    d.line([ml, Y(t), W - mr, Y(t)], fill=GRID)
                    d.text((6, Y(t) - 6), f"{t:g}", fill=FG, font=f_small)
            for i, (name, s) in enumerate(data.items()):
                col = PALETTE[i % len(PALETTE)]
                pts = []
                for j, (m, v) in enumerate(s["rate"]):
                    nxt = s["rate"][j + 1][0] if j + 1 < len(s["rate"]) else s["horizon_minutes"]
                    pts += [(X(m), Y(v)), (X(nxt), Y(v))]
                if len(pts) > 1:
                    d.line(pts, fill=col, width=2)
        else:
            top_n = max(max(n for _, n in s["purchases"]) for s in data.values()) or 1
            Y = lambda v: bottom - (bottom - top) * (v / top_n)
            for t in _ticks(0, top_n, 5):
                d.line([ml, Y(t), W - mr, Y(t)], fill=GRID)
                d.text((6, Y(t) - 6), f"{t:g}", fill=FG, font=f_small)
            for i, (name, s) in enumerate(data.items()):
                col = PALETTE[i % len(PALETTE)]
                pts = []
                for j, (m, n) in enumerate(s["purchases"]):
                    prev_n = s["purchases"][j - 1][1] if j else 0
                    pts += [(X(m), Y(prev_n)), (X(m), Y(n))]
                pts.append((X(s["horizon_minutes"]), Y(s["purchases"][-1][1])))
                d.line(pts, fill=col, width=2)
                for m, n in s["purchases"][1:]:
                    d.line([X(m), bottom, X(m), bottom - 5], fill=col, width=1)
        for t in _ticks(0, horizon, 8):
            d.line([X(t), bottom, X(t), bottom + 4], fill=FG)
            d.text((X(t) - 12, bottom + 6), f"{t:g}", fill=FG, font=f_small)
    d.text((W // 2 - 60, H - 18), "active minutes of play", fill=FG, font=f_small)
    lx = W - mr - 140
    for i, name in enumerate(data):
        col = PALETTE[i % len(PALETTE)]
        d.line([lx, 20 + 14 * i, lx + 22, 20 + 14 * i], fill=col, width=3)
        d.text((lx + 28, 13 + 14 * i), name, fill=FG, font=f_small)
    img.save(path, format="PNG")
    return {"path": str(path), "width": W, "height": H, "backend": "pillow"}


def _render_mpl(data: dict[str, dict], path: Path, title: str, log_y: bool, size: tuple[int, int]) -> dict:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ValueError("backend='matplotlib' needs matplotlib installed (pip install matplotlib); the default 'pillow' backend needs only Pillow") from exc
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(size[0] / 100, size[1] / 100), dpi=100)
    for i, (name, s) in enumerate(data.items()):
        col = PALETTE[i % len(PALETTE)]
        a1.step([m for m, _ in s["rate"]], [v for _, v in s["rate"]], where="post", color=col, label=name)
        a2.step([m for m, _ in s["purchases"]], [n for _, n in s["purchases"]], where="post", color=col, label=name)
    if log_y:
        a1.set_yscale("log")
    a1.set_title(title or "Progression curves")
    a1.set_ylabel("net income per active minute")
    a2.set_ylabel("purchases made")
    a2.set_xlabel("active minutes of play")
    a1.legend()
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)
    return {"path": str(path), "width": size[0], "height": size[1], "backend": "matplotlib"}
