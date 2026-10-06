"""Top-down plan renderer (Pillow): rooms, doors, stairs/ramps, spawns, objectives, landmarks, routes, sightline checks.

A plan view is a *diagnostic*, not a review: use the player-height camera views from ``sight.review_views`` and the
route/sightline metrics before judging a layout.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from .graphs import shortest_route
from .sight import Grid, sample_polyline

BG, WALL, FLOOR, TEXT = (22, 24, 30), (200, 205, 215), (52, 56, 66), (230, 232, 238)
TAG_COLOR = {"spawn": (46, 64, 96), "loot": (84, 70, 46), "objective": (88, 80, 40), "hub": (58, 62, 70), "secret": (66, 50, 82)}
TEAM = {"a": (80, 140, 255), "b": (255, 100, 90)}
SCALE = 5


def render(spec: dict, path: str | Path, *, routes: bool = True, landmark_los: bool = True) -> str:
    w, d = spec["bounds"]["width"], spec["bounds"]["depth"]
    img = Image.new("RGB", (int(w * SCALE) + 24, int(d * SCALE) + 24), BG)
    dr = ImageDraw.Draw(img)
    ox = oz = 12

    def P(x, z):
        return (ox + x * SCALE, oz + z * SCALE)

    wall = spec["wall"]
    for r in spec["rooms"]:
        x, z, rw, rd = r["rect"]
        tag = next((t for t in r.get("tags", []) if t in TAG_COLOR), None)
        dr.rectangle([*P(x, z), *P(x + rw, z + rd)], fill=TAG_COLOR.get(tag, FLOOR), outline=WALL, width=max(1, int(wall * SCALE / 2)))
        dr.text((P(x, z)[0] + 6, P(x, z)[1] + 4), f"{r['id']}" + (f"  y={r['floor']:g}" if r["floor"] else ""), fill=TEXT)
    for c in spec["connections"]:
        if "axis" not in c:
            continue
        half = c["width"] / 2
        color = {"door": (120, 220, 140), "opening": (140, 200, 255), "stairs": (255, 200, 90), "ramp": (255, 160, 70)}[c["kind"]]
        if c["axis"] == "z":
            a, b = P(c["line"], c["at"] - half), P(c["line"], c["at"] + half)
        else:
            a, b = P(c["at"] - half, c["line"]), P(c["at"] + half, c["line"])
        dr.line([a, b], fill=color, width=max(3, int(wall * SCALE)))
        if c["kind"] in ("stairs", "ramp"):
            side = c["side_from"] if c["from"] == c["low"] else c["side_to"]
            vx, vz = {"E": (-1, 0), "W": (1, 0), "S": (0, -1), "N": (0, 1)}[side]
            px, pz = (c["line"], c["at"]) if c["axis"] == "z" else (c["at"], c["line"])
            dr.line([P(px, pz), P(px + vx * c["run"], pz + vz * c["run"])], fill=color, width=2)
    if routes and spec["spawns"] and spec["objectives"]:
        for s in spec["spawns"]:
            r = shortest_route(spec, s["id"], spec["objectives"][0]["id"])
            if r:
                dr.line([P(p[0], p[1]) for p in r["polyline"]], fill=TEAM.get(s.get("team"), (180, 255, 180)), width=2)
                if landmark_los and spec["landmarks"]:
                    grid = Grid(spec)
                    for x, z, _ in sample_polyline(r["polyline"], 8):
                        seen = any(grid.los((x, z), tuple(m["at"][:2])) and math.dist((x, z), m["at"][:2]) <= 80 for m in spec["landmarks"])
                        px, pz = P(x, z)
                        dr.ellipse([px - 2, pz - 2, px + 2, pz + 2], fill=(110, 230, 120) if seen else (240, 90, 90))
    for s in spec["spawns"]:
        px, pz = P(*s["at"][:2])
        dr.polygon([(px, pz - 7), (px - 6, pz + 5), (px + 6, pz + 5)], fill=TEAM.get(s.get("team"), (180, 255, 180)), outline=TEXT)
    for o in spec["objectives"]:
        px, pz = P(*o["at"][:2])
        dr.ellipse([px - 7, pz - 7, px + 7, pz + 7], outline=(250, 210, 60), width=3)
    for m in spec["landmarks"]:
        px, pz = P(*m["at"][:2])
        dr.polygon([(px, pz - 6), (px + 6, pz), (px, pz + 6), (px - 6, pz)], fill=(190, 100, 220), outline=TEXT)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return str(out)
