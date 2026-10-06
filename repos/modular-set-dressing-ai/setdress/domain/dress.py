"""Placement: single modules, socket snapping, and seeded, rule-driven dressing of a region.

Everything is deterministic for a given (scene, kit, rules, seed). ``dress_region`` returns a *plan* (adds) with an
explanation per placement and counts of why candidates were rejected; it never edits the scene. Different seeds give
controlled variations that all respect the same constraints (region, exclusions, paths, sightlines, locked instances, scale).
"""

from __future__ import annotations

import math
import random

from . import geom, kit as K, scene as SC


def make_instance(scene: dict, kit: dict, module_id: str, at, *, yaw: float = 0.0, scale: float = 1.0, y: float | None = None,
                  iid: str | None = None, snap: bool = True, why: str = "") -> dict:
    K.get(kit, module_id)
    step, ystep = scene["grid"]["step"], scene["grid"]["yaw_step"]
    ax, az = (geom.snap(at[0], step), geom.snap(at[1], step)) if snap else (float(at[0]), float(at[1]))
    inst = {"id": iid or next_id(scene, module_id), "module": module_id, "at": [ax, az], "y": scene["region"]["floor_y"] if y is None else float(y),
            "yaw": geom.snap_yaw(yaw, ystep) if snap else geom.norm_yaw(yaw), "scale": float(scale), "locked": False, "source": "ai", "why": why}
    return inst


def next_id(scene: dict, prefix: str, taken: set[str] | None = None) -> str:
    used = {i["id"] for i in scene["instances"]} | (taken or set())
    base = "".join(c if c.isalnum() or c == "_" else "_" for c in prefix)
    n = 1
    while f"{base}_{n:03d}" in used:
        n += 1
    return f"{base}_{n:03d}"


def socket_world(inst: dict, module: dict, name: str) -> dict:
    s = next((s for s in module["sockets"] if s["name"] == name), None)
    if s is None:
        raise ValueError(f"module '{module['id']}' has no socket '{name}'. Sockets: {[x['name'] for x in module['sockets']]}")
    x, z = geom.to_world((s["pos"][0], s["pos"][2]), tuple(inst["at"][:2]), inst["yaw"], inst["scale"])
    base_y = inst["y"] + module["y_offset"] * inst["scale"]
    return {"pos": (x, z), "y": base_y + s["pos"][1] * inst["scale"], "yaw": geom.norm_yaw(s.get("yaw", 0) + inst["yaw"]), "type": s["type"],
            "mates": s.get("mates", [s["type"]]), "orient": s.get("orient", "same" if s["type"] == "surface" else "opposed")}


def snap_to_socket(scene: dict, kit: dict, target_id: str, target_socket: str, module_id: str, module_socket: str, *, scale: float = 1.0,
                   iid: str | None = None, why: str = "") -> dict:
    """The instance of ``module_id`` whose ``module_socket`` coincides with the target's socket, facing it (or aligned, for 'same' sockets)."""
    mods = K.index(kit)
    target = next((i for i in scene["instances"] if i["id"] == target_id), None)
    if target is None:
        raise ValueError(f"unknown target instance '{target_id}'")
    tw = socket_world(target, mods[target["module"]], target_socket)
    m = K.get(kit, module_id)
    ms = next((s for s in m["sockets"] if s["name"] == module_socket), None)
    if ms is None:
        raise ValueError(f"module '{module_id}' has no socket '{module_socket}'. Sockets: {[x['name'] for x in m['sockets']]}")
    mates = ms.get("mates", [ms["type"]])
    if tw["type"] not in mates or ms["type"] not in tw["mates"]:
        raise ValueError(f"sockets are incompatible: target '{target_socket}' is {tw['type']} (mates {tw['mates']}), "
                         f"'{module_id}.{module_socket}' is {ms['type']} (mates {mates})")
    orient = ms.get("orient", "same" if ms["type"] == "surface" else "opposed")
    yaw = tw["yaw"] - ms.get("yaw", 0) + (180.0 if orient == "opposed" else 0.0)
    if orient == "same":
        yaw = target["yaw"]
    local = (ms["pos"][0], ms["pos"][2])
    off = geom.rot(local[0] * scale, local[1] * scale, yaw)
    at = (tw["pos"][0] - off[0], tw["pos"][1] - off[1])
    base_y = tw["y"] - ms["pos"][1] * scale
    return {"id": iid or next_id(scene, module_id), "module": module_id, "at": [at[0], at[1]], "y": base_y - m["y_offset"] * scale,
            "yaw": geom.norm_yaw(yaw), "scale": float(scale), "locked": False, "source": "ai",
            "why": why or f"snapped {module_id}.{module_socket} to {target_id}.{target_socket}"}


# --- region dressing ----------------------------------------------------------------------------------------------------------------------
def _eligible(kit: dict, rules: dict) -> list[dict]:
    inc_ids, inc_tags, exc_tags = set(rules.get("include_modules", [])), set(rules.get("include_tags", [])), set(rules.get("exclude_tags", []))
    out = []
    for m in kit["modules"]:
        if inc_ids and m["id"] not in inc_ids:
            continue
        if inc_tags and not (inc_tags & set(m["tags"])):
            continue
        if exc_tags & set(m["tags"]):
            continue
        if not m["collision"] and not rules.get("allow_decor", False):
            continue
        out.append(m)
    return out


def _yaw_for(scene: dict, pos, rng: random.Random, orient: str, rules: dict) -> float:
    if orient == "fixed":
        return float(rules.get("yaw", 0.0))
    if orient == "face_focal" and scene["focal"]:
        f = min(scene["focal"], key=lambda f: math.dist(pos, f["at"]))
        return math.degrees(math.atan2(f["at"][0] - pos[0], f["at"][1] - pos[1]))
    if orient == "face_wall":
        x, z, w, d = SC.region_rect(scene)
        dist = {"W": pos[0] - x, "E": x + w - pos[0], "N": pos[1] - z, "S": z + d - pos[1]}
        side = min(dist, key=dist.get)
        if dist[side] <= rules.get("wall_distance", 6.0):
            return {"W": 90.0, "E": -90.0, "N": 0.0, "S": 180.0}[side]  # back to the wall, facing into the room
    return rng.choice([0.0, 90.0, 180.0, -90.0])


def dress_region(scene: dict, kit: dict, rules: dict | None = None, seed: int = 0) -> dict:
    rules = rules or {}
    rng = random.Random(seed)
    mods = K.index(kit)
    cands = _eligible(kit, rules)
    if not cands:
        raise ValueError("no module in the kit matches the rules (include_modules/include_tags/exclude_tags); use search_kit to see what is available")
    x, z, w, d = SC.region_rect(scene)
    area = w * d
    target = float(rules.get("density", 0.15))
    spacing = float(rules.get("spacing", 1.0))
    max_items = int(rules.get("max_items", 60))
    attempts_left = int(rules.get("max_attempts", 600))
    weights = rules.get("weights", {})
    max_share = float(rules.get("max_share", 0.4))
    working = SC.normalize(scene)
    added: list[dict] = []
    uses: dict[str, int] = {}
    rejected: dict[str, int] = {}
    covered = sum(mods[i["module"]]["footprint"][0] * mods[i["module"]]["footprint"][1] * i["scale"] ** 2 for i in working["instances"] if i["module"] in mods) / area
    anchors = []
    if rules.get("cluster"):
        c = rules["cluster"]
        anchors = [(rng.uniform(x, x + w), rng.uniform(z, z + d)) for _ in range(int(c.get("count", 3)))]
    explain: list[dict] = []
    last = None

    def pick() -> dict:
        ws = []
        for m in cands:
            wgt = 1.0
            for t in m["tags"]:
                wgt *= weights.get(t, 1.0)
            wgt *= weights.get(m["id"], 1.0) / (1 + uses.get(m["id"], 0))
            n = len(added)
            if n >= 5 and uses.get(m["id"], 0) / n >= max_share:
                wgt = 0.0
            if last == m["id"] and m.get("max_repeat") is not None and _run(added, m["id"]) >= m["max_repeat"]:
                wgt = 0.0
            ws.append(wgt)
        if not any(ws):
            ws = [1.0] * len(cands)
        return rng.choices(cands, weights=ws, k=1)[0]

    def attempt(m: dict, pos, yaw: float, why: str, rule: str) -> bool:
        sc = rules.get("scale") or 1.0
        sc = min(max(sc, m["scale"]["min"]), m["scale"]["max"])
        inst = make_instance(working, kit, m["id"], pos, yaw=yaw, scale=sc, iid=next_id(working, m["id"], {a["id"] for a in added}), why=why)
        errs = [f for f in SC.placement_findings({**working, "instances": working["instances"] + added}, kit, inst, working["instances"] + added, spacing=spacing)
                if f["severity"] == "error"]
        if errs:
            for e in errs:
                rejected[e["code"]] = rejected.get(e["code"], 0) + 1
            return False
        added.append(inst)
        uses[m["id"]] = uses.get(m["id"], 0) + 1
        explain.append({"id": inst["id"], "module": m["id"], "rule": rule, "orient": rules.get("orient", "random90"), "at": inst["at"], "yaw": inst["yaw"], "why": why})
        return True

    orient = rules.get("orient", "random90")
    while covered < target and len(added) < max_items and attempts_left > 0:
        attempts_left -= 1
        m = pick()
        if anchors:
            ax, az = rng.choice(anchors)
            r = float(rules["cluster"].get("radius", 10.0))
            pos = (ax + rng.uniform(-r, r), az + rng.uniform(-r, r))
        else:
            pos = (rng.uniform(x, x + w), rng.uniform(z, z + d))
        yaw = _yaw_for(scene, pos, rng, orient, rules)
        if attempt(m, pos, yaw, f"scatter ({orient})", "scatter"):
            last = m["id"]
            covered += m["footprint"][0] * m["footprint"][1] / area

    # focal hierarchy: every focal point needs a high-priority module nearby
    if rules.get("focal_first", True):
        for f_ in working["focal"]:
            near = [i for i in working["instances"] + added
                    if i["module"] in mods and mods[i["module"]]["priority"] >= f_["min_priority"] and math.dist(i["at"][:2], f_["at"]) <= f_["radius"]]
            if near:
                continue
            big = sorted([m for m in K.index(kit).values() if m["priority"] >= f_["min_priority"] and m["collision"]], key=lambda m: (-m["priority"], m["id"]))
            placed = False
            for m in big:
                for k in range(48):
                    ang = 2 * math.pi * k / 48 + rng.uniform(0, 0.2)
                    r = max(m["footprint"]) * 0.9 + 1.0 + (k // 16) * 2.0
                    pos = (f_["at"][0] + r * math.cos(ang), f_["at"][1] + r * math.sin(ang))
                    yaw = math.degrees(math.atan2(f_["at"][0] - pos[0], f_["at"][1] - pos[1]))
                    if attempt(m, pos, yaw, f"focal point '{f_['id']}' needed a priority>={f_['min_priority']} module", "focal"):
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                rejected["focal_unplaced"] = rejected.get("focal_unplaced", 0) + 1
    return {"plan": {"adds": added, "moves": [], "removes": []}, "explain": explain, "rejected": rejected, "coverage_after": round(
        sum(mods[i["module"]]["footprint"][0] * mods[i["module"]]["footprint"][1] * i["scale"] ** 2 for i in working["instances"] + added if i["module"] in mods) / area, 4),
        "attempts_used": int(rules.get("max_attempts", 600)) - attempts_left, "seed": seed}


def _run(added: list[dict], module_id: str) -> int:
    n = 0
    for i in reversed(added):
        if i["module"] != module_id:
            break
        n += 1
    return n
