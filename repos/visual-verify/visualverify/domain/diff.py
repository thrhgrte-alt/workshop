"""Before/after: numeric difference plus a side-by-side or difference image. The two images must have the same size (nothing is resampled unless asked)."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import imageio as IO

MODES = ("side_by_side", "difference")


def numeric(a: IO.Loaded, b: IO.Loaded, pixel_threshold: float) -> dict:
    """Differences of ``b`` (after) from ``a`` (before), on [0, 1] RGB. ``changed_fraction`` counts pixels whose largest channel difference is above the threshold."""
    if a.rgb.shape != b.rgb.shape:
        raise ValueError(f"the images have different sizes ({a.size[0]}x{a.size[1]} vs {b.size[0]}x{b.size[1]}): pass resize=true to resample 'after' onto 'before', or crop them to match")
    d = b.rgb - a.rgb  # float32; the reductions below accumulate in float64
    ad = np.abs(d)
    mx = ad.max(-1)
    mse = float((d.astype(np.float64) ** 2).mean())
    changed = mx > pixel_threshold
    out: dict = {"size": [int(a.rgb.shape[1]), int(a.rgb.shape[0])], "mae": float(ad.mean(dtype=np.float64)), "rmse": math.sqrt(mse), "max_abs": float(ad.max()), "mean_signed_by_channel": [float(d[..., c].mean(dtype=np.float64)) for c in range(3)],
                 "psnr_db": None if mse <= 0 else 10 * math.log10(1.0 / mse), "identical": bool(mse == 0.0), "changed_fraction": float(changed.mean()), "changed_px": int(changed.sum())}
    if changed.any():
        ys, xs = np.nonzero(changed)
        out["changed_bbox"] = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    else:
        out["changed_bbox"] = None
    return out


def render(a: IO.Loaded, b: IO.Loaded, mode: str, gain: float = 4.0, gap: int = 4) -> Image.Image:
    """``difference``: |after - before| times ``gain`` as RGB. ``side_by_side``: before | after | amplified difference, separated by ``gap`` px of mid gray."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if gain <= 0 or gain > 64:
        raise ValueError("gain must be in (0, 64]")
    ad = np.abs(b.rgb - a.rgb)
    diff = np.clip(ad * gain, 0, 1)
    to8 = lambda x: np.rint(np.clip(x, 0, 1) * 255).astype(np.uint8)
    if mode == "difference":
        return Image.fromarray(to8(diff), "RGB")
    h, w = a.rgb.shape[:2]
    canvas = np.full((h, w * 3 + gap * 2, 3), 128, dtype=np.uint8)
    canvas[:, :w], canvas[:, w + gap:2 * w + gap], canvas[:, 2 * w + 2 * gap:] = to8(a.rgb), to8(b.rgb), to8(diff)
    return Image.fromarray(canvas, "RGB")


def png_bytes(img: Image.Image) -> bytes:
    """Deterministic PNG (no metadata, fixed compression)."""
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6, optimize=False)
    return buf.getvalue()
