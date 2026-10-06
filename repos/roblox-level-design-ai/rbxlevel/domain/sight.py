"""Plan-view sightlines, landmark visibility, spawn exposure and player-height review cameras.

The level is rasterised into free/solid cells (cell size = wall thickness): room interiors are free, walls are
solid, and door/opening/stair gaps carved through the walls are free. Line of sight is a straight 2-D ray over
free cells at *eye height*. Limits of this approximation (stated, not hidden): floors and ceilings do not occlude,
landmarks and props do not occlude, and there is no vertical field of view. It answers "can a player standing here
see along this line in plan?", which is what layout reviews need, not "what does the screen look like".
"""

from __future__ import annotations

import math

from .graphs import route_distances, shortest_route
from .spec import finding


class Grid:
    def __init__(self, spec: dict):
        self.cell = float(spec["wall"])
        self.nx = int(math.ceil(spec["bounds"]["width"] / self.cell))
        self.nz = int(math.ceil(spec["bounds"]["depth"] / self.cell))
        self.free = [[False] * self.nz for _ in range(self.nx)]
        self.floor = [[0.0] * self.nz for _ in range(self.nx)]
        wall = spec["wall"]
        for r in spec["rooms"]:
            x, z, w, d = r["rect"]
            self._fill(x + wall, z + wall, x + w - wall, z + d - wall, r["floor"])
        for c in spec["connections"]:
            if "axis" not in c:
                continue
            half = c["width"] / 2
            lo, hi = c["at"] - half, c["at"] + half
            line = c["line"]
            if c["axis"] == "z":
                self._fill(line - wall, lo, line + wall, hi, None)
            else:
                self._fill(lo, line - wall, hi, line + wall, None)

    def _fill(self, x0, z0, x1, z1, floor):
        i0, i1 = int(math.floor(x0 / self.cell + 1e-9)), int(math.ceil(x1 / self.cell - 1e-9))
        j0, j1 = int(math.floor(z0 / self.cell + 1e-9)), int(math.ceil(z1 / self.cell - 1e-9))
        for i in range(max(i0, 0), min(i1, self.nx)):
            for j in range(max(j0, 0), min(j1, self.nz)):
                self.free[i][j] = True
                if floor is not None:
                    self.floor[i][j] = floor

    def is_free(self, x: float, z: float) -> bool:
        i, j = int(x // self.cell), int(z // self.cell)
        return 0 <= i < self.nx and 0 <= j < self.nz and self.free[i][j]

    def los(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        dx, dz = b[0] - a[0], b[1] - a[1]
        dist = math.hypot(dx, dz)
        if dist < 1e-9:
            return self.is_free(*a)
        steps = max(1, int(dist / (self.cell / 2)))
        for k in range(steps + 1):
            t = k / steps
            if not self.is_free(a[0] + dx * t, a[1] + dz * t):
                return False
        return True

    def free_distance(self, origin: tuple[float, float], angle: float, max_dist: float = 240.0) -> float:
        step = self.cell / 2
        d = 0.0
        ca, sa = math.cos(angle), math.sin(angle)
        while d < max_dist:
            if not self.is_free(origin[0] + ca * (d + step), origin[1] + sa * (d + step)):
                return d
            d += step
        return max_dist


def room_samples(room: dict, wall: float, n: int = 3) -> list[tuple[float, float]]:
    x, z, w, d = room["rect"]
    xs = [x + wall + (w - 2 * wall) * (i + 0.5) / n for i in range(n)]
    zs = [z + wall + (d - 2 * wall) * (j + 0.5) / n for j in range(n)]
    return [(a, b) for a in xs for b in zs]


def long_sightlines(spec: dict, grid: Grid, rays: int = 16) -> dict:
    out = {}
    for r in spec["rooms"]:
        best, where = 0.0, None
        for p in room_samples(r, spec["wall"]):
            if not grid.is_free(*p):
                continue
            for k in range(rays):
                d = grid.free_distance(p, 2 * math.pi * k / rays)
                if d > best:
                    best, where = d, p
        out[r["id"]] = {"max_free_distance": round(best, 1), "from": [round(c, 1) for c in where] if where else None}
    return out


def sample_polyline(poly: list[list[float]], step: float) -> list[tuple[float, float, float]]:
    """Points every ``step`` studs along a polyline of (x, z, y); always includes both ends."""
    pts = [tuple(poly[0])]
    carry = 0.0
    for a, b in zip(poly, poly[1:]):
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        pos = step - carry
        while pos <= seg + 1e-9:
            t = pos / seg
            pts.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
            pos += step
        carry = (carry + seg) % step
    if math.dist(pts[-1], poly[-1]) > 1e-6:
        pts.append(tuple(poly[-1]))
    return pts


def landmark_visibility(spec: dict, grid: Grid, route: dict, view_range: float, step: float = 8.0) -> dict:
    samples = sample_polyline(route["polyline"], step)
    marks = [(m["id"], tuple(m["at"][:2])) for m in spec["landmarks"]]
    rows = []
    for x, z, y in samples:
        seen = [mid for mid, p in marks if math.dist((x, z), p) <= view_range and grid.los((x, z), p)]
        rows.append({"at": [round(x, 1), round(z, 1)], "visible": seen})
    visible = sum(1 for r in rows if r["visible"])
    longest_blind, run = 0.0, 0.0
    for r in rows:
        run = 0.0 if r["visible"] else run + step
        longest_blind = max(longest_blind, run)
    return {"fraction": round(visible / len(rows), 3) if rows else 0.0, "samples": len(rows),
            "longest_blind_stretch": round(longest_blind, 1), "rows": rows}


def spawn_exposure(spec: dict, grid: Grid, view_range: float) -> list[dict]:
    """For each spawn: which ENEMY spawns it can see (plan-view LOS within ``view_range``)."""
    out = []
    for s in spec["spawns"]:
        exposed = []
        for other in spec["spawns"]:
            if other is s or not s.get("team") or other.get("team") == s.get("team"):
                continue
            p, q = tuple(s["at"][:2]), tuple(other["at"][:2])
            if math.dist(p, q) <= view_range and grid.los(p, q):
                exposed.append(other["id"])
        out.append({"spawn": s["id"], "team": s.get("team"), "exposed_to": exposed})
    return out


def analyze(spec: dict, style: dict) -> dict:
    limits = style.get("limits", {})
    view_range = limits.get("view_range", 80)
    grid = Grid(spec)
    findings = []
    longest = long_sightlines(spec, grid)
    worst = max(longest.items(), key=lambda kv: kv[1]["max_free_distance"]) if longest else (None, {"max_free_distance": 0})
    metrics = {"longest_sightline": worst[1]["max_free_distance"]}
    max_allowed = limits.get("max_sightline", 80)
    for rid, info in longest.items():
        if info["max_free_distance"] > max_allowed:
            findings.append(finding("warning", "long_sightline", f"room '{rid}' has a {info['max_free_distance']}-stud unobstructed sightline (limit {max_allowed}); add cover or stagger doors", rid))
    exposure = spawn_exposure(spec, grid, view_range)
    enemy_visible = [e for e in exposure if e["exposed_to"]]
    metrics["spawn_exposure_count"] = len(enemy_visible)
    for e in enemy_visible:
        findings.append(finding("error", "spawn_exposed", f"spawn '{e['spawn']}' has line of sight to an enemy spawn within {view_range} studs", e["spawn"]))
    routes = {}
    visibility = []
    for s in spec["spawns"]:
        if not spec["objectives"]:
            break
        route = shortest_route(spec, s["id"], spec["objectives"][0]["id"])
        if route and spec["landmarks"]:
            lv = landmark_visibility(spec, grid, route, view_range)
            routes[s["id"]] = lv
            visibility.append(lv["fraction"])
            if lv["longest_blind_stretch"] > limits.get("max_blind_stretch", 48):
                findings.append(finding("warning", "lost_player", f"along the route from '{s['id']}' there is a {lv['longest_blind_stretch']}-stud stretch with no landmark in view", s["id"]))
    if visibility:
        metrics["landmark_visibility"] = round(min(visibility), 3)
    elif not spec["landmarks"]:
        findings.append(finding("warning", "no_landmarks", "level has no landmarks; players have nothing to navigate by"))
    return {"metrics": metrics, "findings": findings, "longest_by_room": longest, "landmark_routes": {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in routes.items()},
            "spawn_exposure": exposure, "limits_note": "plan-view line of sight at eye height; floors, ceilings and props do not occlude"}


# --- review cameras ----------------------------------------------------------------------------------------------------------------
def review_views(spec: dict, step: float = 24.0, max_views: int = 24, ahead: float = 24.0) -> list[dict]:
    """Cameras at player eye height along the main route, plus landmark checks, room corners and a top-down overview."""
    eye = spec["player"]["eye"]
    grid = Grid(spec)
    views: list[dict] = []
    if spec["spawns"] and spec["objectives"]:
        route = shortest_route(spec, spec["spawns"][0]["id"], spec["objectives"][0]["id"])
        if route:
            poly = route["polyline"]
            samples = sample_polyline(poly, step)
            cum, _ = route_distances(route)
            for k, (x, z, y) in enumerate(samples[:-1]):
                nx, nz, ny = samples[k + 1]
                dx, dz = nx - x, nz - z
                norm = math.hypot(dx, dz) or 1.0
                look = (x + dx / norm * ahead, z + dz / norm * ahead)
                views.append({"name": f"route_{k:02d}", "kind": "route", "position": [round(x, 1), round(y + eye, 1), round(z, 1)],
                              "look_at": [round(look[0], 1), round(y + eye, 1), round(look[1], 1)],
                              "note": "player height, walking toward the objective"})
            # the view a player has on arrival at the objective, looking back at where they came from
            ex, ez, ey = samples[-1]
            sx, sz, _ = samples[0]
            views.append({"name": "arrival", "kind": "route", "position": [round(ex, 1), round(ey + eye, 1), round(ez, 1)],
                          "look_at": [round(sx, 1), round(ey + eye, 1), round(sz, 1)], "note": "arrival at the objective, looking back"})
            if spec["landmarks"]:
                for k, (x, z, y) in enumerate(samples[: max(1, len(samples) // 2) + 1:2]):
                    near = min(spec["landmarks"], key=lambda m: math.dist((x, z), m["at"][:2]))
                    views.append({"name": f"landmark_{k:02d}", "kind": "landmark", "position": [round(x, 1), round(y + eye, 1), round(z, 1)],
                                  "look_at": [round(near["at"][0], 1), round(y + near.get("height", 20), 1), round(near["at"][1], 1)],
                                  "visible_in_plan": grid.los((x, z), tuple(near["at"][:2])),
                                  "note": f"can the player see landmark '{near['id']}' from here?"})
    for r in spec["rooms"]:
        x, z, w, d = r["rect"]
        wall = spec["wall"]
        views.append({"name": f"room_{r['id']}", "kind": "room", "position": [round(x + wall + 2, 1), round(r["floor"] + eye, 1), round(z + wall + 2, 1)],
                      "look_at": [round(x + w / 2, 1), round(r["floor"] + eye, 1), round(z + d / 2, 1)], "note": f"room '{r['id']}' from a corner"})
    w, d = spec["bounds"]["width"], spec["bounds"]["depth"]
    views.append({"name": "overview", "kind": "overview", "position": [round(w / 2, 1), round(max(w, d) * 0.95, 1), round(d / 2 + 0.1, 1)],
                  "look_at": [round(w / 2, 1), 0.0, round(d / 2, 1)], "note": "top-down overview (never judge a level from this alone)"})
    route_views = [v for v in views if v["kind"] in ("route",)]
    others = [v for v in views if v["kind"] not in ("route",)]
    keep = max_views - len(others) if max_views > len(others) else max_views // 2
    if len(route_views) > keep:
        idx = [round(i * (len(route_views) - 1) / max(keep - 1, 1)) for i in range(keep)]
        route_views = [route_views[i] for i in sorted(set(idx))]
    return (route_views + others)[:max_views]
