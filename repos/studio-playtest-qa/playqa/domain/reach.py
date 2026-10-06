"""Reachability as a flood fill over pathfinding results.

Studio's pathfinder answers ``spawn -> area`` (and, in the hop mode, ``area -> area``) with a status, a waypoint count and the distance from the last waypoint to the
target. This module takes those results as DATA and flood-fills from the spawn over the usable edges, so an area that only a hop through another area reaches still counts.
(The idea is the flood fill of roblox-level-design-ai's walkcheck, which rasterises generated geometry; here there is no geometry, only Studio's own answers. That
repository's code is not imported: this one works on its own result schema.)

An edge is USABLE when its status is ``Success`` and it has at least ``min_waypoints`` waypoints. An area counts as reached when a usable chain from the spawn leads to it.
Whether the last edge ends close enough to the target (``end_gap <= max_end_gap``) is a separate finding, so "reached but stops short" is not confused with "unreachable".
"""

from __future__ import annotations

from collections import deque
from typing import Any

SPAWN = "spawn"


def usable(edge: dict, min_waypoints: int) -> tuple[bool, str]:
    st = edge.get("status")
    wp = edge.get("waypoints") or 0
    if st != "Success":
        return False, f"status {st}"
    if wp < min_waypoints:
        return False, f"Success with {wp} waypoint(s), fewer than {min_waypoints}"
    return True, "usable"


def flood(areas: list[str], edges: list[dict], *, min_waypoints: int) -> dict[str, Any]:
    """``{"reached": {area: {"parent": node, "edge": edge, "hops": n}}, "unreached": [areas], "attempts": {area: [edges that point at it]}}``."""
    out_edges: dict[str, list[dict]] = {}
    attempts: dict[str, list[dict]] = {}
    for e in edges:
        out_edges.setdefault(e["from"], []).append(e)
        attempts.setdefault(e["to"], []).append(e)
    reached: dict[str, dict] = {SPAWN: {"parent": None, "edge": None, "hops": 0}}
    queue = deque([SPAWN])
    while queue:
        node = queue.popleft()
        for e in out_edges.get(node, []):
            ok, _ = usable(e, min_waypoints)
            if ok and e["to"] not in reached:
                reached[e["to"]] = {"parent": node, "edge": e, "hops": reached[node]["hops"] + 1}
                queue.append(e["to"])
    return {"reached": {a: reached[a] for a in areas if a in reached}, "unreached": [a for a in areas if a not in reached],
            "attempts": {a: attempts.get(a, []) for a in areas}}


def route(reached: dict[str, dict], area: str) -> list[str]:
    """The chain of node names from the spawn to ``area`` (inclusive), following BFS parents."""
    chain, node = [], area
    while node is not None:
        chain.append(node)
        node = reached[node]["parent"] if node in reached else None
    return list(reversed(chain))
