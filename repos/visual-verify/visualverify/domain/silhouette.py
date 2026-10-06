"""Silhouette: mask extraction, IoU against a reference mask, centroid and bounding-box offsets.

A mask is a boolean image: True = subject. It is only as good as the way it was extracted, so every result says how (``method``) and flags the cases where
extraction is unreliable (busy backgrounds, a subject touching the border). Nothing here knows what the subject IS.
"""

from __future__ import annotations

import numpy as np

from . import imageio as IO
from . import pixels as P

METHODS = ("auto", "alpha", "background", "luminance", "mask")


def otsu(lum: np.ndarray, bins: int = 64) -> float:
    hist, edges = np.histogram(lum, bins=bins, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * (edges[:-1] + edges[1:]) / 2)
    denom = omega * (1 - omega)
    sigma = np.where(denom > 1e-12, (mu[-1] * omega - mu) ** 2 / np.maximum(denom, 1e-12), 0.0)
    i = int(sigma.argmax())
    return float((edges[i] + edges[i + 1]) / 2)


def border_pixels(a: np.ndarray) -> np.ndarray:
    """The one-pixel frame of an (H, W[, C]) array, as (N[, C])."""
    return np.concatenate([a[0], a[-1], a[1:-1, 0], a[1:-1, -1]]) if a.shape[0] > 2 and a.shape[1] > 2 else a.reshape(-1, *a.shape[2:])


def extract_mask(img: IO.Loaded, method: str = "auto", bg_tolerance: float = 0.12) -> tuple[np.ndarray, dict]:
    """``(mask, info)``. Methods: ``alpha`` (alpha >= 0.5), ``background`` (RGB distance from the median border colour > bg_tolerance, on a 0-1 scale),
    ``luminance`` (Otsu threshold, subject = the class that is not mostly on the border), ``mask`` (the image IS a mask: luminance >= 0.5),
    ``auto`` (alpha when the image has real transparency, else background)."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    used = method
    if method == "auto":
        used = "alpha" if img.has_alpha else "background"
    info: dict = {"method": used}
    if used == "alpha":
        if img.alpha is None:
            raise ValueError("this image has no alpha channel: use method 'background', 'luminance' or 'mask'")
        mask = img.alpha >= 0.5
    elif used == "background":
        bg = np.median(border_pixels(img.rgb), axis=0)
        dist = np.sqrt(((img.rgb - bg) ** 2).sum(-1)) / np.sqrt(3.0)
        mask = dist > bg_tolerance
        info["background"] = P.to_hex(bg)
    elif used == "luminance":
        lum = P.luminance(img.rgb)
        t = otsu(lum)
        mask = lum < t
        if border_pixels(mask).mean() > 0.5:
            mask = ~mask
        info["threshold"] = round(t, 4)
    else:  # mask
        mask = P.luminance(img.rgb) >= 0.5
    info["fill"] = float(mask.mean())
    info["border_fill"] = float(border_pixels(mask).mean())
    return mask, info


def mask_warnings(info: dict, label: str) -> list[str]:
    w = []
    if info["fill"] == 0.0:
        w.append(f"{label}: the mask is empty (nothing differs from the background): the background may be busy or the subject the same colour as it")
    elif info["fill"] > 0.95:
        w.append(f"{label}: the mask covers {info['fill']:.0%} of the image: the subject may fill the frame or the background was not found")
    if info["border_fill"] > 0.5 and info["fill"] > 0:
        w.append(f"{label}: over half of the image border is inside the mask: the background is probably not plain, so the silhouette is unreliable")
    return w


def describe(mask: np.ndarray, min_component_share: float) -> dict:
    """Area, bounding box (x0, y0, x1, y1; x1/y1 exclusive), centroid (mean pixel index), and component structure of one mask."""
    h, w = mask.shape
    n = int(mask.sum())
    out: dict = {"area_px": n, "fill": n / (h * w), "size": [w, h]}
    if n == 0:
        return {**out, "bbox": None, "centroid": None, "components": 0, "largest_share": None}
    ys, xs = np.nonzero(mask)
    sizes = P.components(mask)
    out.update(bbox=[int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], centroid=[float(xs.mean()), float(ys.mean())],
               components=sum(1 for s in sizes if s / (h * w) >= min_component_share), largest_share=sizes[0] / n)
    return out


def compare_masks(cand: np.ndarray, ref: np.ndarray) -> dict:
    """IoU, Dice, centroid offset and bounding-box offset between two same-size masks (candidate minus reference)."""
    if cand.shape != ref.shape:
        raise ValueError("masks must have the same shape")
    h, w = cand.shape
    inter, union = int((cand & ref).sum()), int((cand | ref).sum())
    a, b = int(cand.sum()), int(ref.sum())
    out: dict = {"intersection_px": inter, "union_px": union, "iou": None if union == 0 else inter / union, "dice": None if a + b == 0 else 2 * inter / (a + b)}
    dc, dr = describe(cand, 1.0), describe(ref, 1.0)
    if dc["centroid"] is not None and dr["centroid"] is not None:
        dx, dy = dc["centroid"][0] - dr["centroid"][0], dc["centroid"][1] - dr["centroid"][1]
        db = [dc["bbox"][i] - dr["bbox"][i] for i in range(4)]
        out.update(centroid_offset_px=[dx, dy], centroid_offset=float(np.hypot(dx / w, dy / h)), bbox_offset_px=db,
                   bbox_offset=float(max(abs(db[0]) / w, abs(db[2]) / w, abs(db[1]) / h, abs(db[3]) / h)),
                   candidate_only_share=int((cand & ~ref).sum()) / union, reference_only_share=int((ref & ~cand).sum()) / union)
    return out
