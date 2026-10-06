"""Composition metrics for a dressed scene: density, repetition, colour spread, negative space, focal points, hierarchy.

These are measurements of a layout in plan view with the kit's metadata. They flag typical problems (too crowded, one
prop everywhere, nothing at the focal point, a blocked path) but cannot judge whether a scene is charming: that stays a
manual rubric criterion.
"""

from __future__ import annotations

import colorsys
import math
from collections import Counter

from . import geom, kit as K, scene as SC


def _hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def occupancy(scene: dict, kit: dict, cell: float = 1.0):
    x, z, w, d = SC.region_rect(scene)
    nx, nz = int(math.ceil(w / cell)), int(math.ceil(d / cell))
    grid = [[False] * nz for _ in range(nx)]
    mods = K.index(kit)
    for inst in scene["instances"]:
        m = mods.get(inst["module"])
        if not m or not m["collision"]:
            continue
        poly = SC.poly_of(inst, m)
        xs, zs = [p[0] for p in poly], [p[1] for p in poly]
        for i in range(max(int((min(xs) - x) // cell), 0), min(int((max(xs) - x) // cell) + 1, nx)):
            for j in range(max(int((min(zs) - z) // cell), 0), min(int((max(zs) - z) // cell) + 1, nz)):
                c = [(x + i * cell, z + j * cell), (x + (i + 1) * cell, z + j * cell), (x + (i + 1) * cell, z + (j + 1) * cell), (x + i * cell, z + (j + 1) * cell)]
                if geom.overlap_depth(poly, c) > 1e-6 and geom.overlap_depth(poly, c) > 0.02:
                    grid[i][j] = True
    return grid


def largest_free_rectangle(grid) -> int:
    """Cells in the largest axis-aligned empty rectangle (histogram method)."""
    if not grid:
        return 0
    nx, nz = len(grid), len(grid[0])
    heights = [0] * nz
    best = 0
    for i in range(nx):
        for j in range(nz):
            heights[j] = 0 if grid[i][j] else heights[j] + 1
        stack: list[int] = []
        for j in range(nz + 1):
            cur = heights[j] if j < nz else 0
            start = j
            while stack and heights[stack[-1]] >= cur:
                top = stack.pop()
                left = stack[-1] + 1 if stack else 0
                best = max(best, heights[top] * (j - left))
                start = left
            stack.append(j)
    return best


def measure(scene: dict, kit: dict, baseline_lock: dict | None = None) -> dict:
    mods = K.index(kit)
    insts = [i for i in scene["instances"] if i["module"] in mods]
    x, z, w, d = SC.region_rect(scene)
    area = w * d
    solid = [i for i in insts if mods[i["module"]]["collision"]]
    metrics: dict[str, float] = {"item_count": len(insts)}
    metrics["coverage"] = round(sum(mods[i["module"]]["footprint"][0] * mods[i["module"]]["footprint"][1] * i["scale"] ** 2 for i in solid) / area, 4)
    counts = Counter(i["module"] for i in insts)
    if insts:
        metrics["max_module_share"] = round(max(counts.values()) / len(insts), 3) if len(insts) >= 5 else 0.0
        metrics["module_diversity"] = round(len(counts) / len(insts), 3)
        adj = tot = 0
        for a in range(len(insts)):
            near = [b for b in range(a + 1, len(insts)) if math.dist(insts[a]["at"][:2], insts[b]["at"][:2]) <= 1.5 * (max(mods[insts[a]["module"]]["footprint"]) + max(mods[insts[b]["module"]]["footprint"]))]
            for b in near:
                tot += 1
                adj += insts[a]["module"] == insts[b]["module"]
        metrics["adjacent_same_ratio"] = round(adj / tot, 3) if tot >= 4 else 0.0
    # colour: normalised entropy of hue bins weighted by footprint area
    bins: Counter = Counter()
    for i in insts:
        c = mods[i["module"]].get("color")
        if c:
            r, g, b = _hex_rgb(c)
            h, s, v = colorsys.rgb_to_hsv(r, g, b)
            key = "neutral" if s < 0.15 else int(h * 12) % 12
            bins[key] += mods[i["module"]]["footprint"][0] * mods[i["module"]]["footprint"][1]
    if len(insts) >= 5 and bins:
        tot = sum(bins.values())
        ent = -sum((v / tot) * math.log(v / tot) for v in bins.values())
        metrics["color_balance"] = round(ent / math.log(13), 3)
    grid = occupancy(scene, kit)
    metrics["largest_free_rect_fraction"] = round(largest_free_rectangle(grid) / max(len(grid) * len(grid[0]), 1), 3) if grid else 0.0
    sat = []
    for f_ in scene["focal"]:
        sat.append(any(mods[i["module"]]["priority"] >= f_["min_priority"] and math.dist(i["at"][:2], f_["at"]) <= f_["radius"] for i in insts))
    if sat:
        metrics["focal_satisfied"] = round(sum(sat) / len(sat), 3)
    classes = {mods[i["module"]]["size_class"] for i in solid}
    metrics["size_classes_present"] = len(classes)
    findings = SC.validate(scene, kit)
    metrics["collisions"] = sum(f["code"] == "collision" for f in findings)
    metrics["blocked_paths"] = sum(f["code"] == "blocks_path" for f in findings)
    metrics["blocked_sightlines"] = sum(f["code"] == "blocks_sightline" for f in findings)
    metrics["outside_region"] = sum(f["code"] in ("outside_region", "in_exclusion") for f in findings)
    metrics["unresolved_modules"] = sum(1 for m in {i["module"] for i in insts} if mods[m]["_defaulted"])
    lock_findings = SC.check_locks(scene, baseline_lock) if baseline_lock else []
    metrics["locked_violations"] = len(lock_findings)
    return {"metrics": metrics, "findings": findings + lock_findings, "module_counts": dict(counts)}
