"""Blockout geometry and Luau generation, plus locked-constraint checks and revision diffs.

A normalised, validated spec becomes a list of axis-aligned parts (floors, wall segments with door gaps and
lintels, stair/ramp steps, spawns, objective and landmark markers, encounter zones). Only ``Position`` and ``Size``
are used (no rotations), so the geometry is exact and the Luau is plain. Ramps are built as fine steps; they are
walkable blockout stand-ins, not final art.

Safety: the generated script creates ONE Folder named ``AI_Blockout_<id>`` and replaces only a previous folder of
that name carrying ``AIGeneratedBy = "rbxlevel"``. It never publishes, saves, or makes network requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import re

from . import api
from .spec import KINDS, finding

MARKER = "rbxlevel"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
TARGET_RE = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_ ]+)*")
FORBIDDEN = ("Publish", "SavePlace", "MarketplaceService", "AssetService", "RequestAsync", "HttpService:Get",
             "HttpService:Post", "loadstring", "require(", "getfenv", "setfenv", "TeleportService", "DataStoreService",
             "MessagingService", "ClearAllChildren", "game:Destroy", "workspace:Destroy")
PALETTE = {"floor": (0.42, 0.44, 0.48), "wall": (0.66, 0.68, 0.72), "lintel": (0.58, 0.60, 0.64), "step": (0.50, 0.52, 0.56),
           "ramp": (0.55, 0.50, 0.40), "team_a": (0.20, 0.45, 0.90), "team_b": (0.90, 0.30, 0.25), "neutral_spawn": (0.3, 0.8, 0.4),
           "objective": (0.95, 0.78, 0.15), "landmark": (0.75, 0.35, 0.85), "ceiling": (0.5, 0.5, 0.55)}
TAG_FLOOR = {"spawn": (0.35, 0.45, 0.62), "loot": (0.55, 0.45, 0.30), "objective": (0.55, 0.50, 0.28), "hub": (0.45, 0.48, 0.50),
             "secret": (0.40, 0.32, 0.50)}


def validate_target_path(path: str) -> str:
    """Dotted DataModel path such as ``workspace.Levels``; anything else could break out of the Luau string."""
    if not TARGET_RE.fullmatch(path):
        raise ValueError(f"target path '{path}' must be a dotted path like workspace.Levels")
    return path


def part(group, name, size, pos, color, *, cls="Part", material="SmoothPlastic", collide=True, transparency=0.0, attrs=None) -> dict:
    return {"group": group, "name": name, "class": cls, "size": [round(v, 3) for v in size], "position": [round(v, 3) for v in pos],
            "color": [round(v, 3) for v in color], "material": material, "collide": collide, "transparency": transparency,
            "attrs": attrs or {}}


def _subtract(lo: float, hi: float, gaps: list[tuple[float, float]]) -> list[tuple[float, float]]:
    segs, cur = [], lo
    for g0, g1 in sorted(gaps):
        if g0 > cur + 1e-6:
            segs.append((cur, min(g0, hi)))
        cur = max(cur, g1)
    if hi > cur + 1e-6:
        segs.append((cur, hi))
    return [(a, b) for a, b in segs if b - a > 1e-6]


def _room_gaps(spec: dict, room: dict, door_h: float) -> dict[str, list[tuple[float, float, bool]]]:
    """Per side: (lo, hi, full_height) gaps from connections touching this room."""
    gaps: dict[str, list] = {s: [] for s in "NSEW"}
    for c in spec["connections"]:
        if "axis" not in c:
            continue
        for rid, side in ((c["from"], c["side_from"]), (c["to"], c["side_to"])):
            if rid == room["id"]:
                half = c["width"] / 2
                full = c["kind"] in ("stairs", "ramp") and rid == c["low"]
                gaps[side].append((c["at"] - half, c["at"] + half, full))
    return gaps


def build_parts(spec: dict, style: dict | None = None) -> list[dict]:
    style = style or {}
    b = style.get("build", {})
    door_h = b.get("door_height", 10.0)
    floor_t = b.get("floor_thickness", 2.0)
    step_h = b.get("step_height", 1.0)
    wall = spec["wall"]
    parts: list[dict] = []
    rooms = {r["id"]: r for r in spec["rooms"]}
    for r in spec["rooms"]:
        x, z, w, d = r["rect"]
        y0, h = r["floor"], r["height"]
        g = f"Room_{r['id']}"
        tag = next((t for t in r.get("tags", []) if t in TAG_FLOOR), None)
        parts.append(part(g, "floor", (w, floor_t, d), (x + w / 2, y0 - floor_t / 2, z + d / 2), TAG_FLOOR.get(tag, PALETTE["floor"])))
        gaps = _room_gaps(spec, r, min(door_h, h))
        dh = min(door_h, h - 2)
        for side in "NSWE":
            if side in "NS":
                lo, hi = x, x + w
                zc = z + wall / 2 if side == "N" else z + d - wall / 2
                gl = [(a, b_) for a, b_, _ in gaps[side]]
                for k, (a, b_) in enumerate(_subtract(lo, hi, gl)):
                    parts.append(part(g, f"wall_{side}_{k}", (b_ - a, h, wall), ((a + b_) / 2, y0 + h / 2, zc), PALETTE["wall"]))
                for k, (a, b_, full) in enumerate(sorted(gaps[side])):
                    if not full:
                        parts.append(part(g, f"lintel_{side}_{k}", (b_ - a, h - dh, wall), ((a + b_) / 2, y0 + dh + (h - dh) / 2, zc), PALETTE["lintel"]))
            else:
                lo, hi = z + wall, z + d - wall
                xc = x + wall / 2 if side == "W" else x + w - wall / 2
                gl = [(a, b_) for a, b_, _ in gaps[side]]
                for k, (a, b_) in enumerate(_subtract(lo, hi, gl)):
                    parts.append(part(g, f"wall_{side}_{k}", (wall, h, b_ - a), (xc, y0 + h / 2, (a + b_) / 2), PALETTE["wall"]))
                for k, (a, b_, full) in enumerate(sorted(gaps[side])):
                    if not full:
                        parts.append(part(g, f"lintel_{side}_{k}", (wall, h - dh, b_ - a), (xc, y0 + dh + (h - dh) / 2, (a + b_) / 2), PALETTE["lintel"]))
        if b.get("ceilings"):
            parts.append(part(g, "ceiling", (w, floor_t, d), (x + w / 2, y0 + h + floor_t / 2, z + d / 2), PALETTE["ceiling"], transparency=0.4))
    # stairs and ramps: solid steps inside the lower room, nearest the wall highest
    for c in spec["connections"]:
        if c.get("kind") not in ("stairs", "ramp") or "axis" not in c:
            continue
        low = rooms[c["low"]]
        side = c["side_from"] if c["from"] == c["low"] else c["side_to"]
        inward = {"E": (-1, 0), "W": (1, 0), "S": (0, -1), "N": (0, 1)}[side]
        rise, run = c["rise"], c["run"]
        sh = c.get("step_height", step_h) if c["kind"] == "stairs" else step_h
        n = max(1, math.ceil(rise / sh - 1e-9))
        depth = run / n
        color = PALETTE["ramp"] if c["kind"] == "ramp" else PALETTE["step"]
        for i in range(n):
            top = low["floor"] + rise * (i + 1) / n
            d0, d1 = run - (i + 1) * depth, run - i * depth
            mid = c["line"] + 0.0
            perp_center = (d0 + d1) / 2
            height = top - low["floor"]
            if c["axis"] == "z":  # wall runs along z, steps advance along x
                pos = (c["line"] + inward[0] * perp_center, low["floor"] + height / 2, c["at"])
                size = (depth, height, c["width"])
            else:
                pos = (c["at"], low["floor"] + height / 2, c["line"] + inward[1] * perp_center)
                size = (c["width"], height, depth)
            parts.append(part("Connectors", f"{c['id']}_step_{i:02d}", size, pos, color, attrs={"ConnectionId": c["id"], "Kind": c["kind"]}))
    # markers
    for s in spec["spawns"]:
        room = rooms[s["room"]]
        color = PALETTE["team_a"] if s.get("team") == "a" else PALETTE["team_b"] if s.get("team") == "b" else PALETTE["neutral_spawn"]
        parts.append(part("Markers", f"Spawn_{s['id']}", (6, 1, 6), (s["at"][0], room["floor"] + 0.5, s["at"][1]), color, cls="SpawnLocation",
                          attrs={"SpawnId": s["id"], "Team": s.get("team", "")}))
    for o in spec["objectives"]:
        room = rooms[o["room"]]
        parts.append(part("Markers", f"Objective_{o['id']}", (6, 0.5, 6), (o["at"][0], room["floor"] + 0.25, o["at"][1]), PALETTE["objective"],
                          material="Neon", collide=False, attrs={"ObjectiveId": o["id"], "Kind": o.get("kind", "")}))
    for m in spec["landmarks"]:
        room = rooms[m["room"]]
        h = float(m.get("height", 20))
        parts.append(part("Markers", f"Landmark_{m['id']}", (6, h, 6), (m["at"][0], room["floor"] + h / 2, m["at"][1]), PALETTE["landmark"], attrs={"LandmarkId": m["id"]}))
    for e in spec["encounters"]:
        room = rooms[e["room"]]
        x, z, w, d = room["rect"]
        t = (e["intensity"] - 1) / 4
        parts.append(part("Markers", f"Encounter_{e['id']}", (max(w - 2 * wall - 2, 2), 0.5, max(d - 2 * wall - 2, 2)),
                          (x + w / 2, room["floor"] + 0.25, z + d / 2), (0.2 + 0.7 * t, 0.8 - 0.6 * t, 0.2), collide=False, transparency=0.85,
                          attrs={"EncounterId": e["id"], "Intensity": e["intensity"]}))
    return parts


def part_count_by_group(parts: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in parts:
        out[p["group"]] = out.get(p["group"], 0) + 1
    return out


def bounding_box(parts: list[dict]) -> dict:
    lo = [min(p["position"][i] - p["size"][i] / 2 for p in parts) for i in range(3)]
    hi = [max(p["position"][i] + p["size"][i] / 2 for p in parts) for i in range(3)]
    return {"min": [round(v, 2) for v in lo], "max": [round(v, 2) for v in hi], "size": [round(h - l, 2) for l, h in zip(lo, hi)]}


# --- spec-level id safety ----------------------------------------------------------------------------------------------------
def check_ids(spec: dict) -> list[dict]:
    out = []
    keys = ("rooms", "connections", "spawns", "objectives", "landmarks", "encounters")
    for key in keys:
        for item in spec.get(key, []):
            if not ID_RE.match(str(item.get("id", ""))):
                out.append(finding("error", "bad_id", f"{key[:-1]} id '{item.get('id')}' must match {ID_RE.pattern} (letters, digits, underscore)", str(item.get("id"))))
    if not ID_RE.match(str(spec.get("id", ""))):
        out.append(finding("error", "bad_id", f"spec id '{spec.get('id')}' must match {ID_RE.pattern}"))
    return out


# --- Luau ---------------------------------------------------------------------------------------------------------------------------
def _n(x: float) -> str:
    return str(int(x)) if float(x) == int(x) and abs(x) < 1e9 else f"{x:.4g}"


def _attrs(attrs: dict) -> str:
    items = []
    for k, v in sorted(attrs.items()):
        if not ID_RE.match(k):
            raise ValueError(f"attribute name '{k}' is not allowed")
        if isinstance(v, bool):
            items.append(f"{k} = {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            items.append(f"{k} = {_n(v)}")
        else:
            if not re.fullmatch(r"[A-Za-z0-9_ .-]*", str(v)):
                raise ValueError(f"attribute value '{v}' contains unsupported characters")
            items.append(f'{k} = "{v}"')
    return "{" + ", ".join(items) + "}"


def spec_hash(spec: dict) -> str:
    keep = {k: spec[k] for k in ("id", "bounds", "rooms", "connections", "spawns", "objectives", "landmarks", "encounters", "wall", "grid")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:12]


def build_luau(spec: dict, parts: list[dict], target_path: str = "workspace", style: dict | None = None) -> str:
    validate_target_path(target_path)
    id_problems = [f for f in check_ids(spec) if f["severity"] == "error"]
    if id_problems:
        raise ValueError("refusing to generate Luau: " + id_problems[0]["message"])
    snap = api.load()
    for p in parts:
        if p["class"] not in api.ALLOWED_CLASSES:
            raise ValueError(f"class '{p['class']}' is not allowed in blockouts")
        if p["material"] not in snap["enums"]["Material"]:
            raise ValueError(f"unknown material '{p['material']}'")
    limit = (style or {}).get("limits", {}).get("max_blockout_parts", 800)
    if len(parts) > limit:
        raise ValueError(f"blockout has {len(parts)} parts (limit {limit}); simplify the spec or raise limits.max_blockout_parts deliberately")
    root = f"AI_Blockout_{spec['id']}"
    rows = []
    for p in parts:
        rows.append("\t{" + ", ".join([f'"{p["group"]}"', f'"{p["name"]}"', f'"{p["class"]}"', *(_n(v) for v in p["size"]), *(_n(v) for v in p["position"]),
                                        *(_n(v) for v in p["color"]), f'"{p["material"]}"', "true" if p["collide"] else "false",
                                        _n(p["transparency"]), _attrs(p["attrs"])]) + "},")
    code = "\n".join([
        f"-- GENERATED by rbxlevel (spec {spec_hash(spec)}) - review before running.",
        f'-- Creates/replaces ONE Folder named {root}. It only replaces a folder it created itself (attribute AIGeneratedBy = "{MARKER}").',
        "-- It does not publish, save, or make network requests.",
        f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "{root}"', "",
        "local function resolveTarget(path)", "\tlocal parts = string.split(path, \".\")", "\tlocal node",
        '\tif string.lower(parts[1]) == "workspace" then', "\t\tnode = workspace", "\telse",
        "\t\tlocal ok, service = pcall(function() return game:GetService(parts[1]) end)",
        "\t\tif ok and service then node = service else node = game:FindFirstChild(parts[1]) end", "\tend",
        "\tfor i = 2, #parts do", "\t\tif not node then break end", "\t\tnode = node:FindFirstChild(parts[i])", "\tend",
        "\tif not node then error(\"target path '\" .. path .. \"' was not found\") end", "\treturn node", "end", "",
        "local target = resolveTarget(TARGET_PATH)", "local existing = target:FindFirstChild(ROOT_NAME)", "if existing then",
        f'\tif existing:GetAttribute("AIGeneratedBy") == "{MARKER}" then', "\t\texisting:Destroy()", "\telse",
        "\t\terror(\"refusing to replace '\" .. ROOT_NAME .. \"': it was not created by rbxlevel\")", "\tend", "end", "",
        'local root = Instance.new("Folder")', "root.Name = ROOT_NAME", f'root:SetAttribute("AIGeneratedBy", "{MARKER}")',
        f'root:SetAttribute("AISpecHash", "{spec_hash(spec)}")', "local groups = {}",
        "local function group(name)", "\tif not groups[name] then", '\t\tlocal m = Instance.new("Model")', "\t\tm.Name = name", "\t\tm.Parent = root",
        "\t\tgroups[name] = m", "\tend", "\treturn groups[name]", "end", "",
        "-- group, name, class, size x y z, position x y z, colour r g b, material, collide, transparency, attributes",
        "local PARTS = {", *rows, "}", "",
        "for _, p in ipairs(PARTS) do", "\tlocal part = Instance.new(p[3])", "\tpart.Name = p[2]", "\tpart.Size = Vector3.new(p[4], p[5], p[6])",
        "\tpart.Position = Vector3.new(p[7], p[8], p[9])", "\tpart.Color = Color3.new(p[10], p[11], p[12])", "\tpart.Material = Enum.Material[p[13]]",
        "\tpart.Anchored = true", "\tpart.CanCollide = p[14]", "\tpart.Transparency = p[15]",
        '\tif p[3] == "SpawnLocation" then part.Neutral = true end',
        "\tfor k, v in pairs(p[16]) do part:SetAttribute(k, v) end", "\tpart.Parent = group(p[1])", "end", "",
        "root.Parent = target", "",
        'local json = game:GetService("HttpService"):JSONEncode({status = "ok", root = root:GetFullName(), '
        f'spec_hash = "{spec_hash(spec)}", parts = {len(parts)}}})', "print(json)", "return json", ""])
    issues = lint(code)
    if issues:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(issues))
    return code


def build_remove_luau(spec_id: str, target_path: str = "workspace") -> str:
    if not ID_RE.match(spec_id):
        raise ValueError(f"spec id '{spec_id}' must match {ID_RE.pattern}")
    validate_target_path(target_path)
    root = f"AI_Blockout_{spec_id}"
    code = "\n".join([f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "{root}"', "",
                      "local function resolveTarget(path)", "\tlocal parts = string.split(path, \".\")", "\tlocal node",
                      '\tif string.lower(parts[1]) == "workspace" then', "\t\tnode = workspace", "\telse",
                      "\t\tnode = game:FindFirstChild(parts[1])", "\tend",
                      "\tfor i = 2, #parts do", "\t\tif not node then break end", "\t\tnode = node:FindFirstChild(parts[i])", "\tend",
                      "\tif not node then error(\"target path '\" .. path .. \"' was not found\") end", "\treturn node", "end", "",
                      "local target = resolveTarget(TARGET_PATH)", "local existing = target:FindFirstChild(ROOT_NAME)", 'local status = "not_found"',
                      "if existing then", f'\tif existing:GetAttribute("AIGeneratedBy") == "{MARKER}" then', "\t\texisting:Destroy()", '\t\tstatus = "removed"',
                      "\telse", "\t\terror(\"refusing to remove '\" .. ROOT_NAME .. \"': it was not created by rbxlevel\")", "\tend", "end",
                      'local json = game:GetService("HttpService"):JSONEncode({status = status})', "print(json)", "return json", ""])
    issues = lint(code)
    if issues:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(issues))
    return code


def lint(code: str) -> list[str]:
    issues = [f"forbidden token '{t}'" for t in FORBIDDEN if t in code]
    for m in re.finditer(r":Destroy\(\)", code):
        if f'GetAttribute("AIGeneratedBy") == "{MARKER}"' not in code[max(0, m.start() - 160): m.start()]:
            issues.append("Destroy() is not directly guarded by the AIGeneratedBy check")
    if "HttpService" in code and 'GetService("HttpService"):JSONEncode' not in code:
        issues.append("HttpService may only be used for JSONEncode")
    return issues


# --- locks and revisions ---------------------------------------------------------------------------------------------------------------
def lock_snapshot(spec: dict) -> dict:
    """Capture the geometry of everything the spec marks as locked."""
    locked = spec.get("locked", {})
    rooms = {r["id"]: r for r in spec["rooms"]}
    conns = {c["id"]: c for c in spec["connections"]}
    snap: dict = {"rooms": {}, "connections": {}, "items": {}, "bounds": None}
    for rid in locked.get("rooms", []):
        if rid in rooms:
            r = rooms[rid]
            snap["rooms"][rid] = {"rect": list(r["rect"]), "floor": r["floor"], "height": r["height"]}
    for cid in locked.get("connections", []):
        if cid in conns:
            c = conns[cid]
            snap["connections"][cid] = {"from": c["from"], "to": c["to"], "kind": c["kind"], "at": c.get("at"), "width": c["width"]}
    for key in ("spawns", "objectives", "landmarks"):
        for iid in locked.get(key, []):
            item = next((i for i in spec[key] if i["id"] == iid), None)
            if item:
                snap["items"][f"{key}:{iid}"] = {"at": list(item["at"][:2])}
    if locked.get("bounds"):
        snap["bounds"] = {"width": spec["bounds"]["width"], "depth": spec["bounds"]["depth"]}
    return snap


def check_locks(spec: dict, baseline: dict) -> list[dict]:
    """Compare ``spec`` against a lock snapshot (from the baseline revision). Every difference is an error."""
    out = []
    now = lock_snapshot({**spec, "locked": {**spec.get("locked", {}), **_locks_from_snapshot(baseline)}})
    for kind in ("rooms", "connections", "items"):
        for key, want in baseline.get(kind, {}).items():
            have = now[kind].get(key)
            if have is None:
                out.append(finding("error", "locked_removed", f"locked {kind[:-1]} '{key}' was removed", key))
            elif have != want:
                out.append(finding("error", "locked_changed", f"locked {kind[:-1]} '{key}' changed: {want} -> {have}", key))
    if baseline.get("bounds") and now["bounds"] != baseline["bounds"]:
        out.append(finding("error", "locked_changed", f"locked bounds changed: {baseline['bounds']} -> {now['bounds']}", "bounds"))
    return out


def _locks_from_snapshot(baseline: dict) -> dict:
    return {"rooms": list(baseline.get("rooms", {})), "connections": list(baseline.get("connections", {})),
            "bounds": bool(baseline.get("bounds")),
            **{k: [i.split(":", 1)[1] for i in baseline.get("items", {}) if i.startswith(k + ":")] for k in ("spawns", "objectives", "landmarks")}}


def diff_specs(old: dict, new: dict) -> dict:
    def index(spec, key):
        return {i["id"]: i for i in spec.get(key, [])}

    out: dict = {}
    for key in ("rooms", "connections", "spawns", "objectives", "landmarks", "encounters"):
        a, b = index(old, key), index(new, key)
        changed = []
        for iid in sorted(set(a) & set(b)):
            fields = [f for f in ("rect", "floor", "height", "kind", "at", "width", "from", "to", "intensity", "team", "tags")
                      if a[iid].get(f) != b[iid].get(f)]
            if fields:
                changed.append({"id": iid, "fields": fields})
        out[key] = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)), "changed": changed}
    out["bounds_changed"] = old.get("bounds") != new.get("bounds")
    out["unchanged"] = not out["bounds_changed"] and all(not (v["added"] or v["removed"] or v["changed"]) for k, v in out.items() if isinstance(v, dict))
    return out


# --- read back what exists in Studio ---------------------------------------------------------------------------------------------------
def build_inspect_luau(spec_id: str, target_path: str = "workspace") -> str:
    """Read-only Luau: lists every Part under the blockout folder as JSON (name, group, class, size, position)."""
    if not ID_RE.match(spec_id):
        raise ValueError(f"spec id '{spec_id}' must match {ID_RE.pattern}")
    validate_target_path(target_path)
    code = "\n".join([
        "-- GENERATED by rbxlevel (inspect) - read-only: reads parts, changes nothing.",
        f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "AI_Blockout_{spec_id}"', "",
        "local function resolveTarget(path)", "\tlocal parts = string.split(path, \".\")", "\tlocal node",
        '\tif string.lower(parts[1]) == "workspace" then', "\t\tnode = workspace", "\telse", "\t\tnode = game:FindFirstChild(parts[1])", "\tend",
        "\tfor i = 2, #parts do", "\t\tif not node then break end", "\t\tnode = node:FindFirstChild(parts[i])", "\tend",
        "\tif not node then error(\"target path '\" .. path .. \"' was not found\") end", "\treturn node", "end", "",
        "local root = resolveTarget(TARGET_PATH):FindFirstChild(ROOT_NAME)",
        'if not root then error("blockout \'" .. ROOT_NAME .. "\' was not found") end',
        "local out = {}",
        "for _, d in ipairs(root:GetDescendants()) do",
        '\tif d:IsA("BasePart") then',
        "\t\tout[#out + 1] = {group = d.Parent.Name, name = d.Name, class = d.ClassName,",
        "\t\t\tsize = {d.Size.X, d.Size.Y, d.Size.Z}, position = {d.Position.X, d.Position.Y, d.Position.Z}}",
        "\tend", "end",
        'local json = game:GetService("HttpService"):JSONEncode({status = "ok", root = root:GetFullName(), '
        'spec_hash = root:GetAttribute("AISpecHash"), parts = out})',
        "print(json)", "return json", ""])
    issues = lint(code)
    if issues:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(issues))
    return code


def compare_built(expected: list[dict], report: dict, tol: float = 0.05) -> dict:
    """Compare the parts the spec wants with the parts found in Studio (detects hand edits and drift)."""
    want = {(p["group"], p["name"]): p for p in expected}
    have = {(p["group"], p["name"]): p for p in report["parts"]}
    missing = sorted(f"{g}/{n}" for g, n in set(want) - set(have))
    extra = sorted(f"{g}/{n}" for g, n in set(have) - set(want))
    moved = []
    for key in sorted(set(want) & set(have)):
        w, h = want[key], have[key]
        dpos = max(abs(a - b) for a, b in zip(w["position"], h["position"]))
        dsize = max(abs(a - b) for a, b in zip(w["size"], h["size"]))
        if dpos > tol or dsize > tol:
            moved.append({"part": f"{key[0]}/{key[1]}", "position_delta": round(dpos, 3), "size_delta": round(dsize, 3)})
    return {"in_sync": not (missing or extra or moved), "missing": missing, "extra": extra, "moved": moved,
            "expected": len(want), "found": len(have)}
