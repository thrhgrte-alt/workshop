"""Generated images with KNOWN properties, for the evals, the examples and the tests. Fully deterministic: any randomness comes from a ``numpy.random.RandomState``
with an explicit seed in the spec. Every generator returns a uint8 array (H, W, 3) or (H, W, 4); ``write`` saves PNGs through Pillow.

A spec is a mapping ``{"gen": <name>, ...parameters}``; ``build(spec)`` returns the array. Colours are [r, g, b] in 0-255. Boxes are [x0, y0, x1, y1), x1/y1 exclusive.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def _canvas(size, color) -> np.ndarray:
    w, h = size
    return np.broadcast_to(np.array(color, dtype=np.uint8), (h, w, len(color))).copy()


def solid(size, color):
    return _canvas(size, color)


def rect(size, bg, fg, box, alpha=False):
    """A filled box on a plain background. With ``alpha`` the background is fully transparent (alpha 0) and the box opaque."""
    if alpha:
        a = _canvas(size, [*bg, 0])
        x0, y0, x1, y1 = box
        a[y0:y1, x0:x1] = [*fg, 255]
        return a
    a = _canvas(size, bg)
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1] = fg
    return a


def rects(size, bg, items):
    a = _canvas(size, bg)
    for it in items:
        x0, y0, x1, y1 = it["box"]
        a[y0:y1, x0:x1] = it["fg"]
    return a


def ellipse(size, bg, fg, box):
    img = Image.new("RGB", tuple(size), tuple(bg))
    ImageDraw.Draw(img).ellipse(list(box), fill=tuple(fg))
    return np.asarray(img)


def vstep(size, left, right, x):
    """Left colour for columns < x, right colour from column x on."""
    a = _canvas(size, left)
    a[:, x:] = right
    return a


def bands(size, colors, axis="x"):
    """Equal bands of the given colours (the size along ``axis`` must divide evenly so every share is exact)."""
    w, h = size
    n = len(colors)
    length = w if axis == "x" else h
    if length % n:
        raise ValueError("size must divide evenly by the number of colours")
    a = _canvas(size, colors[0])
    step = length // n
    for i, c in enumerate(colors):
        if axis == "x":
            a[:, i * step:(i + 1) * step] = c
        else:
            a[i * step:(i + 1) * step, :] = c
    return a


def hgradient(size, vmax=255):
    """Gray, value = round(x * vmax / (w - 1)) so a 256-wide gradient steps by exactly one level per column."""
    w, h = size
    row = np.rint(np.arange(w) * vmax / (w - 1)).astype(np.uint8)
    return np.repeat(np.repeat(row[None, :, None], h, 0), 3, 2)


def vgradient(size, vmax=255):
    """Gray, value = round(y * vmax / (h - 1)): the vertical twin of hgradient."""
    w, h = size
    col = np.rint(np.arange(h) * vmax / (h - 1)).astype(np.uint8)
    return np.repeat(np.repeat(col[:, None, None], w, 1), 3, 2)


def checker(size, cell, a, b):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx // cell + yy // cell) % 2 == 0)[..., None]
    return np.where(m, np.array(a, dtype=np.uint8), np.array(b, dtype=np.uint8)).astype(np.uint8)


def white_noise(size, seed, lo=0, hi=255):
    """Independent random gray per pixel (very noisy: edge density near 1)."""
    rs = np.random.RandomState(seed)
    g = rs.randint(lo, hi + 1, size=(size[1], size[0]), dtype=np.int64).astype(np.uint8)
    return np.repeat(g[..., None], 3, 2)


def _smooth(shape, seed, sigma):
    rs = np.random.RandomState(seed)
    f = np.fft.fft2(rs.rand(*shape))
    fy, fx = np.fft.fftfreq(shape[0])[:, None], np.fft.fftfreq(shape[1])[None, :]
    s = np.fft.ifft2(f * np.exp(-(fx ** 2 + fy ** 2) / (2 * sigma ** 2))).real
    return (s - s.min()) / (s.max() - s.min())


def smooth_tile(size, seed, sigma=0.03, tint=(1.0, 1.0, 1.0)):
    """A tile that wraps seamlessly by construction (circularly filtered noise)."""
    s = np.round(_smooth((size[1], size[0]), seed, sigma) * 255)
    return np.stack([np.clip(s * t, 0, 255) for t in tint], -1).astype(np.uint8)


def smooth_crop(size, seed, sigma=0.03):
    """A crop of a larger smooth image: the same look, but it does NOT wrap (a visible seam when tiled)."""
    big = _smooth((size[1] * 2, size[0] * 2), seed, sigma)[: size[1], : size[0]]
    big = (big - big.min()) / (big.max() - big.min())
    return np.repeat(np.round(big * 255).astype(np.uint8)[..., None], 3, 2)


def lattice(size, period, radius=3):
    """White dots every ``period`` px on black, softened by a circular box blur: an exact repeat at (period, 0) and (0, period)."""
    a = np.zeros((size[1], size[0]))
    a[::period, ::period] = 1.0
    for ax in (0, 1):
        a = sum(np.roll(a, k, ax) for k in range(-radius, radius + 1)) / (2 * radius + 1)
    g = np.rint(a / a.max() * 255).astype(np.uint8)
    return np.repeat(g[..., None], 3, 2)


def gray_noise(size, seed, mean, amp):
    """Uniform noise around ``mean`` (0-1) of half-width ``amp``: std = amp / sqrt(3). Stored as gray 8-bit."""
    rs = np.random.RandomState(seed)
    v = np.clip(mean + (rs.rand(size[1], size[0]) * 2 - 1) * amp, 0, 1)
    return np.repeat(np.rint(v * 255).astype(np.uint8)[..., None], 3, 2)


def normal_noisy(size, seed, tilt=0.3):
    """A valid tangent-space normal map: random small tilts, unit length, z > 0, encoded as (n + 1) / 2 * 255."""
    rs = np.random.RandomState(seed)
    x = (rs.rand(size[1], size[0]) * 2 - 1) * tilt
    y = (rs.rand(size[1], size[0]) * 2 - 1) * tilt
    z = np.sqrt(1.0 - x ** 2 - y ** 2)
    return np.rint((np.stack([x, y, z], -1) + 1) / 2 * 255).astype(np.uint8)


def blob_corner(size, bg, fg, box):
    return rect(size, bg, fg, box)


GENERATORS = {"solid": solid, "rect": rect, "rects": rects, "ellipse": ellipse, "vstep": vstep, "bands": bands, "hgradient": hgradient, "vgradient": vgradient, "checker": checker, "white_noise": white_noise,
              "smooth_tile": smooth_tile, "smooth_crop": smooth_crop, "lattice": lattice, "gray_noise": gray_noise, "normal_noisy": normal_noisy, "blob_corner": blob_corner}


def shifted(base, add):
    """``base`` (another spec) with ``add`` levels added to every channel (clipped): a known uniform brightness change."""
    return np.clip(build(base).astype(np.int64) + add, 0, 255).astype(np.uint8)


def boxed(base, box, color):
    """``base`` (another spec) with a filled box of ``color`` painted on it."""
    a = build(base).copy()
    x0, y0, x1, y1 = box
    a[y0:y1, x0:x1] = color
    return a


GENERATORS["shifted"] = shifted
GENERATORS["boxed"] = boxed


def build(spec: dict) -> np.ndarray:
    spec = dict(spec)
    gen = spec.pop("gen")
    if gen == "garbage":
        raise ValueError("'garbage' is not an image: write() stores raw bytes for it")
    if gen not in GENERATORS:
        raise ValueError(f"unknown generator '{gen}'. Known: {sorted(GENERATORS)}")
    arr = GENERATORS[gen](**spec)
    return arr


def write(spec: dict, path: Path) -> Path:
    if spec.get("gen") == "garbage":  # bytes that are not an image, for refusal evals
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"this is not a png file")
        return Path(path)
    arr = build(spec)
    mode = "RGBA" if arr.shape[-1] == 4 else "RGB"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode).save(path, format="PNG", compress_level=6, optimize=False)
    return Path(path)
