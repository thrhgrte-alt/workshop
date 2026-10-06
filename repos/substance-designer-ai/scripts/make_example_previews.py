#!/usr/bin/env python3
"""Regenerate the tracked SYNTHETIC example previews (deterministic). Requires numpy + Pillow.

These are stand-ins that make the examples, tests and evals runnable without Designer. They are not
production textures. Put your real assets in your own local library (see README, "Your library").
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from sdai.domain import synthetic as syn  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "examples"


def save(arr, rel):
    syn._save(arr, ROOT / rel)
    print("wrote", rel)


h_stone = syn.height_tiles(128, seed=1, cols=4, rows=6)
save(syn.color_map(h_stone), "positive/stone_wall_chunky.png")

h_wood = syn.height_tiles(128, seed=2, cols=6, rows=1, bevel=0.25)
save(syn.color_map(h_wood, syn.hex_to_unit("#c68642"), syn.hex_to_unit("#4a2f17")), "positive/wood_planks_warm.png")

h_pebble = syn.height_tiles(128, seed=3, cols=9, rows=9, bevel=0.5, variation=0.2)
save(syn.color_map(h_pebble, syn.hex_to_unit("#8a7358"), syn.hex_to_unit("#3a2d20")), "positive/pebble_ground_soft.png")

h_neg = syn.height_tiles(128, seed=4, cols=12, rows=18, bevel=0.1)
noise = np.random.default_rng(5).random((128, 128, 1))
save(np.clip(syn.color_map(h_neg) * 0.35 + noise * 0.65, 0, 1), "negative/stone_wall_too_noisy.png")

save(syn.color_map(h_stone, syn.hex_to_unit("#22dd66"), syn.hex_to_unit("#ee1188")), "negative/stone_wall_neon_palette.png")

seam = np.clip(syn.color_map(h_stone) + np.linspace(0, 0.5, 128)[None, :, None], 0, 1)
save(seam, "negative/stone_wall_visible_seam.png")
