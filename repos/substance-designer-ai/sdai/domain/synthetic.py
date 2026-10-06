"""Deterministic synthetic material maps for tests, evals and the tracked example previews.

These are NOT production textures: they are simple procedural stand-ins so the measurement, rubric and
retrieval code can be tested without Substance Designer. Variants deliberately break one property
(seam, palette, noise, normals) so checks can prove they catch it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

STONE_LIGHT = (0.545, 0.588, 0.639)  # ~#8b96a3
STONE_DARK = (0.169, 0.188, 0.212)  # ~#2b3036


def hex_to_unit(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _periodic_noise(n: int, seed: int, octaves: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    out = np.zeros((n, n))
    for o in range(1, octaves + 1):
        for _ in range(3):
            fx, fy = rng.integers(1, 3 * o + 1, 2)
            out += np.sin(2 * np.pi * (fx * x + fy * y) + rng.uniform(0, 6.28)) / o
    out -= out.min()
    return out / max(out.max(), 1e-9)


def height_tiles(n: int = 128, seed: int = 0, cols: int = 4, rows: int = 6, bevel: float = 0.18,
                 variation: float = 0.12) -> np.ndarray:
    """Blocky brick-like height in [0, 1]; periodic, so it tiles."""
    y, x = np.mgrid[0:n, 0:n] / n
    row = np.floor(y * rows)
    xs = (x * cols + 0.5 * (row % 2)) % 1.0  # staggered rows, periodic
    ys = (y * rows) % 1.0
    edge = np.minimum(np.minimum(xs, 1 - xs) * 2, np.minimum(ys, 1 - ys) * 2)  # 0 at gaps, 1 at centre
    h = np.clip(edge / bevel, 0, 1)
    h = h * (1 - variation) + variation * _periodic_noise(n, seed)
    return np.clip(h, 0, 1)


def color_map(h: np.ndarray, light=STONE_LIGHT, dark=STONE_DARK) -> np.ndarray:
    t = h[..., None]
    return np.asarray(dark) * (1 - t) + np.asarray(light) * t


def normal_map(h: np.ndarray, strength: float = 3.0) -> np.ndarray:
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * strength
    dy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * strength
    n = np.stack([-dx, -dy, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def roughness_map(h: np.ndarray, level: float = 0.8) -> np.ndarray:
    r = level * (0.9 + 0.1 * h)
    return np.stack([r, r, r], -1)


def height_rgb(h: np.ndarray) -> np.ndarray:
    return np.stack([h, h, h], -1)


def _save(arr: np.ndarray, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8)).save(path)
    return str(path)


VARIANTS = ("good", "seam", "wrong_palette", "noisy", "bad_normal", "missing_normal")


def write_material_set(directory: Path, variant: str = "good", seed: int = 0, n: int = 128) -> dict[str, str]:
    """Write baseColor/normal/roughness/height PNGs for a variant; returns usage -> path."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    directory = Path(directory)
    h = height_tiles(n, seed)
    base = color_map(h, hex_to_unit("#22dd66"), hex_to_unit("#ee1188")) if variant == "wrong_palette" else color_map(h)
    if variant == "noisy":
        noise = np.random.default_rng(seed + 1).random((n, n, 1))
        base = np.clip(base * 0.35 + noise * 0.65, 0, 1)
    if variant == "seam":
        base = np.clip(base + np.linspace(0, 0.5, n)[None, :, None], 0, 1)  # horizontal ramp -> hard wrap seam
    normal = normal_map(h)
    if variant == "bad_normal":
        normal = np.full_like(normal, 0.5)  # decodes to the zero vector
    maps = {"baseColor": _save(base, directory / "baseColor.png"),
            "roughness": _save(roughness_map(h), directory / "roughness.png"),
            "height": _save(height_rgb(h), directory / "height.png")}
    if variant != "missing_normal":
        maps["normal"] = _save(normal, directory / "normal.png")
    return maps


def fake_probe(catalog: dict, mutate: dict | None = None) -> dict:
    """A probe result that matches the catalog exactly (optionally with one deliberate difference).

    Used to test ``catalog-diff``. A real probe comes from running the generated script inside Designer.
    """
    probe: dict = {"designer_version": "synthetic-test", "atomic": {}, "library": {}}
    for spec in catalog.values():
        info = {"inputs": [s.id for s in spec.inputs], "params": list(spec.params), "outputs": [s.id for s in spec.outputs]}
        if spec.source == "atomic":
            probe["atomic"][spec.definition] = info
        else:
            probe["library"][spec.label] = info
    if mutate:
        spec = catalog[mutate["node"]]
        bucket = probe["atomic"][spec.definition] if spec.source == "atomic" else probe["library"][spec.label]
        if mutate.get("remove_node"):
            (probe["atomic"] if spec.source == "atomic" else probe["library"]).pop(spec.definition or spec.label)
        else:
            old, new = mutate["rename"]
            bucket[mutate["kind"]] = [new if x == old else x for x in bucket[mutate["kind"]]]
    return probe
