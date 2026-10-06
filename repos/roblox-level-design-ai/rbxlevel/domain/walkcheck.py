"""Independent walkability check on the GENERATED geometry (not on the spec).

The spec analysis says which rooms *should* connect. This module asks the built parts: can a character of the
configured size walk from a spawn to an objective? It rasterises the parts into a 2.5-D heightfield (floors and
steps give the walkable surface; walls and lintels block the character's body) and flood-fills with a maximum
step height. Because it reads the parts the builder produced, it catches builder bugs (a missing door gap, a wall
over a ramp, a step that is too tall) that a graph-only check could not.

Approximation: 1-stud cells, axis-aligned boxes only, no jumping, no character width beyond one cell.
"""

from __future__ import annotations

import math
from collections import deque


def _is_surface(p: dict) -> bool:
    return p["name"] == "floor" or "_step_" in p["name"]


def _is_blocker(p: dict) -> bool:
    return p["collide"] and (p["name"].startswith("wall_") or p["name"].startswith("lintel_") or p["name"] == "ceiling")


def heightfield(parts: list[dict], width: float, depth: float, cell: float = 1.0):
    nx, nz = int(math.ceil(width / cell)), int(math.ceil(depth / cell))
    surface = [[None] * nz for _ in range(nx)]
    blockers: list[list[list[tuple[float, float]]]] = [[[] for _ in range(nz)] for _ in range(nx)]
    for p in parts:
        if p["class"] != "Part" or not (_is_surface(p) or _is_blocker(p)):
            continue
        (sx, sy, sz), (px, py, pz) = p["size"], p["position"]
        i0, i1 = max(int(math.floor((px - sx / 2) / cell + 1e-9)), 0), min(int(math.ceil((px + sx / 2) / cell - 1e-9)), nx)
        j0, j1 = max(int(math.floor((pz - sz / 2) / cell + 1e-9)), 0), min(int(math.ceil((pz + sz / 2) / cell - 1e-9)), nz)
        top, bottom = py + sy / 2, py - sy / 2
        for i in range(i0, i1):
            for j in range(j0, j1):
                if _is_surface(p):
                    if surface[i][j] is None or top > surface[i][j]:
                        surface[i][j] = top
                else:
                    blockers[i][j].append((bottom, top))
    return surface, blockers, cell


def walkable_cells(surface, blockers, body_height: float, tol: float = 0.25):
    nx, nz = len(surface), len(surface[0]) if surface else 0
    ok = [[False] * nz for _ in range(nx)]
    for i in range(nx):
        for j in range(nz):
            s = surface[i][j]
            if s is None:
                continue
            lo, hi = s + tol, s + body_height
            ok[i][j] = not any(b < hi and t > lo for b, t in blockers[i][j])
    return ok


def reachable(parts: list[dict], width: float, depth: float, start: tuple[float, float], *, body_height: float, max_step: float,
              cell: float = 1.0) -> set[tuple[int, int]]:
    surface, blockers, cell = heightfield(parts, width, depth, cell)
    ok = walkable_cells(surface, blockers, body_height)
    si, sj = int(start[0] // cell), int(start[1] // cell)
    if not (0 <= si < len(ok) and 0 <= sj < len(ok[0])) or not ok[si][sj]:
        return set()
    seen = {(si, sj)}
    queue = deque([(si, sj)])
    while queue:
        i, j = queue.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ni, nj = i + di, j + dj
            if 0 <= ni < len(ok) and 0 <= nj < len(ok[0]) and (ni, nj) not in seen and ok[ni][nj]:
                if abs(surface[ni][nj] - surface[i][j]) <= max_step + 1e-9:
                    seen.add((ni, nj))
                    queue.append((ni, nj))
    return seen


def check(spec: dict, parts: list[dict]) -> dict:
    """Can each spawn walk to each objective in the built geometry? Also reports rooms that no spawn can reach."""
    player = spec["player"]
    width, depth = spec["bounds"]["width"], spec["bounds"]["depth"]
    findings, per_spawn = [], []
    reached_rooms: set[str] = set()
    for s in spec["spawns"]:
        cells = reachable(parts, width, depth, (s["at"][0], s["at"][1]), body_height=player["height"], max_step=player["max_step"])
        row = {"spawn": s["id"], "cells_reached": len(cells), "objectives": {}}
        if not cells:
            findings.append({"severity": "error", "code": "spawn_blocked", "message": f"spawn '{s['id']}' stands inside geometry or off the floor", "where": s["id"]})
        for o in spec["objectives"]:
            ok = (int(o["at"][0] // 1.0), int(o["at"][1] // 1.0)) in cells
            row["objectives"][o["id"]] = ok
            if not ok:
                findings.append({"severity": "error", "code": "objective_unreachable_in_geometry",
                                 "message": f"the built geometry does not let a {player['height']}-stud character walk from '{s['id']}' to '{o['id']}'", "where": o["id"]})
        for r in spec["rooms"]:
            cx, cz = r["rect"][0] + r["rect"][2] / 2, r["rect"][1] + r["rect"][3] / 2
            if (int(cx // 1.0), int(cz // 1.0)) in cells:
                reached_rooms.add(r["id"])
        per_spawn.append(row)
    unreachable = sorted({r["id"] for r in spec["rooms"]} - reached_rooms) if spec["spawns"] else []
    for rid in unreachable:
        findings.append({"severity": "warning", "code": "room_unreachable_in_geometry", "message": f"room '{rid}' cannot be walked into from any spawn in the built geometry", "where": rid})
    return {"per_spawn": per_spawn, "unreachable_rooms": unreachable, "findings": findings,
            "ok": not any(f["severity"] == "error" for f in findings)}
