"""Safe image loading: path confinement, size limits, one deterministic way to turn a file into float arrays.

Nothing here randomises, uploads or caches outside the process. Reference images stay on the local disk they were read from.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ..guide_adapter import scope as S

EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".tif", ".tiff"}
SUPPORTED_MODES = {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "CMYK"}


class ImageRefused(ValueError):
    """The image cannot be read safely or meaningfully; the message says why and what to do."""


@dataclass
class Loaded:
    """An image as float arrays in [0, 1]. ``rgb`` is HxWx3, ``alpha`` HxW or None, ``gray`` is True for single-channel sources."""

    rgb: np.ndarray
    alpha: np.ndarray | None
    gray: bool
    mode: str
    bit_depth: int
    name: str
    sha256: str
    size: tuple[int, int]  # (width, height) of the file

    @property
    def has_alpha(self) -> bool:
        return self.alpha is not None and float(self.alpha.min()) < 0.999

    @property
    def valid(self) -> np.ndarray:
        """Pixels that count for colour and value statistics: opaque enough (alpha >= 0.5), or all pixels when there is no real alpha."""
        if self.has_alpha:
            return self.alpha >= 0.5
        return np.ones(self.rgb.shape[:2], dtype=bool)


def check_path(path: str | Path, roots: list[Path]) -> Path:
    """Resolve ``path`` (following symlinks) and require it to be a readable image file inside one of ``roots``."""
    if path is None or str(path).strip() == "" or "\x00" in str(path):
        raise ImageRefused("an image path is required")
    resolved = S.resolve_inside(path, roots)  # raises PathNotAllowed (a PermissionError) with the allowed roots in the message
    if not resolved.is_file():
        raise ImageRefused(f"'{Path(path).name}' is not a file inside the allowed folders")
    if resolved.suffix.lower() not in EXTENSIONS:
        raise ImageRefused(f"unsupported file type '{resolved.suffix}'. Supported: {', '.join(sorted(EXTENSIONS))}")
    return resolved


def load(path: str | Path, roots: list[Path], *, max_bytes: int, max_pixels: int, max_side: int | None = None) -> Loaded:
    """Read an image. ``max_side`` downscales (area average, never upscales); ``None`` keeps the file's size."""
    p = check_path(path, roots)
    size_bytes = p.stat().st_size
    if size_bytes > max_bytes:
        raise ImageRefused(f"'{p.name}' is {size_bytes} bytes, above the limit {max_bytes} (limits.max_file_bytes)")
    data = p.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    try:
        img = Image.open(_bytes_io(data))
        if img.width * img.height > max_pixels:  # the header is enough to know: refuse before decoding anything
            raise ImageRefused(f"'{p.name}' has {img.width * img.height} pixels, above the limit {max_pixels} (limits.max_pixels)")
        img.load()
    except ImageRefused:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageRefused(f"'{p.name}' is too large to read safely (limits.max_pixels={max_pixels})") from exc
    except Exception as exc:  # truncated, corrupt or not an image
        raise ImageRefused(f"'{p.name}' could not be decoded as an image: {type(exc).__name__}") from exc
    return from_pil(img, p.name, digest, max_pixels=max_pixels, max_side=max_side)


def _bytes_io(data: bytes):
    import io

    return io.BytesIO(data)


def from_pil(img: Image.Image, name: str, digest: str, *, max_pixels: int, max_side: int | None = None) -> Loaded:
    if img.width * img.height > max_pixels:
        raise ImageRefused(f"'{name}' has {img.width * img.height} pixels, above the limit {max_pixels} (limits.max_pixels)")
    img = ImageOps.exif_transpose(img)
    mode = img.mode
    if mode not in SUPPORTED_MODES:
        raise ImageRefused(f"'{name}' has pixel mode {mode}, which is not supported ({', '.join(sorted(SUPPORTED_MODES))})")
    bit_depth = 16 if mode == "I;16" else 8
    gray = mode in ("1", "L", "LA", "I;16")
    alpha = None
    if mode == "I;16":
        lum = np.asarray(img, dtype=np.float64) / 65535.0
        chans = [lum, lum, lum]
    else:
        if mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        elif mode == "1":
            img = img.convert("L")
        elif mode == "CMYK":
            img = img.convert("RGB")
        a = np.asarray(img, dtype=np.float64) / 255.0
        if a.ndim == 2:
            chans = [a, a, a]
        elif a.shape[2] == 2:  # LA
            chans, alpha = [a[..., 0]] * 3, a[..., 1]
        elif a.shape[2] == 4:
            chans, alpha = [a[..., 0], a[..., 1], a[..., 2]], a[..., 3]
        else:
            chans = [a[..., 0], a[..., 1], a[..., 2]]
    rgb = np.stack(chans, axis=-1)
    size = (rgb.shape[1], rgb.shape[0])
    if max_side and max(size) > max_side:
        rgb, alpha = _resize(rgb, alpha, max_side)
    return Loaded(rgb.astype(np.float32), None if alpha is None else alpha.astype(np.float32), gray, mode, bit_depth, name, digest, size)


def target_size(w: int, h: int, max_side: int) -> tuple[int, int]:
    s = max_side / max(w, h)
    return max(1, round(w * s)), max(1, round(h * s))


def resize_plane(plane: np.ndarray, w: int, h: int) -> np.ndarray:
    """Area-average when shrinking, bilinear when enlarging (float plane in, float plane out). Deterministic."""
    src_h, src_w = plane.shape
    if (src_w, src_h) == (w, h):
        return plane
    method = Image.BOX if (w <= src_w and h <= src_h) else Image.BILINEAR
    return np.asarray(Image.fromarray(plane.astype(np.float32), mode="F").resize((w, h), method), dtype=np.float64)


def _resize(rgb: np.ndarray, alpha: np.ndarray | None, max_side: int):
    w, h = target_size(rgb.shape[1], rgb.shape[0], max_side)
    out = np.stack([resize_plane(rgb[..., c], w, h) for c in range(3)], axis=-1)
    return out, None if alpha is None else resize_plane(alpha, w, h)


def resized_to(img: Loaded, w: int, h: int) -> Loaded:
    """The same image resampled to exactly (w, h) (used to put a reference and a candidate on one grid)."""
    rgb = np.stack([resize_plane(img.rgb[..., c].astype(np.float64), w, h) for c in range(3)], axis=-1)
    alpha = None if img.alpha is None else resize_plane(img.alpha.astype(np.float64), w, h)
    return Loaded(rgb.astype(np.float32), None if alpha is None else alpha.astype(np.float32), img.gray, img.mode, img.bit_depth, img.name, img.sha256, img.size)
