"""Deterministic synthetic images (Pillow). Used by the PlaceholderAdapter, the examples and the tests.

They are *pipeline fixtures*, drawn from a direction's palette and axes: sky gradient, ground band, and a few blocks whose shape follows the
silhouette axis. They are labelled PLACEHOLDER in the image. They are not generated art and say nothing about model quality.
"""

from __future__ import annotations

import random
from pathlib import Path

from .imaging_compat import Image, ImageDraw, need


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def render(direction: dict, width: int, height: int, seed: int = 0, label: bool = True):
    need()
    rng = random.Random(f"{seed}|{direction['id']}")
    pal = {p["role"]: _rgb(p["hex"]) for p in direction["palette"]}
    img = Image.new("RGB", (width, height))
    px = ImageDraw.Draw(img)
    horizon = {"thirds_left": 0.62, "centered_symmetry": 0.55, "leading_lines": 0.7, "framed_depth": 0.6}.get(direction["axes"].get("composition"), 0.6)
    top, bottom = (pal["highlight"], pal["secondary"]) if direction["axes"].get("lighting") not in ("night_lit",) else (pal["shadow"], pal["secondary"])
    for y in range(height):
        t = y / max(height - 1, 1)
        c = _mix(top, bottom, min(t / max(horizon, 1e-6), 1.0)) if t < horizon else _mix(pal["dominant"], pal["shadow"], (t - horizon) / (1 - horizon))
        px.line([(0, y), (width, y)], fill=c)
    sil = direction["axes"].get("silhouette")
    n = {"tall_vertical": 4, "wide_horizontal": 3, "layered_diagonal": 5, "central_mass": 1, "round_bulky": 1, "tall_slender": 1, "angular_wedge": 2, "asymmetric_hooked": 2}.get(sil, 3)
    base_y = int(height * horizon)
    for i in range(n):
        if sil in ("tall_vertical", "tall_slender"):
            bw, bh = width * rng.uniform(0.06, 0.12), height * rng.uniform(0.45, 0.8)
        elif sil == "wide_horizontal":
            bw, bh = width * rng.uniform(0.25, 0.4), height * rng.uniform(0.12, 0.2)
        elif sil in ("central_mass", "round_bulky"):
            bw, bh = width * 0.34, height * 0.45
        else:
            bw, bh = width * rng.uniform(0.1, 0.2), height * rng.uniform(0.2, 0.45)
        cx = width * (0.5 if n == 1 else (i + 0.7) / (n + 0.4))
        shade = _mix(pal["shadow"], pal["dominant"], 0.25 + 0.15 * i / max(n, 1))
        box = [cx - bw / 2, base_y - bh, cx + bw / 2, base_y]
        if sil == "round_bulky":
            px.ellipse(box, fill=shade)
        elif sil in ("angular_wedge", "layered_diagonal"):
            px.polygon([(box[0], box[3]), (box[2], box[3]), (box[2] - bw * 0.2, box[1])], fill=shade)
        else:
            px.rectangle(box, fill=shade)
        # small lit openings give the fixture some edge detail, as real concepts have
        for _ in range(5):
            wx = rng.uniform(box[0] + bw * 0.2, max(box[0] + bw * 0.2, box[2] - bw * 0.4))
            wy = rng.uniform(box[1] + bh * 0.15, max(box[1] + bh * 0.15, box[3] - bh * 0.4))
            px.rectangle([wx, wy, wx + max(bw * 0.12, 3), wy + max(bh * 0.1, 3)], fill=pal["highlight"])
    ax = int(width * 0.7)
    px.ellipse([ax - width * 0.04, base_y - height * 0.3, ax + width * 0.04, base_y - height * 0.3 + width * 0.08], fill=pal["accent"])
    if label:
        px.rectangle([0, 0, 170, 16], fill=(0, 0, 0))
        px.text((4, 2), "PLACEHOLDER - not a generation", fill=(255, 255, 255))
    return img


def solid(color: str, size=(96, 96)):
    need()
    return Image.new("RGB", size, _rgb(color))


def save(img, path: str | Path) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return str(path)


def from_spec(spec: dict, path: str | Path) -> str:
    """Build a deterministic test image from a small spec and save it. Used by the evals and tests so expectations are checkable by hand.

    kinds: ``solid`` {color, size}; ``split`` {left, right, size} (vertical halves); ``box`` {bg, fg, box:[x0,y0,x1,y1], size};
    ``noise`` {seed, size, base}; ``direction`` {direction, size, seed} (the placeholder renderer; direction is a direction dict).
    """
    need()
    kind = spec["kind"]
    size = tuple(spec.get("size", (96, 96)))
    if kind == "solid":
        img = solid(spec["color"], size)
    elif kind == "split":
        img = Image.new("RGB", size, _rgb(spec["left"]))
        ImageDraw.Draw(img).rectangle([size[0] // 2, 0, size[0], size[1]], fill=_rgb(spec["right"]))
    elif kind == "box":
        img = Image.new("RGB", size, _rgb(spec["bg"]))
        x0, y0, x1, y1 = spec["box"]
        ImageDraw.Draw(img).rectangle([x0 * size[0], y0 * size[1], x1 * size[0], y1 * size[1]], fill=_rgb(spec["fg"]))
    elif kind == "noise":
        rng = random.Random(spec.get("seed", 0))
        base = _rgb(spec.get("base", "#808080"))
        img = Image.new("RGB", size)
        img.putdata([tuple(max(0, min(255, c + rng.randint(-90, 90))) for c in base) for _ in range(size[0] * size[1])])
    elif kind == "direction":
        img = render(spec["direction"], size[0], size[1], spec.get("seed", 0), label=False)
    else:
        raise ValueError(f"unknown image spec kind '{kind}'")
    return save(img, path)
