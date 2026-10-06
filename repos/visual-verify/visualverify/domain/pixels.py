"""Pixel metrics. Everything here is a number computed from pixels with NumPy; none of it judges taste.

Lineage: the luminance, k-means palette, gradient edge density and Lab chamfer ideas were extracted from concept-art-ai's analysis module and its vendored
imaging core, then rewritten here with their definitions pinned down (documented in each function and in docs of README "What is measured") so the same input
always gives the same output. No randomness is used anywhere in this package: the k-means starts from luminance quantiles, subsampling is a fixed stride.

Conventions: colours are sRGB-ENCODED floats in [0, 1] (not linear light) unless a function says otherwise; luminance is Rec.709 weights applied to the encoded
values (as most image editors show it), not photometric luminance; image coordinates are pixel indices, x to the right and y down.
"""

from __future__ import annotations

import math

import numpy as np

LUMA = np.array([0.2126, 0.7152, 0.0722])
D65 = np.array([0.95047, 1.0, 1.08883])
_M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])


def luminance(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)  # always float64: the result must not depend on the NumPy version's mixed-precision rules
    return rgb[..., 0] * LUMA[0] + rgb[..., 1] * LUMA[1] + rgb[..., 2] * LUMA[2]


def to_hex(rgb) -> str:
    r, g, b = (int(round(min(max(float(c), 0.0), 1.0) * 255)) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgb(h: str) -> np.ndarray:
    t = str(h).strip().lstrip("#")
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6 or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise ValueError(f"'{h}' is not a hex colour like #a1b2c3")
    return np.array([int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (encoded, 0-1) to CIE L*a*b* under D65. Shape (..., 3) in and out."""
    c = np.asarray(rgb, dtype=np.float64)
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = lin @ _M.T / D65
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def delta_e76(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(srgb_to_lab(a) - srgb_to_lab(b), axis=-1)


def saturation_value(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """HSV saturation and value. S = (max-min)/max (0 where max = 0), V = max."""
    rgb = np.asarray(rgb, dtype=np.float64)
    mx, mn = rgb.max(-1), rgb.min(-1)
    return np.where(mx > 1e-9, (mx - mn) / np.maximum(mx, 1e-9), 0.0), mx


# --- palette -------------------------------------------------------------------------------------------------------------------------------
def dominant_colors(pixels: np.ndarray, k: int = 5, iterations: int = 12, max_samples: int = 20000) -> list[dict]:
    """Deterministic k-means in sRGB. ``pixels`` is (N, 3). Initial centres are pixels at evenly spaced luminance quantiles; samples are taken at a fixed stride.

    Returns ``[{hex, share, rgb}]`` largest share first (ties broken by hex), empty clusters dropped. Shares are over the sampled pixels."""
    px = np.asarray(pixels, dtype=np.float64).reshape(-1, 3)
    if len(px) == 0:
        return []
    if len(px) > max_samples:
        px = px[:: math.ceil(len(px) / max_samples)]
    order = np.argsort(luminance(px), kind="stable")
    centers = px[order[np.linspace(0, len(px) - 1, k).round().astype(int)]].copy()
    for _ in range(iterations):
        label = _assign(px, centers)
        for i in range(k):
            members = px[label == i]
            if len(members):
                centers[i] = members.mean(0)
    label = _assign(px, centers)
    shares = np.bincount(label, minlength=k) / len(px)
    rows = [{"hex": to_hex(centers[i]), "share": round(float(shares[i]), 4), "rgb": [float(v) for v in centers[i]]} for i in range(k) if shares[i] > 0]
    merged: dict[str, dict] = {}
    for r in rows:  # two centres can round to the same hex: report one colour
        if r["hex"] in merged:
            merged[r["hex"]]["share"] = round(merged[r["hex"]]["share"] + r["share"], 4)
        else:
            merged[r["hex"]] = dict(r)
    return sorted(merged.values(), key=lambda r: (-r["share"], r["hex"]))


def _assign(px: np.ndarray, centers: np.ndarray) -> np.ndarray:
    d = ((px[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
    return d.argmin(1)


def palette_distance(dominant: list[dict], target_hex: list[str], target_weights: list[float] | None = None) -> dict:
    """Symmetric Lab chamfer between the image's dominant colours and a target palette (CIE76 delta-E).

    ``image_to_target``: share-weighted mean over image colours of the delta-E to the nearest target colour (image colours the palette does not contain).
    ``target_to_image``: weighted mean over target colours of the delta-E to the nearest dominant colour (target colours the image lacks).
    ``distance`` is their mean. 0 = same colours; roughly 2.3 is a just-noticeable difference for delta-E76; 100 is the span of L*."""
    if not dominant or not target_hex:
        raise ValueError("palette distance needs at least one image colour and one target colour")
    img = np.array([hex_to_rgb(d["hex"]) if "rgb" not in d else d["rgb"] for d in dominant], dtype=np.float64)
    shares = np.array([d["share"] for d in dominant], dtype=np.float64)
    shares = shares / shares.sum()
    tgt = np.array([hex_to_rgb(h) for h in target_hex])
    w = np.array(target_weights if target_weights else [1.0] * len(target_hex), dtype=np.float64)
    if len(w) != len(tgt) or (w < 0).any() or w.sum() <= 0:
        raise ValueError("target_weights must be non-negative, not all zero, one per target colour")
    w = w / w.sum()
    li, lt = srgb_to_lab(img), srgb_to_lab(tgt)
    d = np.linalg.norm(li[:, None, :] - lt[None, :, :], axis=-1)  # (image, target)
    a, b = float((shares * d.min(1)).sum()), float((w * d.min(0)).sum())
    near = [{"image": dominant[i]["hex"], "target": target_hex[int(d[i].argmin())], "delta_e": round(float(d[i].min()), 2)} for i in range(len(dominant))]
    missing = [{"target": target_hex[j], "nearest_image": dominant[int(d[:, j].argmin())]["hex"], "delta_e": round(float(d[:, j].min()), 2)} for j in range(len(tgt))]
    return {"distance": (a + b) / 2, "image_to_target": a, "target_to_image": b, "pairs": near, "targets": missing}


def sat_value_stats(rgb: np.ndarray, valid: np.ndarray) -> dict:
    s, v = saturation_value(rgb[valid])
    return {"saturation": _dist(s), "value": _dist(v)}


def _dist(x: np.ndarray) -> dict:
    p5, p50, p95 = (float(q) for q in np.percentile(x, [5, 50, 95]))
    return {"mean": float(x.mean()), "p5": p5, "p50": p50, "p95": p95, "min": float(x.min()), "max": float(x.max())}


# --- value structure -----------------------------------------------------------------------------------------------------------------------
def value_structure(lum: np.ndarray, dark_cut: float, light_cut: float, bins: int = 8) -> dict:
    """Histogram, spread and level maps of a luminance array (any shape; values 0-1).

    ``contrast`` = 95th minus 5th percentile. ``levels3`` shares are dark (< dark_cut), mid, light (> light_cut). ``levels5`` uses equal 0.2 steps."""
    x = np.asarray(lum, dtype=np.float64).ravel()
    n = len(x)
    hist = np.histogram(x, bins=bins, range=(0.0, 1.0))[0] / n
    p5, p50, p95 = (float(q) for q in np.percentile(x, [5, 50, 95]))
    l3 = {"dark": float((x < dark_cut).mean()), "mid": float(((x >= dark_cut) & (x <= light_cut)).mean()), "light": float((x > light_cut).mean())}
    l5 = np.histogram(np.minimum(x, 1.0 - 1e-12), bins=5, range=(0.0, 1.0))[0] / n
    return {"mean": float(x.mean()), "std": float(x.std()), "p5": p5, "p50": p50, "p95": p95, "contrast": p95 - p5,
            "histogram": [round(float(h), 4) for h in hist], "levels3": l3, "levels5": [round(float(v), 4) for v in l5]}


def histogram_emd(a: np.ndarray, b: np.ndarray, bins: int = 32) -> float:
    """Earth-mover distance between two luminance distributions: sum of |CDF difference| times the bin width (so 1.0 would be black vs white)."""
    ha = np.histogram(np.asarray(a).ravel(), bins=bins, range=(0.0, 1.0))[0].astype(np.float64)
    hb = np.histogram(np.asarray(b).ravel(), bins=bins, range=(0.0, 1.0))[0].astype(np.float64)
    ca, cb = np.cumsum(ha / ha.sum()), np.cumsum(hb / hb.sum())
    return float(np.abs(ca - cb).sum() / bins)


# --- edges ---------------------------------------------------------------------------------------------------------------------------------
def edge_map(lum: np.ndarray, threshold: float) -> np.ndarray:
    """Boolean map of pixels whose luminance gradient magnitude (np.gradient, central differences) is above ``threshold``."""
    gy, gx = np.gradient(np.asarray(lum, dtype=np.float64))
    return np.hypot(gx, gy) > threshold


def edge_stats(lum: np.ndarray, threshold: float, grid: int = 4) -> dict:
    e = edge_map(lum, threshold)
    h, w = e.shape
    ys = [round(i * h / grid) for i in range(grid + 1)]
    xs = [round(i * w / grid) for i in range(grid + 1)]
    cells = np.array([[e[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].sum() for c in range(grid)] for r in range(grid)], dtype=np.float64)
    total = float(cells.sum())
    if total <= 0:
        entropy = None
    else:
        p = cells.ravel() / total
        p = p[p > 0]
        entropy = float(-(p * np.log(p)).sum() / math.log(grid * grid))
    dens = [[round(float(cells[r, c] / max(1, (ys[r + 1] - ys[r]) * (xs[c + 1] - xs[c]))), 4) for c in range(grid)] for r in range(grid)]
    return {"density": float(e.mean()), "distribution": entropy, "grid_density": dens, "edge_pixels": int(total)}


# --- connected components (4-connectivity, run based so it stays fast in pure NumPy/Python) ---------------------------------------------------
def components(mask: np.ndarray) -> list[int]:
    """Sizes (pixels) of the 4-connected components of a boolean mask, largest first. Union-find over horizontal runs."""
    m = np.asarray(mask, dtype=bool)
    parent: list[int] = []
    sizes: list[int] = []

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev: list[tuple[int, int, int]] = []  # (x0, x1, id) of the previous row's runs, x1 exclusive
    for y in range(m.shape[0]):
        row = m[y]
        d = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
        starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        cur: list[tuple[int, int, int]] = []
        j = 0
        for s, e in zip(starts.tolist(), ends.tolist()):
            rid = len(parent)
            parent.append(rid)
            sizes.append(e - s)
            while j < len(prev) and prev[j][1] <= s:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < e:
                a, b = find(rid), find(prev[k][2])
                if a != b:
                    parent[b] = a
                    sizes[a] += sizes[b]
                k += 1
            cur.append((s, e, rid))
        prev = cur
    return sorted((sizes[i] for i in range(len(parent)) if find(i) == i), reverse=True)
