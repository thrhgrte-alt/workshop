"""Tiling: how visible the seam is when a tile is repeated, and whether the tile repeats a pattern inside itself.

Both measurements work on the tile at its FULL resolution (the caller refuses sizes above limits.max_side_exact instead of downscaling, because a
downscale would hide a one-pixel seam).
"""

from __future__ import annotations

import math

import numpy as np

from . import pixels as P

STEP_FLOOR = 0.002  # about half of one 8-bit step: keeps a flat tile from dividing by zero (ratio 0 for a flat border)


def _axis_ratio(t: np.ndarray, axis: int) -> dict:
    """Wrap-around step divided by the steps just inside the two edges. ``t`` is HxWxC in [0,1]; axis 1 = across columns, 0 = across rows."""
    a = np.moveaxis(t, axis, 0)  # (n, ..., C) with the wrapped axis first
    n = a.shape[0]
    if n < 3:
        raise ValueError("a tile needs at least 3 pixels along each axis to measure a seam")
    wrap = float(np.abs(a[0] - a[-1]).mean())
    inside = (float(np.abs(a[1] - a[0]).mean()) + float(np.abs(a[-1] - a[-2]).mean())) / 2
    overall = float(np.abs(np.diff(a, axis=0)).mean())
    denom = max(inside, 0.25 * overall, STEP_FLOOR)
    return {"ratio": wrap / denom, "wrap_step": wrap, "inside_step": inside, "mean_step": overall}


def seam(t: np.ndarray) -> dict:
    """``ratio`` is the worse of the two axes (1.0 = the wrap looks like any other neighbouring pair of pixels)."""
    h = _axis_ratio(t, 1)
    v = _axis_ratio(t, 0)
    worst = "horizontal" if h["ratio"] >= v["ratio"] else "vertical"
    return {"ratio": max(h["ratio"], v["ratio"]), "worst_axis": worst, "horizontal": h, "vertical": v}


def repetition(lum: np.ndarray, min_lag_fraction: float, lobe_cut: float) -> dict:
    """Highest circular autocorrelation outside the main lobe.

    The autocorrelation (FFT of the zero-mean luminance, normalised so lag 0 is 1.0) is symmetric, so lags are folded to the half plane. The MAIN LOBE is
    the neighbourhood of lag 0 over which the ring-averaged autocorrelation is still above ``lobe_cut``: a smooth image is correlated with itself at
    small shifts without repeating anything. Lags inside the lobe, and shorter than ``min_lag_fraction`` of the shorter side, are ignored. ``peak`` is the
    highest value left (1.0 = a lag at which the tile repeats exactly) and ``lag`` its (dx, dy) in pixels; among equal peaks the shortest lag is reported.
    A flat tile has no variance: ``peak`` is None."""
    x = np.asarray(lum, dtype=np.float64)
    h, w = x.shape
    x = x - x.mean()
    var = float((x ** 2).sum())
    if var <= 1e-12:
        return {"peak": None, "lag": None, "flat": True, "lobe_radius_px": None}
    f = np.fft.fft2(x)
    ac = np.fft.ifft2(f * np.conj(f)).real / var
    ly = (np.arange(h) + h // 2) % h - h // 2
    lx = (np.arange(w) + w // 2) % w - w // 2
    gx, gy = np.meshgrid(lx, ly)
    radius = np.hypot(gx, gy)
    r_int = np.floor(radius + 0.5).astype(int)
    ring = np.bincount(r_int.ravel(), weights=ac.ravel()) / np.maximum(np.bincount(r_int.ravel()), 1)
    below = np.nonzero(ring < lobe_cut)[0]
    lobe = int(below[0]) if len(below) else int(r_int.max()) + 1
    floor = max(2, math.ceil(min_lag_fraction * min(h, w)))
    r0 = max(lobe, floor)
    cand = (radius >= r0) & ((gx > 0) | ((gx == 0) & (gy > 0)))
    if not cand.any():
        return {"peak": None, "lag": None, "flat": False, "lobe_radius_px": lobe, "note": "the smooth structure covers the whole tile: no repeat distance to test"}
    vals = ac[cand]
    peak = float(vals.max())
    idx = np.nonzero(cand & (ac >= peak - 1e-9))
    order = sorted(zip(radius[idx].tolist(), np.abs(gy[idx]).tolist(), gx[idx].tolist(), gy[idx].tolist()))
    _, _, dx, dy = order[0]
    return {"peak": peak, "lag": [int(dx), int(dy)], "flat": False, "lobe_radius_px": lobe}
