"""Level specification: structured data first, geometry second.

A *level spec* describes rooms (axis-aligned rectangles on a stud grid), the connections between them (door,
opening, stairs, ramp), spawns, objectives, landmarks, encounters and *locked* constraints. The model writes or
edits a spec; this module normalises and validates it (exact arithmetic), and other modules analyse and build it.

Coordinates: ``x`` and ``z`` are horizontal studs, ``y`` is up. ``rect = [x, z, width, depth]`` is a room's
OUTER boundary; walls of thickness ``wall`` sit inside it. Compass sides: W/E are the min/max-x edges and N/S the
min/max-z edges. ``floor`` is the room's floor height.

Character numbers (height, step, slope, jump) are configurable ASSUMPTIONS in ``style/style.yaml``; verify them
against your game's character settings.
"""

from __future__ import annotations

import ast
import copy
import math
import operator
from pathlib import Path
from typing import Any

import yaml

KINDS = ("door", "opening", "stairs", "ramp")
SIDES = ("N", "S", "E", "W")
TAGS_SPECIAL = {"spawn", "objective", "loot", "secret", "hub", "safe"}  # rooms allowed to be dead ends


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


# --- safe arithmetic for templates -----------------------------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_FUNCS = {"min": min, "max": max, "round": round, "int": int, "abs": abs}


def eval_expr(expr: str, names: dict) -> float:
    """Evaluate ``+ - * / // %``, parentheses, parameter names and min/max/round/int/abs. Nothing else."""

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"expression '{expr}' uses unknown name '{node.id}'")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"expression '{expr}' uses something other than arithmetic")

    try:
        return ev(ast.parse(expr.strip(), mode="eval"))
    except (ArithmeticError, SyntaxError) as exc:
        raise ValueError(f"expression '{expr}' could not be evaluated: {exc}") from exc


def _resolve(value: Any, params: dict) -> Any:
    if isinstance(value, str) and value.startswith("="):
        return eval_expr(value[1:], params)
    if isinstance(value, list):
        return [_resolve(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, params) for k, v in value.items()}
    return value


# --- templates ---------------------------------------------------------------------------------------------------------
def load_template(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "id" not in data or "spec" not in data:
        raise ValueError(f"{path}: template needs 'id' and 'spec'")
    return data


def list_templates(directory: Path) -> list[dict]:
    out = []
    for f in sorted(Path(directory).glob("*.yaml")):
        t = load_template(f)
        out.append({"id": t["id"], "title": t.get("title", t["id"]), "description": " ".join(t.get("description", "").split()),
                    "parameters": dict(t.get("parameters", {})), "file": f.name})
    return out


def resolve_template_params(template: dict, overrides: dict | None) -> dict:
    declared = template.get("parameters", {})
    overrides = overrides or {}
    errors = [f"unknown parameter '{k}'. Available: {sorted(declared)}" for k in overrides if k not in declared]
    values = {}
    for name, spec in declared.items():
        v = overrides.get(name, spec.get("default"))
        if v is None:
            errors.append(f"{name}: no value and no default")
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errors.append(f"{name}: expected a number, got {v!r}")
            continue
        if "min" in spec and v < spec["min"]:
            errors.append(f"{name}: {v} is below minimum {spec['min']}")
        if "max" in spec and v > spec["max"]:
            errors.append(f"{name}: {v} is above maximum {spec['max']}")
        values[name] = v
    if errors:
        raise ValueError("invalid template parameters: " + "; ".join(errors))
    return values


def instantiate(template: dict, overrides: dict | None = None, *, spec_id: str | None = None) -> dict:
    params = resolve_template_params(template, overrides)
    spec = _resolve(copy.deepcopy(template["spec"]), params)
    spec["id"] = spec_id or spec.get("id") or template["id"]
    spec.setdefault("template", {"id": template["id"], "parameters": params})
    return spec


# --- geometry helpers --------------------------------------------------------------------------------------------------------
def rect_of(room: dict) -> tuple[float, float, float, float]:
    x, z, w, d = room["rect"]
    return x, z, w, d


def center(room: dict) -> tuple[float, float]:
    x, z, w, d = rect_of(room)
    return x + w / 2, z + d / 2


def interiors_overlap(a: dict, b: dict) -> float:
    ax, az, aw, ad = rect_of(a)
    bx, bz, bw, bd = rect_of(b)
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oz = min(az + ad, bz + bd) - max(az, bz)
    return ox * oz if ox > 1e-9 and oz > 1e-9 else 0.0


def shared_wall(a: dict, b: dict) -> dict | None:
    """The wall two rooms share, from ``a``'s point of view. ``axis`` is the axis the wall runs along."""
    ax, az, aw, ad = rect_of(a)
    bx, bz, bw, bd = rect_of(b)
    eps = 1e-6

    def span(lo1, hi1, lo2, hi2):
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        return (lo, hi) if hi - lo > eps else None

    if abs(ax + aw - bx) < eps and (s := span(az, az + ad, bz, bz + bd)):
        return {"side_a": "E", "side_b": "W", "axis": "z", "line": ax + aw, "lo": s[0], "hi": s[1]}
    if abs(bx + bw - ax) < eps and (s := span(az, az + ad, bz, bz + bd)):
        return {"side_a": "W", "side_b": "E", "axis": "z", "line": ax, "lo": s[0], "hi": s[1]}
    if abs(az + ad - bz) < eps and (s := span(ax, ax + aw, bx, bx + bw)):
        return {"side_a": "S", "side_b": "N", "axis": "x", "line": az + ad, "lo": s[0], "hi": s[1]}
    if abs(bz + bd - az) < eps and (s := span(ax, ax + aw, bx, bx + bw)):
        return {"side_a": "N", "side_b": "S", "axis": "x", "line": az, "lo": s[0], "hi": s[1]}
    return None


def door_point(conn: dict) -> tuple[float, float]:
    """Plan position (x, z) of a connection's centre on the shared wall line."""
    return (conn["line"], conn["at"]) if conn["axis"] == "z" else (conn["at"], conn["line"])


# --- normalisation --------------------------------------------------------------------------------------------------------------
def _snap(v: float, grid: float) -> float:
    return round(v / grid) * grid


def normalize(spec: dict, style: dict | None = None) -> tuple[dict, list[dict]]:
    """Fill defaults, snap to the grid, resolve connection geometry. Returns (spec, findings)."""
    style = style or {}
    defaults = style.get("defaults", {})
    s = copy.deepcopy(spec)
    f: list[dict] = []
    s.setdefault("units", "studs")
    s.setdefault("grid", defaults.get("grid", 4))
    s.setdefault("wall", defaults.get("wall", 2))
    s.setdefault("player", {})
    for k, v in style.get("player", {}).items():
        s["player"].setdefault(k, v)
    grid = s["grid"]
    snapped = 0
    for room in s.get("rooms", []):
        if "rect" not in room or len(room["rect"]) != 4:
            f.append(finding("error", "bad_rect", f"room '{room.get('id')}' needs rect [x, z, width, depth]", str(room.get("id"))))
            continue
        new = [_snap(float(v), grid) for v in room["rect"]]
        if any(abs(a - b) > 1e-9 for a, b in zip(new, room["rect"])):
            snapped += 1
        room["rect"] = new
        room.setdefault("floor", 0.0)
        room["floor"] = float(room["floor"])
        room.setdefault("height", defaults.get("room_height", 16))
        room.setdefault("tags", [])
        room.setdefault("name", room["id"])
    if snapped:
        f.append(finding("info", "snapped_to_grid", f"{snapped} room rectangle(s) were snapped to the {grid}-stud grid"))
    rooms = {r["id"]: r for r in s.get("rooms", []) if "id" in r and "rect" in r and len(r["rect"]) == 4}
    for conn in s.get("connections", []):
        conn.setdefault("kind", "door")
        conn.setdefault("width", {"door": defaults.get("door_width", 8), "opening": defaults.get("opening_width", 16),
                                  "stairs": defaults.get("stairs_width", 8), "ramp": defaults.get("ramp_width", 10)}.get(conn["kind"], 8))
        a, b = rooms.get(conn.get("from")), rooms.get(conn.get("to"))
        if not a or not b:
            continue
        wall = shared_wall(a, b)
        if wall is None:
            continue
        conn["axis"], conn["line"] = wall["axis"], wall["line"]
        conn["side_from"], conn["side_to"] = wall["side_a"], wall["side_b"]
        conn["overlap"] = [wall["lo"], wall["hi"]]
        conn["at"] = _snap(float(conn["at"]), grid / 2) if "at" in conn else (wall["lo"] + wall["hi"]) / 2
        if conn["kind"] in ("stairs", "ramp"):
            conn["rise"] = abs(b["floor"] - a["floor"])
            conn["low"], conn["high"] = (conn["from"], conn["to"]) if a["floor"] <= b["floor"] else (conn["to"], conn["from"])
            conn.setdefault("run", max(conn["rise"] / math.tan(math.radians(defaults.get("ramp_design_slope_deg", 25))), grid)
                            if conn["kind"] == "ramp" else max(conn["rise"] * 2, grid))
            conn["run"] = _snap(conn["run"], grid / 2)
    for key in ("spawns", "objectives", "landmarks", "encounters"):
        s.setdefault(key, [])
    s.setdefault("locked", {})
    return s, f


# --- validation ------------------------------------------------------------------------------------------------------------------
def validate(spec: dict, style: dict | None = None) -> list[dict]:
    """Structural and geometric validity of a NORMALISED spec."""
    style = style or {}
    limits = style.get("limits", {})
    f: list[dict] = []
    player = spec["player"]
    grid, wall = spec["grid"], spec["wall"]
    bounds = spec.get("bounds")
    if not bounds or not all(k in bounds for k in ("width", "depth")):
        f.append(finding("error", "no_bounds", "spec needs bounds: {width, depth}"))
        bounds = {"width": float("inf"), "depth": float("inf")}
    ids = [r.get("id") for r in spec.get("rooms", [])]
    if not ids:
        f.append(finding("error", "no_rooms", "spec has no rooms"))
    if len(ids) != len(set(ids)):
        f.append(finding("error", "duplicate_id", "room ids must be unique"))
    rooms = {r["id"]: r for r in spec.get("rooms", []) if "id" in r and "rect" in r and len(r["rect"]) == 4}
    min_room = limits.get("min_room_size", 12)
    for r in rooms.values():
        x, z, w, d = rect_of(r)
        rid = r["id"]
        if w <= 0 or d <= 0:
            f.append(finding("error", "bad_rect", f"room '{rid}' has a non-positive size", rid))
        if min(w, d) < min_room:
            f.append(finding("error", "room_too_small", f"room '{rid}' is {w}x{d}; minimum is {min_room} studs", rid))
        if x < 0 or z < 0 or x + w > bounds["width"] + 1e-9 or z + d > bounds["depth"] + 1e-9:
            f.append(finding("error", "out_of_bounds", f"room '{rid}' lies outside bounds {bounds['width']}x{bounds['depth']}", rid))
        if r["height"] < player["height"] * limits.get("min_ceiling_factor", 2.4):
            f.append(finding("error", "ceiling_low", f"room '{rid}' height {r['height']} is below {limits.get('min_ceiling_factor', 2.4)}x the player height", rid))
        if w % grid or d % grid or x % grid or z % grid:
            f.append(finding("warning", "not_grid_aligned", f"room '{rid}' is not aligned to the {grid}-stud grid", rid))
    room_list = list(rooms.values())
    for i, a in enumerate(room_list):
        for b in room_list[i + 1 :]:
            if interiors_overlap(a, b) > 0:
                f.append(finding("error", "room_overlap", f"rooms '{a['id']}' and '{b['id']}' overlap", a["id"]))

    cids = [c.get("id") for c in spec.get("connections", [])]
    if len(cids) != len(set(cids)):
        f.append(finding("error", "duplicate_id", "connection ids must be unique"))
    for c in spec.get("connections", []):
        cid = c.get("id", "?")
        if c.get("kind") not in KINDS:
            f.append(finding("error", "bad_kind", f"connection '{cid}': kind must be one of {KINDS}", cid))
            continue
        a, b = rooms.get(c.get("from")), rooms.get(c.get("to"))
        if not a or not b:
            f.append(finding("error", "unknown_room", f"connection '{cid}' refers to an unknown room", cid))
            continue
        if "axis" not in c:
            f.append(finding("error", "no_shared_wall", f"connection '{cid}': rooms '{c['from']}' and '{c['to']}' do not share a wall", cid))
            continue
        lo, hi = c["overlap"]
        half = c["width"] / 2
        if c["width"] < limits.get("min_door_width", 4):
            f.append(finding("error", "door_too_narrow", f"connection '{cid}' is {c['width']} wide; minimum {limits.get('min_door_width', 4)}", cid))
        if c["at"] - half < lo + wall - 1e-9 or c["at"] + half > hi - wall + 1e-9:
            f.append(finding("error", "door_outside_wall", f"connection '{cid}' ({c['at'] - half:.1f}..{c['at'] + half:.1f}) does not fit the shared wall "
                             f"({lo:.1f}..{hi:.1f}) with {wall} studs of frame at each end", cid))
        rise = abs(a["floor"] - b["floor"])
        if c["kind"] in ("door", "opening") and rise > 1e-9:
            f.append(finding("error", "floor_mismatch", f"connection '{cid}' is a {c['kind']} between floors {a['floor']} and {b['floor']}; use stairs or a ramp", cid))
        if c["kind"] in ("stairs", "ramp"):
            if rise < 1e-9:
                f.append(finding("error", "no_rise", f"connection '{cid}' is {c['kind']} between rooms on the same floor", cid))
                continue
            run = c["run"]
            slope = math.degrees(math.atan2(rise, run))
            if c["kind"] == "ramp" and slope > limits.get("max_ramp_slope_deg", 40):
                f.append(finding("error", "ramp_too_steep", f"ramp '{cid}' is {slope:.0f} degrees; maximum {limits.get('max_ramp_slope_deg', 40)}", cid))
            if c["kind"] == "stairs":
                step = c.get("step_height", limits.get("stairs_step_height", 1.0))
                if step > player["max_step"] + 1e-9:
                    f.append(finding("error", "step_too_high", f"stairs '{cid}' step {step} exceeds the player's max step {player['max_step']}", cid))
            low = rooms[c["low"]]
            lx, lz, lw, ld = rect_of(low)
            extent = lw if c["axis"] == "z" else ld  # perpendicular to the wall
            if run + wall > extent:
                f.append(finding("error", "run_does_not_fit", f"'{cid}' needs {run}+{wall} studs inside room '{c['low']}' but it is only {extent} deep", cid))
            if rise > limits.get("max_rise", 24):
                f.append(finding("warning", "tall_climb", f"'{cid}' climbs {rise} studs", cid))
    # placed things
    for key, label in (("spawns", "spawn"), ("objectives", "objective"), ("landmarks", "landmark")):
        seen = set()
        for item in spec.get(key, []):
            iid = item.get("id", "?")
            if iid in seen:
                f.append(finding("error", "duplicate_id", f"{label} id '{iid}' used twice", iid))
            seen.add(iid)
            at = item.get("at")
            if not at or len(at) < 2:
                f.append(finding("error", "bad_position", f"{label} '{iid}' needs at: [x, z]", iid))
                continue
            room = next((r for r in room_list if _inside(r, at[0], at[1], wall)), None)
            if room is None:
                f.append(finding("error", f"{label}_outside", f"{label} '{iid}' at {at[:2]} is not inside any room's free area", iid))
            elif item.get("room") not in (None, room["id"]):
                f.append(finding("error", "room_mismatch", f"{label} '{iid}' says room '{item['room']}' but is inside '{room['id']}'", iid))
            else:
                item["room"] = room["id"]
    for e in spec.get("encounters", []):
        if e.get("room") not in rooms:
            f.append(finding("error", "unknown_room", f"encounter '{e.get('id')}' refers to unknown room '{e.get('room')}'", str(e.get("id"))))
        if not 1 <= e.get("intensity", 1) <= 5:
            f.append(finding("error", "bad_intensity", f"encounter '{e.get('id')}' intensity must be 1-5", str(e.get("id"))))
    for rid in spec.get("locked", {}).get("rooms", []):
        if rid not in rooms:
            f.append(finding("error", "unknown_locked", f"locked room '{rid}' does not exist", rid))
    for cid in spec.get("locked", {}).get("connections", []):
        if cid not in cids:
            f.append(finding("error", "unknown_locked", f"locked connection '{cid}' does not exist", cid))
    return f


def _inside(room: dict, x: float, z: float, wall: float) -> bool:
    rx, rz, rw, rd = rect_of(room)
    return rx + wall <= x <= rx + rw - wall and rz + wall <= z <= rz + rd - wall


def prepare(spec: dict, style: dict | None = None) -> tuple[dict, list[dict]]:
    """normalise + validate. Returns (normalised spec, all findings)."""
    norm, f1 = normalize(spec, style)
    return norm, f1 + validate(norm, style)


def has_errors(findings: list[dict]) -> bool:
    return any(x["severity"] == "error" for x in findings)
