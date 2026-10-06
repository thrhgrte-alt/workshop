"""Objective image measurements (optional: needs numpy + Pillow).

These are *measurements*, useful for rubric checks and regression tests. They
do not judge taste. Each function documents what it actually computes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:  # optional dependency, reported by `doctor`
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover
    np = None  # type: ignore
    Image = None  # type: ignore

from .retrieval import palette_similarity


def available() -> bool:
    return np is not None and Image is not None


def _need() -> None:
    if not available():
        raise RuntimeError("Image analysis needs numpy and Pillow: pip install '.[imaging]'")


def load_rgb(path: str | Path, max_side: int = 512):
    """Load an image as float RGB in [0, 1], downscaled so the longest side <= max_side."""
    _need()
    img = Image.open(path).convert("RGB")
    if max(img.size) > max_side:
        scale = max_side / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def luminance(arr):
    return 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]


def to_hex(rgb) -> str:
    r, g, b = (int(round(float(c) * 255)) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def palette(arr, k: int = 5, iterations: int = 12) -> list[dict]:
    """Dominant colours by deterministic k-means. Returns [{hex, share}] largest first."""
    _need()
    px = arr.reshape(-1, 3)
    if len(px) > 20000:
        px = px[:: len(px) // 20000]
    order = np.argsort(luminance(px))
    centers = px[order[np.linspace(0, len(px) - 1, k).astype(int)]].copy()
    for _ in range(iterations):
        d = ((px[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        label = d.argmin(1)
        for i in range(k):
            members = px[label == i]
            if len(members):
                centers[i] = members.mean(0)
    label = ((px[:, None, :] - centers[None, :, :]) ** 2).sum(-1).argmin(1)
    shares = np.bincount(label, minlength=k) / len(px)
    rows = [{"hex": to_hex(centers[i]), "share": round(float(shares[i]), 4)} for i in range(k) if shares[i] > 0]
    return sorted(rows, key=lambda r: -r["share"])


def luminance_stats(arr) -> dict:
    _need()
    lum = luminance(arr)
    p5, p50, p95 = (float(x) for x in np.percentile(lum, [5, 50, 95]))
    return {"mean": float(lum.mean()), "std": float(lum.std()), "p5": p5, "p50": p50, "p95": p95,
            "contrast": p95 - p5}


def saturation_mean(arr) -> float:
    _need()
    mx, mn = arr.max(-1), arr.min(-1)
    return float(np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0).mean())


def seam_score(arr) -> float:
    """Tileability: the luminance step across the wrap-around edge, relative to the steps just inside it.

    For a continuous signal the step across the seam equals the steps beside it (ratio ~1.0).
    A hard seam makes the wrap step much larger than its neighbours (ratio >> 1). The denominator
    is floored at a quarter of the image's mean step so flat borders do not blow the ratio up.
    Returns the worse of the horizontal and vertical axes. Values above ~1.5 deserve a look.
    """
    _need()
    lum = luminance(arr)

    def axis_ratio(wrap, near_a, near_b, global_step):
        denom = max((near_a + near_b) / 2, 0.25 * global_step, 1e-4)
        return float(wrap / denom)

    h = axis_ratio(np.abs(lum[:, 0] - lum[:, -1]).mean(), np.abs(lum[:, 1] - lum[:, 0]).mean(),
                   np.abs(lum[:, -1] - lum[:, -2]).mean(), np.abs(np.diff(lum, axis=1)).mean())
    v = axis_ratio(np.abs(lum[0, :] - lum[-1, :]).mean(), np.abs(lum[1, :] - lum[0, :]).mean(),
                   np.abs(lum[-1, :] - lum[-2, :]).mean(), np.abs(np.diff(lum, axis=0)).mean())
    return max(h, v)


def edge_density(arr, threshold: float = 0.08) -> float:
    """Fraction of pixels whose luminance gradient magnitude exceeds ``threshold``."""
    _need()
    lum = luminance(arr)
    gy, gx = np.gradient(lum)
    return float((np.hypot(gx, gy) > threshold).mean())


def detail_frequency(arr) -> float:
    """Share of non-DC spectral energy above a quarter of the sampling rate (0 = smooth, 1 = all fine detail)."""
    _need()
    lum = luminance(arr)
    lum = lum - lum.mean()
    spec = np.abs(np.fft.fftshift(np.fft.fft2(lum))) ** 2
    h, w = spec.shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((yy - h / 2) / h, (xx - w / 2) / w)
    total = spec.sum()
    return float(spec[r > 0.125].sum() / total) if total > 0 else 0.0


def colorfulness(arr) -> float:
    """Hasler-Suesstrunk colourfulness metric on [0,1] RGB (scaled to 0-255 units)."""
    _need()
    r, g, b = (arr[..., i] * 255 for i in range(3))
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))


def _otsu(lum) -> float:
    hist, edges = np.histogram(lum, bins=64, range=(0, 1))
    p = hist / max(hist.sum(), 1)
    omega = np.cumsum(p)
    mu = np.cumsum(p * (edges[:-1] + edges[1:]) / 2)
    mu_t = mu[-1]
    denom = omega * (1 - omega)
    sigma_b = np.where(denom > 1e-9, (mu_t * omega - mu) ** 2 / np.maximum(denom, 1e-9), 0)
    return float((edges[:-1][sigma_b.argmax()] + edges[1:][sigma_b.argmax()]) / 2)


def silhouette_stats(arr, size: int = 96) -> dict:
    """Threshold luminance (Otsu), then report fill and connected-component structure.

    The darker class is treated as the subject only if the border is mostly the lighter class,
    otherwise the lighter class is the subject. Heuristic - useful to flag fragmented silhouettes.
    """
    _need()
    lum = luminance(arr)
    h, w = lum.shape
    ys = np.linspace(0, h - 1, min(size, h)).astype(int)
    xs = np.linspace(0, w - 1, min(size, w)).astype(int)
    small = lum[np.ix_(ys, xs)]
    t = _otsu(small)
    mask = small < t
    border = np.concatenate([mask[0], mask[-1], mask[:, 0], mask[:, -1]])
    if border.mean() > 0.5:  # border is "dark": subject is the light class
        mask = ~mask
    labels = np.zeros(mask.shape, dtype=np.int32)
    sizes = []
    for sy, sx in zip(*np.nonzero(mask)):
        if labels[sy, sx]:
            continue
        n = len(sizes) + 1
        stack, count = [(sy, sx)], 0
        labels[sy, sx] = n
        while stack:
            y, x = stack.pop()
            count += 1
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = n
                    stack.append((ny, nx))
        sizes.append(count)
    total = max(sum(sizes), 1)
    significant = [s for s in sizes if s / mask.size >= 0.005]
    return {"fill": float(mask.mean()), "components": len(significant),
            "largest_share": float(max(sizes) / total) if sizes else 0.0, "threshold": t}


def region_contrast(arr, box: Sequence[float]) -> float:
    """|mean luminance inside box - outside|; box = (x0, y0, x1, y1) in 0-1 image coordinates."""
    _need()
    lum = luminance(arr)
    h, w = lum.shape
    x0, y0, x1, y1 = box
    inside = np.zeros_like(lum, dtype=bool)
    inside[int(y0 * h) : max(int(y1 * h), int(y0 * h) + 1), int(x0 * w) : max(int(x1 * w), int(x0 * w) + 1)] = True
    if inside.all() or not inside.any():
        return 0.0
    return float(abs(lum[inside].mean() - lum[~inside].mean()))


def palette_adherence(arr, target_hex: Sequence[str], k: int = 6) -> float:
    """0-1 similarity between the image's dominant colours and a target palette (Lab chamfer)."""
    found = [p["hex"] for p in palette(arr, k)]
    return palette_similarity(found, list(target_hex))


def measure_all(path: str | Path, target_palette: Sequence[str] = (), max_side: int = 384) -> dict:
    arr = load_rgb(path, max_side)
    out = {**{f"lum_{k}": v for k, v in luminance_stats(arr).items()},
           "saturation": saturation_mean(arr), "seam_score": seam_score(arr), "edge_density": edge_density(arr),
           "detail_frequency": detail_frequency(arr), "colorfulness": colorfulness(arr),
           "palette": palette(arr, 5), "size": [arr.shape[1], arr.shape[0]]}
    if target_palette:
        out["palette_adherence"] = palette_adherence(arr, target_palette)
    return out
