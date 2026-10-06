"""Scenes: placed module instances plus the constraints they must respect.

A scene holds a dressing ``region``, ``exclusions``, functional ``paths`` (clearance corridors), ``corridors`` (sightlines
that must stay open at eye height), ``focal`` points and the ``instances`` placed so far. Every change is expressed as a
*plan* (adds, moves, removes) that can be reviewed, applied, and inverted, so scene construction stays deterministic and
reversible. Locked instances can never be moved or removed by a plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

from . import geom, kit as K

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
EYE_HEIGHT = 4.5
PATH_HEIGHT = 5.0  # a character blocks anything lower than this on a path
Finding = dict


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


def normalize(scene: dict) -> dict:
    s = copy.deepcopy(scene)
    s.setdefault("units", "studs")
    s.setdefault("grid", {})
    s["grid"].setdefault("step", 1.0)
    s["grid"].setdefault("yaw_step", 90.0)
    s.setdefault("exclusions", [])
    s.setdefault("paths", [])
    s.setdefault("corridors", [])
    s.setdefault("focal", [])
    s.setdefault("instances", [])
    s.setdefault("locked", {})
    region = s.get("region", {})
    region.setdefault("floor_y", 0.0)
    s["region"] = region
    for p in s["paths"]:
        p.setdefault("width", 6.0)
    for c in s["corridors"]:
        c.setdefault("width", 3.0)
        c.setdefault("eye_height", EYE_HEIGHT)
    for f_ in s["focal"]:
        f_.setdefault("radius", 8.0)
        f_.setdefault("min_priority", 3)
    for i in s["instances"]:
        i.setdefault("y", region["floor_y"])
        i.setdefault("yaw", 0.0)
        i.setdefault("scale", 1.0)
        i.setdefault("locked", False)
        i.setdefault("why", "")
    return s


def poly_of(inst: dict, module: dict) -> list[tuple[float, float]]:
    return geom.footprint_corners(tuple(inst["at"][:2]), inst["yaw"], tuple(module["footprint"]), inst["scale"], tuple(module["footprint_offset"]))


def y_range(inst: dict, module: dict) -> tuple[float, float]:
    base = inst["y"] + module["y_offset"] * inst["scale"]
    return base, base + module["height"] * inst["scale"]


def region_rect(scene: dict) -> tuple[float, float, float, float]:
    r = scene["region"]
    return r["x"], r["z"], r["w"], r["d"]


def validate_structure(scene: dict, kit: dict) -> list[Finding]:
    f: list[Finding] = []
    r = scene.get("region", {})
    if not all(k in r for k in ("x", "z", "w", "d")) or r.get("w", 0) <= 0 or r.get("d", 0) <= 0:
        f.append(finding("error", "bad_region", "scene needs region {x, z, w, d} with positive size"))
    if not ID_RE.match(str(scene.get("id", ""))):
        f.append(finding("error", "bad_id", f"scene id '{scene.get('id')}' must match {ID_RE.pattern}"))
    mods = K.index(kit)
    seen = set()
    for i in scene["instances"]:
        iid = str(i.get("id"))
        if not ID_RE.match(iid):
            f.append(finding("error", "bad_id", f"instance id '{iid}' must match {ID_RE.pattern}", iid))
        if iid in seen:
            f.append(finding("error", "duplicate_id", f"instance id '{iid}' used twice", iid))
        seen.add(iid)
        m = mods.get(i.get("module"))
        if m is None:
            try:
                K.get(kit, str(i.get("module")))
            except ValueError as exc:
                f.append(finding("error", "unknown_module", f"instance '{iid}': {exc}", iid))
            continue
        if not (isinstance(i.get("at"), list) and len(i["at"]) >= 2):
            f.append(finding("error", "bad_position", f"instance '{iid}' needs at: [x, z]", iid))
            continue
        sc = m["scale"]
        if not sc["min"] - 1e-9 <= i["scale"] <= sc["max"] + 1e-9:
            f.append(finding("error", "scale_out_of_range", f"instance '{iid}' scale {i['scale']} is outside {m['id']}'s allowed {sc['min']}-{sc['max']}", iid))
    for p in scene["paths"]:
        if not ID_RE.match(str(p.get("id", ""))) or len(p.get("points", [])) < 2:
            f.append(finding("error", "bad_path", f"path '{p.get('id')}' needs an id and at least two points"))
    for lk in scene["locked"].get("instances", []):
        if lk not in seen:
            f.append(finding("error", "unknown_locked", f"locked instance '{lk}' does not exist", lk))
    return f


def placement_findings(scene: dict, kit: dict, inst: dict, others: list[dict], *, spacing: float = 0.0) -> list[Finding]:
    """Everything wrong with one instance against region, exclusions, other instances, paths and corridors."""
    mods = K.index(kit)
    m = mods[inst["module"]]
    poly = poly_of(inst, m)
    y0, y1 = y_range(inst, m)
    f: list[Finding] = []
    iid = inst["id"]
    x, z, w, d = region_rect(scene)
    if not geom.inside_rect(poly, x, z, w, d):
        f.append(finding("error", "outside_region", f"'{iid}' ({inst['module']}) does not fit inside the dressing region", iid))
    for k, ex in enumerate(scene["exclusions"]):
        if geom.overlap_depth(poly, geom.rect_corners(*ex)) > 0:
            f.append(finding("error", "in_exclusion", f"'{iid}' overlaps exclusion zone {k}", iid))
    if m["collision"]:
        grown = geom.footprint_corners(tuple(inst["at"][:2]), inst["yaw"], (m["footprint"][0] + 2 * spacing / max(inst["scale"], 1e-9), m["footprint"][1] + 2 * spacing / max(inst["scale"], 1e-9)),
                                       inst["scale"], tuple(m["footprint_offset"])) if spacing else poly
        for o in others:
            om = mods[o["module"]]
            if o["id"] == iid or not om["collision"]:
                continue
            oy0, oy1 = y_range(o, om)
            if min(y1, oy1) - max(y0, oy0) <= 1e-6:
                continue
            depth = geom.overlap_depth(grown, poly_of(o, om))
            if depth > 0:
                f.append(finding("error", "collision", f"'{iid}' ({inst['module']}) collides with '{o['id']}' ({o['module']}) by {depth:.2f} studs", iid))
    if m["collision"]:
        floor = scene["region"]["floor_y"]
        for p in scene["paths"]:
            pts = [tuple(q) for q in p["points"]]
            for a, b in zip(pts, pts[1:]):
                if y0 < floor + PATH_HEIGHT and geom.overlap_depth(poly, geom.segment_rect(a, b, p["width"])) > 0:
                    f.append(finding("error", "blocks_path", f"'{iid}' ({inst['module']}) blocks path '{p['id']}' (needs {p['width']} studs clear)", iid))
                    break
    for c in scene["corridors"]:
        if y0 <= c["eye_height"] <= y1 and geom.overlap_depth(poly, geom.segment_rect(tuple(c["from"]), tuple(c["to"]), c["width"])) > 0 and m["collision"]:
            f.append(finding("error", "blocks_sightline", f"'{iid}' ({inst['module']}) blocks sightline '{c.get('id', '?')}' at eye height {c['eye_height']}", iid))
    return f


def validate(scene: dict, kit: dict, *, spacing: float = 0.0) -> list[Finding]:
    f = validate_structure(scene, kit)
    if any(x["severity"] == "error" for x in f if x["code"] in ("bad_region", "unknown_module", "bad_position")):
        return f
    for inst in scene["instances"]:
        f.extend(placement_findings(scene, kit, inst, scene["instances"], spacing=spacing))
    # a colliding pair is reported from both sides; keep one finding per pair
    seen, out = set(), []
    for x in f:
        if x["code"] == "collision":
            other = re.search(r"collides with '([^']+)'", x["message"]).group(1)
            key = tuple(sorted((x["where"], other)))
            if key in seen:
                continue
            seen.add(key)
        out.append(x)
    return out


# --- plans ----------------------------------------------------------------------------------------------------------------------------
def empty_plan() -> dict:
    return {"adds": [], "moves": [], "removes": []}


def apply_plan(scene: dict, plan: dict) -> dict:
    s = copy.deepcopy(scene)
    by = {i["id"]: i for i in s["instances"]}
    locked = {i["id"] for i in s["instances"] if i.get("locked")} | set(s["locked"].get("instances", []))
    for mv in plan["moves"]:
        if mv["id"] in locked:
            raise ValueError(f"instance '{mv['id']}' is locked and cannot be moved")
        if mv["id"] not in by:
            raise ValueError(f"cannot move unknown instance '{mv['id']}'")
        by[mv["id"]].update({k: v for k, v in mv["to"].items()})
    for rid in plan["removes"]:
        if rid in locked:
            raise ValueError(f"instance '{rid}' is locked and cannot be removed")
        if rid not in by:
            raise ValueError(f"cannot remove unknown instance '{rid}'")
    s["instances"] = [i for i in s["instances"] if i["id"] not in set(plan["removes"])]
    ids = {i["id"] for i in s["instances"]}
    for a in plan["adds"]:
        if a["id"] in ids:
            raise ValueError(f"instance id '{a['id']}' already exists")
        s["instances"].append(copy.deepcopy(a))
        ids.add(a["id"])
    return s


def invert_plan(scene_before: dict, plan: dict) -> dict:
    """The plan that undoes ``plan`` when applied to the scene it produced."""
    by = {i["id"]: i for i in scene_before["instances"]}
    return {"adds": [copy.deepcopy(by[r]) for r in plan["removes"]],
            "moves": [{"id": mv["id"], "from": mv["to"], "to": mv["from"]} for mv in plan["moves"]],
            "removes": [a["id"] for a in plan["adds"]]}


def diff(a: dict, b: dict) -> dict:
    ia, ib = {i["id"]: i for i in a["instances"]}, {i["id"]: i for i in b["instances"]}
    changed = [{"id": k, "fields": [f for f in ("module", "at", "y", "yaw", "scale") if ia[k].get(f) != ib[k].get(f)]} for k in sorted(set(ia) & set(ib))
               if any(ia[k].get(f) != ib[k].get(f) for f in ("module", "at", "y", "yaw", "scale"))]
    out = {"added": sorted(set(ib) - set(ia)), "removed": sorted(set(ia) - set(ib)), "changed": changed, "region_changed": a["region"] != b["region"]}
    out["unchanged"] = not (out["added"] or out["removed"] or out["changed"] or out["region_changed"])
    return out


def scene_hash(scene: dict) -> str:
    keep = {"id": scene["id"], "region": scene["region"], "instances": [{k: i[k] for k in ("id", "module", "at", "y", "yaw", "scale")} for i in sorted(scene["instances"], key=lambda i: i["id"])]}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


def lock_snapshot(scene: dict) -> dict:
    ids = {i["id"] for i in scene["instances"] if i.get("locked")} | set(scene["locked"].get("instances", []))
    return {i["id"]: {"module": i["module"], "at": list(i["at"][:2]), "y": i["y"], "yaw": i["yaw"], "scale": i["scale"]}
            for i in scene["instances"] if i["id"] in ids}


def check_locks(scene: dict, baseline: dict) -> list[Finding]:
    now = {i["id"]: i for i in scene["instances"]}
    out = []
    for iid, want in baseline.items():
        have = now.get(iid)
        if have is None:
            out.append(finding("error", "locked_removed", f"locked instance '{iid}' was removed", iid))
            continue
        cur = {"module": have["module"], "at": list(have["at"][:2]), "y": have["y"], "yaw": have["yaw"], "scale": have["scale"]}
        if cur != want:
            out.append(finding("error", "locked_changed", f"locked instance '{iid}' changed: {want} -> {cur}", iid))
    return out
