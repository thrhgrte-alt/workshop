"""Connectivity, routes, loops, choke points, pacing and fairness on a normalised level spec.

Everything is exact graph computation over the spec. Distances are walking distances in studs through door
and ramp nodes (plan-view straight lines inside rooms), not navmesh paths. Results describe the *layout*;
whether it is fun is a human judgment recorded separately in the rubric.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict, deque

from .spec import TAGS_SPECIAL, finding


def rooms_by_id(spec: dict) -> dict[str, dict]:
    return {r["id"]: r for r in spec["rooms"]}


def adjacency(spec: dict) -> dict[str, list[tuple[str, dict]]]:
    adj: dict[str, list[tuple[str, dict]]] = {r["id"]: [] for r in spec["rooms"]}
    for c in spec["connections"]:
        if c.get("from") in adj and c.get("to") in adj and "axis" in c:
            adj[c["from"]].append((c["to"], c))
            adj[c["to"]].append((c["from"], c))
    return adj


def components(spec: dict) -> list[list[str]]:
    adj = adjacency(spec)
    seen: set[str] = set()
    out = []
    for rid in adj:
        if rid in seen:
            continue
        comp, queue = [], deque([rid])
        seen.add(rid)
        while queue:
            cur = queue.popleft()
            comp.append(cur)
            for nxt, _ in adj[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        out.append(sorted(comp))
    return out


def cyclomatic_number(spec: dict) -> int:
    """Independent loops: edges - rooms + components (counting each distinct room pair once)."""
    pairs = {frozenset((c["from"], c["to"])) for c in spec["connections"] if "axis" in c and c["from"] != c["to"]}
    return len(pairs) - len(spec["rooms"]) + len(components(spec))


def dead_ends(spec: dict) -> list[str]:
    adj = adjacency(spec)
    out = []
    for r in spec["rooms"]:
        neighbours = {n for n, _ in adj[r["id"]]}
        if len(neighbours) == 1 and not (set(r.get("tags", [])) & TAGS_SPECIAL) and not _has_item(spec, r["id"]):
            out.append(r["id"])
    return out


def _has_item(spec: dict, room_id: str) -> bool:
    return any(i.get("room") == room_id for key in ("spawns", "objectives") for i in spec[key])


def unreachable_rooms(spec: dict) -> list[str]:
    adj = adjacency(spec)
    starts = {s["room"] for s in spec["spawns"] if s.get("room") in adj}
    if not starts:
        return []
    seen, queue = set(starts), deque(starts)
    while queue:
        cur = queue.popleft()
        for nxt, _ in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return sorted(set(adj) - seen)


# --- walking distances --------------------------------------------------------------------------------------------------
def _side_nodes(conn: dict, rooms: dict[str, dict], wall: float) -> dict[str, tuple[float, float, float]]:
    """(x, z, y) of a connection as seen from each room it joins. Ramps/stairs: the foot sits ``run`` studs inside the lower room."""
    px, pz = (conn["line"], conn["at"]) if conn["axis"] == "z" else (conn["at"], conn["line"])
    out = {}
    for rid, side in ((conn["from"], conn["side_from"]), (conn["to"], conn["side_to"])):
        x, z = px, pz
        if conn["kind"] in ("stairs", "ramp") and rid == conn["low"]:
            run = conn["run"]
            dx, dz = {"E": (-1, 0), "W": (1, 0), "S": (0, -1), "N": (0, 1)}[side]
            x, z = px + dx * run, pz + dz * run
        out[rid] = (x, z, rooms[rid]["floor"])
    return out


def build_point_graph(spec: dict, extra_points: list[tuple[str, str, tuple[float, float]]] = ()) -> tuple[dict, dict]:
    """Nodes: (kind, id, room) -> (x, z, y). Edges: within a room by plan distance, across a connection by its length."""
    rooms = rooms_by_id(spec)
    nodes: dict[tuple, tuple[float, float, float]] = {}
    by_room: dict[str, list[tuple]] = defaultdict(list)
    for c in spec["connections"]:
        if "axis" not in c:
            continue
        for rid, pos in _side_nodes(c, rooms, spec["wall"]).items():
            key = ("conn", c["id"], rid)
            nodes[key] = pos
            by_room[rid].append(key)
    for kind, pid, rid, (x, z) in extra_points:
        key = (kind, pid, rid)
        nodes[key] = (x, z, rooms[rid]["floor"])
        by_room[rid].append(key)
    edges: dict[tuple, list[tuple[tuple, float]]] = defaultdict(list)
    for rid, keys in by_room.items():
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                d = math.dist(nodes[a][:2], nodes[b][:2])
                edges[a].append((b, d))
                edges[b].append((a, d))
    for c in spec["connections"]:
        if "axis" not in c:
            continue
        a, b = ("conn", c["id"], c["from"]), ("conn", c["id"], c["to"])
        if c["kind"] in ("stairs", "ramp"):
            cost = math.hypot(c["run"], c["rise"])
        else:
            cost = 0.0
        edges[a].append((b, cost))
        edges[b].append((a, cost))
    return nodes, edges


def _dijkstra(edges: dict, src: tuple, dst: tuple) -> tuple[float, list[tuple]] | None:
    dist = {src: 0.0}
    prev: dict[tuple, tuple] = {}
    heap = [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == dst:
            path = [u]
            while path[-1] in prev:
                path.append(prev[path[-1]])
            return d, path[::-1]
        if d > dist.get(u, math.inf):
            continue
        for v, w in edges.get(u, []):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return None


def item_points(spec: dict) -> list[tuple[str, str, str, tuple[float, float]]]:
    out = []
    for kind, key in (("spawn", "spawns"), ("objective", "objectives")):
        for item in spec[key]:
            if item.get("room"):
                out.append((kind, item["id"], item["room"], (item["at"][0], item["at"][1])))
    return out


def route_between(spec: dict, a: tuple[str, str], b: tuple[str, str]) -> dict | None:
    """Shortest walking route between two placed items, each given as (kind, id): kind is 'spawn' or 'objective'."""
    pts = item_points(spec)
    nodes, edges = build_point_graph(spec, pts)
    src = next((n for n in nodes if n[0] == a[0] and n[1] == a[1]), None)
    dst = next((n for n in nodes if n[0] == b[0] and n[1] == b[1]), None)
    if src is None or dst is None:
        return None
    found = _dijkstra(edges, src, dst)
    if not found:
        return None
    length, path = found
    rooms_seq: list[str] = []
    for n in path:
        if not rooms_seq or rooms_seq[-1] != n[2]:
            rooms_seq.append(n[2])
    return {"length": round(length, 2), "rooms": rooms_seq, "polyline": [[round(c, 2) for c in nodes[n]] for n in path],
            "node_rooms": [n[2] for n in path], "doors": [n[1] for n in path if n[0] == "conn"]}


def shortest_route(spec: dict, spawn_id: str, objective_id: str) -> dict | None:
    return route_between(spec, ("spawn", spawn_id), ("objective", objective_id))


def spawn_separation(spec: dict) -> float | None:
    """Shortest walking distance between spawns of different teams (None when there is no such pair)."""
    best = None
    for i, a in enumerate(spec["spawns"]):
        for b in spec["spawns"][i + 1 :]:
            if a.get("team") and b.get("team") and a["team"] != b["team"]:
                r = route_between(spec, ("spawn", a["id"]), ("spawn", b["id"]))
                if r and (best is None or r["length"] < best):
                    best = r["length"]
    return best


def room_sequences(spec: dict, src_room: str, dst_room: str, max_len: int = 12, limit: int = 3000) -> list[list[str]]:
    adj = adjacency(spec)
    out: list[list[str]] = []

    def dfs(cur: str, path: list[str]):
        if len(out) >= limit or len(path) > max_len:
            return
        if cur == dst_room:
            out.append(list(path))
            return
        for nxt, _ in sorted({(n, c["id"]) for n, c in adj[cur]}):
            if nxt not in path:
                path.append(nxt)
                dfs(nxt, path)
                path.pop()

    dfs(src_room, [src_room])
    return out


def _sequence_cost(spec: dict, seq: list[str], src_pt: tuple[float, float], dst_pt: tuple[float, float]) -> float | None:
    """Cheapest walking cost along a fixed room sequence (parallel doors between two rooms are all tried)."""
    rooms = rooms_by_id(spec)
    options = []
    for a, b in zip(seq, seq[1:]):
        cs = [c for c in spec["connections"] if "axis" in c and {c["from"], c["to"]} == {a, b}]
        if not cs:
            return None
        options.append(cs)
    # dp over (position after crossing)
    frontier = [(0.0, src_pt)]
    for (a, b), cs in zip(zip(seq, seq[1:]), options):
        nxt = []
        for c in cs:
            nodes = _side_nodes(c, rooms, spec["wall"])
            enter, leave = nodes[a], nodes[b]
            cross = math.hypot(c["run"], c["rise"]) if c["kind"] in ("stairs", "ramp") else 0.0
            best = min(cost + math.dist(pos, enter[:2]) for cost, pos in frontier)
            nxt.append((best + cross, leave[:2]))
        frontier = nxt
    return min(cost + math.dist(pos, dst_pt) for cost, pos in frontier)


def alternates(spec: dict, spawn_id: str, objective_id: str, ratio: float = 1.5) -> dict:
    spawn = next(s for s in spec["spawns"] if s["id"] == spawn_id)
    obj = next(o for o in spec["objectives"] if o["id"] == objective_id)
    seqs = room_sequences(spec, spawn["room"], obj["room"])
    scored = []
    for seq in seqs:
        c = _sequence_cost(spec, seq, tuple(spawn["at"][:2]), tuple(obj["at"][:2]))
        if c is not None:
            scored.append((c, seq))
    scored.sort(key=lambda t: (t[0], t[1]))
    if not scored:
        return {"shortest": None, "within_ratio": 0, "routes": []}
    best = scored[0][0]
    within = [(c, s) for c, s in scored if c <= best * ratio + 1e-9]
    return {"shortest": round(best, 2), "within_ratio": len(within), "ratio": ratio,
            "routes": [{"length": round(c, 2), "rooms": s} for c, s in within[:6]]}


def independent_routes(spec: dict, src_room: str, dst_room: str) -> int:
    """Max number of routes that share no intermediate room (vertex-disjoint paths, Menger via max-flow)."""
    if src_room == dst_room:
        return 1
    adj = adjacency(spec)
    cap: dict[tuple, int] = defaultdict(int)
    graph: dict[tuple, set] = defaultdict(set)

    def add(u, v, c):
        cap[(u, v)] += c
        graph[u].add(v)
        graph[v].add(u)

    for r in adj:
        add((r, "in"), (r, "out"), 10**6 if r in (src_room, dst_room) else 1)
    for r, ns in adj.items():
        for n, _ in ns:
            add((r, "out"), (n, "in"), 1)
    s, t = (src_room, "in"), (dst_room, "out")
    flow = 0
    while True:
        parent = {s: None}
        queue = deque([s])
        while queue and t not in parent:
            u = queue.popleft()
            for v in graph[u]:
                if v not in parent and cap[(u, v)] > 0:
                    parent[v] = u
                    queue.append(v)
        if t not in parent:
            return flow
        v = t
        while parent[v] is not None:
            u = parent[v]
            cap[(u, v)] -= 1
            cap[(v, u)] += 1
            v = u
        flow += 1


def chokepoints(spec: dict, src_room: str, dst_room: str) -> list[str]:
    """Rooms (other than the endpoints) whose removal cuts every route between the two rooms."""
    out = []
    adj = adjacency(spec)
    for r in adj:
        if r in (src_room, dst_room):
            continue
        seen, queue = {src_room}, deque([src_room])
        while queue:
            cur = queue.popleft()
            for nxt, _ in adj[cur]:
                if nxt != r and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if dst_room not in seen:
            out.append(r)
    return sorted(out)


# --- combined analysis --------------------------------------------------------------------------------------------------------
def analyze(spec: dict, style: dict) -> dict:
    limits = style.get("limits", {})
    findings: list[dict] = []
    comps = components(spec)
    loops = cyclomatic_number(spec)
    de = dead_ends(spec)
    unreachable = unreachable_rooms(spec)
    if len(comps) > 1:
        findings.append(finding("error", "disconnected", f"layout has {len(comps)} separate parts: {[c for c in comps]}"))
    for rid in unreachable:
        findings.append(finding("error", "unreachable_room", f"room '{rid}' cannot be reached from any spawn", rid))
    for rid in de:
        findings.append(finding("warning", "dead_end", f"room '{rid}' is a dead end with nothing in it; tag it (loot/secret/safe) or connect it", rid))

    team_routes: dict[str, list[dict]] = defaultdict(list)
    per_pair = []
    for s in spec["spawns"]:
        for o in spec["objectives"]:
            r = shortest_route(spec, s["id"], o["id"])
            if r is None:
                findings.append(finding("error", "no_route", f"no route from spawn '{s['id']}' to objective '{o['id']}'", s["id"]))
                continue
            alt = alternates(spec, s["id"], o["id"], limits.get("alt_route_ratio", 1.5))
            indep = independent_routes(spec, s["room"], o["room"])
            choke = chokepoints(spec, s["room"], o["room"])
            row = {"spawn": s["id"], "team": s.get("team"), "objective": o["id"], "length": r["length"], "rooms": r["rooms"],
                   "alternates_within_ratio": alt["within_ratio"], "independent_routes": indep, "chokepoints": choke}
            per_pair.append(row)
            team_routes[s.get("team") or s["id"]].append(row)

    metrics: dict[str, float] = {"rooms": len(spec["rooms"]), "loops": loops, "dead_ends": len(de), "unreachable_rooms": len(unreachable)}
    if per_pair:
        metrics["route_length_max"] = max(p["length"] for p in per_pair)
        metrics["route_length_min"] = min(p["length"] for p in per_pair)
        metrics["alternates_min"] = min(p["alternates_within_ratio"] for p in per_pair)
        metrics["independent_routes_min"] = min(p["independent_routes"] for p in per_pair)
        metrics["chokepoints_max"] = max(len(p["chokepoints"]) for p in per_pair)
    team_best = {t: min(r["length"] for r in rows) for t, rows in team_routes.items()}
    if len(team_best) >= 2:
        metrics["fairness_ratio"] = round(min(team_best.values()) / max(team_best.values()), 3)
    return {"metrics": metrics, "routes": per_pair, "findings": findings, "components": comps, "dead_ends": de,
            "unreachable": unreachable}


def route_distances(route: dict) -> tuple[list[float], dict[str, tuple[float, float]]]:
    """Cumulative walking distance at each polyline node, and the distance span spent inside each room."""
    pts = route["polyline"]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    legs: dict[str, tuple[float, float]] = {}
    for d, room in zip(cum, route["node_rooms"]):
        lo, hi = legs.get(room, (d, d))
        legs[room] = (min(lo, d), max(hi, d))
    return cum, legs


def pacing(spec: dict, style: dict) -> dict:
    """Walk the primary route (first spawn -> first objective) and place encounters along it."""
    if not spec["spawns"] or not spec["objectives"]:
        return {"metrics": {}, "findings": [finding("warning", "no_route_for_pacing", "pacing needs a spawn and an objective")], "timeline": []}
    route = shortest_route(spec, spec["spawns"][0]["id"], spec["objectives"][0]["id"])
    if not route:
        return {"metrics": {}, "findings": [finding("error", "no_route", "no primary route")], "timeline": []}
    cum, legs = route_distances(route)
    total = cum[-1]
    timeline = []
    for e in spec["encounters"]:
        if e["room"] in legs:
            lo, hi = legs[e["room"]]
            timeline.append({"encounter": e["id"], "room": e["room"], "distance": round((lo + hi) / 2, 1), "intensity": e["intensity"]})
    timeline.sort(key=lambda t: t["distance"])
    marks = [0.0] + [t["distance"] for t in timeline] + [total]
    gaps = [b - a for a, b in zip(marks, marks[1:])]
    metrics = {"primary_route_length": round(total, 1), "encounters_on_route": len(timeline),
               "rest_stretch_max": round(max(gaps), 1)}
    findings = []
    if timeline:
        metrics["first_encounter_distance"] = timeline[0]["distance"]
        metrics["peak_intensity"] = max(t["intensity"] for t in timeline)
        peak = max(timeline, key=lambda t: (t["intensity"], t["distance"]))
        metrics["peak_position_fraction"] = round(peak["distance"] / total, 3) if total else 1.0
    else:
        findings.append(finding("info", "no_encounters", "no encounters lie on the primary route"))
    return {"metrics": metrics, "findings": findings, "timeline": timeline, "route_length": round(total, 1),
            "route_rooms": route["rooms"]}
