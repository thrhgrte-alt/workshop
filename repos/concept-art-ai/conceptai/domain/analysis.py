"""Objective image measurements for concept art, and how they feed the rubric.

Everything here is a *measurement of pixels* (numpy + Pillow). It can say "the dominant colours are far from the brief's palette" or "the value
range is narrow", never "this is good". Readability and novelty scores are heuristics built from the measurements; the rubric keeps the
subjective criteria (brief adherence, usability as reference, final style fit) for a person.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Sequence

from ..core import imaging as I
from ..core.retrieval import palette_similarity

try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    np = Image = None  # type: ignore

DARK, LIGHT = 0.33, 0.66
THUMB = 16


def available() -> bool:
    return I.available()


def _centroid_of_edges(arr) -> tuple[float, float]:
    lum = I.luminance(arr)
    gy, gx = np.gradient(lum)
    mag = np.hypot(gx, gy)
    tot = float(mag.sum())
    if tot < 1e-9:
        return 0.5, 0.5
    h, w = lum.shape
    ys, xs = np.mgrid[0:h, 0:w]
    return float((mag * xs).sum() / tot / w), float((mag * ys).sum() / tot / h)


def check_box(box: Sequence[float]) -> list[float]:
    if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box) and box[0] < box[2] and box[1] < box[3]):
        raise ValueError("focal_box must be [x0, y0, x1, y1] in 0-1 image coordinates with x0<x1, y0<y1")
    return list(box)


def measure(path: str | Path, *, target_palette: Sequence[str] = (), focal_box: Sequence[float] | None = None, max_side: int = 384) -> dict:
    """Pixel measurements for one image. ``focal_box`` is (x0, y0, x1, y1) in 0-1 image coordinates."""
    if focal_box:
        focal_box = check_box(focal_box)
    arr = I.load_rgb(path, max_side)
    lum = I.luminance(arr)
    stats = I.luminance_stats(arr)
    sil = I.silhouette_stats(arr)
    cx, cy = _centroid_of_edges(arr)
    m = {
        "size": [int(arr.shape[1]), int(arr.shape[0])],
        "value_range": stats["contrast"], "lum_mean": stats["mean"], "lum_std": stats["std"],
        "value_structure": {"dark": float((lum < DARK).mean()), "mid": float(((lum >= DARK) & (lum <= LIGHT)).mean()), "light": float((lum > LIGHT).mean())},
        "edge_density": I.edge_density(arr), "saturation": I.saturation_mean(arr), "colorfulness": I.colorfulness(arr),
        "detail_frequency": I.detail_frequency(arr), "palette": I.palette(arr, 5),
        "silhouette_fill": sil["fill"], "silhouette_components": sil["components"], "silhouette_largest_share": sil["largest_share"],
        "edge_centroid": [cx, cy],
    }
    if target_palette:
        m["palette_adherence"] = I.palette_adherence(arr, target_palette)
    if focal_box:
        m["focal_contrast"] = I.region_contrast(arr, focal_box)
    return m


def _stamp(path: str | Path) -> tuple[str, int, int]:
    st = os.stat(path)
    return str(Path(path).resolve()), st.st_mtime_ns, st.st_size


@functools.lru_cache(maxsize=512)
def _features(key: tuple[str, int, int]):
    arr = I.load_rgb(key[0], 64)
    img = Image.fromarray((I.luminance(arr) * 255).astype("uint8")).resize((THUMB, THUMB), Image.BILINEAR)
    a = np.asarray(img, dtype=np.float32) / 255.0
    pal = [p["hex"] for p in I.palette(I.load_rgb(key[0], 96), 5)]
    return a - a.mean(), pal


def structure_signature(path: str | Path):
    """A 16x16 mean-centred luminance thumbnail: coarse layout, insensitive to small shifts in colour. Cached per file version."""
    return _features(_stamp(path))[0]


def similarity(path_a: str | Path, path_b: str | Path, palette_a: Sequence[str] | None = None, palette_b: Sequence[str] | None = None) -> float:
    """0-1: 0.5 * palette similarity + 0.5 * layout similarity. 1.0 for identical images."""
    (sa, pa), (sb, pb) = _features(_stamp(path_a)), _features(_stamp(path_b))
    layout = float(max(0.0, 1.0 - np.abs(sa - sb).mean() * 4.0))
    return 0.5 * palette_similarity(palette_a or pa, palette_b or pb) + 0.5 * layout


def novelty_vs_library(path: str | Path, entries: list[dict]) -> dict:
    """1 - similarity to the most similar library image. ``entries`` = [{id, path}] of curated, positive references.

    An empty library gives ``novelty = None`` (unknown), never 1.0: with nothing to compare against, novelty is not established.
    """
    if not entries:
        return {"novelty": None, "nearest": None, "compared": 0, "note": "no curated library images to compare against"}
    best = max(((similarity(path, e["path"]), e["id"]) for e in entries), key=lambda t: t[0])
    return {"novelty": round(1.0 - best[0], 4), "nearest": best[1], "nearest_similarity": round(best[0], 4), "compared": len(entries)}


def diversity(paths: list[str | Path]) -> dict:
    """Pairwise dissimilarity of a set of images (e.g. one per direction)."""
    pairs = [(i, j, 1.0 - similarity(paths[i], paths[j])) for i in range(len(paths)) for j in range(i + 1, len(paths))]
    if not pairs:
        return {"pairs": [], "min_dissimilarity": None, "mean_dissimilarity": None}
    vals = [p[2] for p in pairs]
    return {"pairs": [{"a": i, "b": j, "dissimilarity": round(d, 4)} for i, j, d in pairs], "min_dissimilarity": round(min(vals), 4), "mean_dissimilarity": round(sum(vals) / len(vals), 4)}


def readability(m: dict, ranges: dict) -> dict:
    """0-1 readability from value range, a usable dark/mid/light spread, and not-too-busy edges. A heuristic, labelled as such."""
    vr_min = ranges.get("value_range", {}).get("min", 0.35)
    parts = {"value_range": min(m["value_range"] / vr_min, 1.0) if vr_min else 1.0}
    vs = m["value_structure"]
    present = sum(1 for k in ("dark", "mid", "light") if vs[k] >= 0.05)
    parts["value_structure"] = present / 3
    lo, hi = ranges.get("edge_density", {}).get("min", 0.01), ranges.get("edge_density", {}).get("max", 0.35)
    e = m["edge_density"]
    parts["edge_density"] = 1.0 if lo <= e <= hi else (e / lo if e < lo else max(0.0, 1 - (e - hi) / hi))
    score = sum(parts.values()) / len(parts)
    return {"score": round(score, 3), "parts": {k: round(v, 3) for k, v in parts.items()}, "heuristic": True}


def degenerate(m: dict) -> list[str]:
    out = []
    if m["lum_std"] < 0.02:
        out.append("image is nearly a single flat tone")
    if min(m["size"]) < 64:
        out.append("image is very small")
    if m["colorfulness"] < 1.0 and m["saturation"] < 0.03 and m["lum_std"] < 0.05:
        out.append("image has almost no colour or value variation")
    return out


def check_composition_lock(m: dict, lock: dict) -> list[dict]:
    """Findings for a brief that locks composition: a focal box must stand out and the edge energy must centre near it."""
    f: list[dict] = []
    box = lock.get("focal_box")
    if not box:
        return f
    check_box(box)
    need = lock.get("min_focal_contrast", 0.08)
    got = m.get("focal_contrast")
    if got is None:
        f.append({"severity": "error", "code": "no_focal_measurement", "message": "measure the image with the locked focal_box first"})
        return f
    if got < need:
        f.append({"severity": "error", "code": "focal_contrast_low", "message": f"focal box contrast {got:.3f} is below the locked minimum {need}"})
    tol = lock.get("centroid_tolerance", 0.3)
    fx, fy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    cx, cy = m["edge_centroid"]
    dist = ((cx - fx) ** 2 + (cy - fy) ** 2) ** 0.5
    if dist > tol:
        f.append({"severity": "warning", "code": "detail_far_from_focus", "message": f"edge energy centres {dist:.2f} (image widths) from the focal box; tolerance {tol}"})
    return f
