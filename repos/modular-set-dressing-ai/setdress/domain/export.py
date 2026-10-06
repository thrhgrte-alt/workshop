"""Exports: engine-neutral JSON, and Luau for Roblox Studio (primitive boxes or clones of your kit's templates).

Luau safety: one root named ``AI_SetDress_<id>`` is created, and on re-run only a root carrying
``AIGeneratedBy = "setdress"`` is replaced. No publishing, saving, or network calls; ids, paths and template names are
restricted so they cannot break out of the generated strings. Plain-Lua syntax only, so the mock can run it.
"""

from __future__ import annotations

import json
import re

from . import api, geom, kit as K, scene as SC

MARKER = "setdress"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
PATH_RE = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_ ]+)*")
FORBIDDEN = ("Publish", "SavePlace", "MarketplaceService", "AssetService", "RequestAsync", "HttpService:Get", "HttpService:Post", "loadstring",
             "require(", "getfenv", "setfenv", "TeleportService", "DataStoreService", "MessagingService", "ClearAllChildren", "game:Destroy", "workspace:Destroy")
MATERIALS = {"wood": "Wood", "stone": "Slate", "metal": "Metal", "fabric": "Fabric", "plastic": "SmoothPlastic", "glass": "Glass", "plant": "Grass",
             "brick": "Brick", "concrete": "Concrete", "leather": "Leather"}


def validate_path(path: str) -> str:
    if not PATH_RE.fullmatch(path):
        raise ValueError(f"path '{path}' must be a dotted path like workspace.Props or ReplicatedStorage.Kit")
    return path


def to_json(scene: dict, kit: dict) -> dict:
    """Engine-neutral description: world transforms of every instance (position of the module pivot, yaw degrees about +Y)."""
    mods = K.index(kit)
    return {"format": "setdress-scene/1", "id": scene["id"], "scene_hash": SC.scene_hash(scene), "units": scene["units"],
            "convention": "yaw rotates local +Z toward +X (CFrame.Angles(0, rad(yaw), 0)); at=[x,z]; y=base height of the pivot",
            "instances": [{"id": i["id"], "module": i["module"], "template": mods[i["module"]].get("template"), "position": [i["at"][0], i["y"], i["at"][1]],
                           "yaw": i["yaw"], "scale": i["scale"]} for i in scene["instances"]]}


def primitive_rows(scene: dict, kit: dict) -> list[dict]:
    mods = K.index(kit)
    snap = api.load()
    rows = []
    for i in scene["instances"]:
        m = mods[i["module"]]
        ox, oz = geom.rot(m["footprint_offset"][0] * i["scale"], m["footprint_offset"][1] * i["scale"], i["yaw"])
        base_y = i["y"] + m["y_offset"] * i["scale"]
        mat = MATERIALS.get(m.get("material", ""), "SmoothPlastic")
        if mat not in snap["enums"]["Material"]:
            mat = "SmoothPlastic"
        color = [round(c, 3) for c in (int(m.get("color", "#9a9a9a")[k : k + 2], 16) / 255 for k in (1, 3, 5))]
        rows.append({"id": i["id"], "module": i["module"], "size": [round(m["footprint"][0] * i["scale"], 3), round(m["height"] * i["scale"], 3), round(m["footprint"][1] * i["scale"], 3)],
                     "position": [round(i["at"][0] + ox, 3), round(base_y + m["height"] * i["scale"] / 2, 3), round(i["at"][1] + oz, 3)], "yaw": i["yaw"],
                     "color": color, "material": mat, "collide": bool(m["collision"]), "scale": i["scale"], "template": m.get("template")})
    return rows


def _n(x: float) -> str:
    return str(int(x)) if float(x) == int(x) and abs(x) < 1e9 else f"{x:.5g}"


RESOLVE = [
    "local function resolveTarget(path)", "\tlocal parts = string.split(path, \".\")", "\tlocal node",
    '\tif string.lower(parts[1]) == "workspace" then', "\t\tnode = workspace", "\telse",
    "\t\tlocal ok, service = pcall(function() return game:GetService(parts[1]) end)",
    "\t\tif ok and service then node = service else node = game:FindFirstChild(parts[1]) end", "\tend",
    "\tfor i = 2, #parts do", "\t\tif not node then break end", "\t\tnode = node:FindFirstChild(parts[i])", "\tend",
    "\tif not node then error(\"path '\" .. path .. \"' was not found\") end", "\treturn node", "end", ""]


def lint(code: str) -> list[str]:
    issues = [f"forbidden token '{t}'" for t in FORBIDDEN if t in code]
    for m in re.finditer(r":Destroy\(\)", code):
        if f'GetAttribute("AIGeneratedBy") == "{MARKER}"' not in code[max(0, m.start() - 160): m.start()]:
            issues.append("Destroy() is not directly guarded by the AIGeneratedBy check")
    if "HttpService" in code and 'GetService("HttpService"):JSONEncode' not in code:
        issues.append("HttpService may only be used for JSONEncode")
    return issues


def build_luau(scene: dict, kit: dict, *, target_path: str = "workspace", mode: str = "primitives", kit_path: str = "ReplicatedStorage.Kit",
               max_items: int = 600) -> str:
    validate_path(target_path)
    validate_path(kit_path)
    if mode not in ("primitives", "clone"):
        raise ValueError("mode must be 'primitives' or 'clone'")
    if not ID_RE.match(scene["id"]):
        raise ValueError(f"scene id '{scene['id']}' must match {ID_RE.pattern}")
    if len(scene["instances"]) > max_items:
        raise ValueError(f"scene has {len(scene['instances'])} instances (limit {max_items}); split it into regions")
    rows = primitive_rows(scene, kit)
    for r in rows:
        if not ID_RE.match(r["id"]) or not ID_RE.match(r["module"]):
            raise ValueError(f"refusing to generate Luau: unsafe id '{r['id']}' / '{r['module']}'")
        if mode == "clone" and not (r["template"] and ID_RE.match(r["template"])):
            raise ValueError(f"module '{r['module']}' has no valid 'template' name for clone mode; add one to the kit or use mode='primitives'")
    root = f"AI_SetDress_{scene['id']}"
    h = SC.scene_hash(scene)
    if mode == "primitives":
        data = [f'\t{{"{r["id"]}", "{r["module"]}", {", ".join(_n(v) for v in r["size"])}, {", ".join(_n(v) for v in r["position"])}, {_n(r["yaw"])}, '
                f'{", ".join(_n(v) for v in r["color"])}, "{r["material"]}", {"true" if r["collide"] else "false"}}},' for r in rows]
        body = ["-- id, module, size x y z, position x y z, yaw, colour r g b, material, collide", "local PLACEMENTS = {", *data, "}", "",
                "for _, p in ipairs(PLACEMENTS) do", '\tlocal part = Instance.new("Part")', "\tpart.Name = p[1]", "\tpart.Size = Vector3.new(p[3], p[4], p[5])",
                "\tpart.Position = Vector3.new(p[6], p[7], p[8])", "\tpart.Orientation = Vector3.new(0, p[9], 0)", "\tpart.Color = Color3.new(p[10], p[11], p[12])",
                "\tpart.Material = Enum.Material[p[13]]", "\tpart.Anchored = true", "\tpart.CanCollide = p[14]", '\tpart:SetAttribute("ModuleId", p[2])', "\tpart.Parent = root", "end"]
    else:
        data = [f'\t{{"{r["id"]}", "{r["module"]}", "{r["template"]}", {_n(i["at"][0])}, {_n(i["y"])}, {_n(i["at"][1])}, {_n(r["yaw"])}, {_n(r["scale"])}}},'
                for i, r in zip(scene["instances"], rows)]
        body = ["-- id, module, template, pivot x y z, yaw, scale", "local PLACEMENTS = {", *data, "}", "", f'local kit = resolveTarget("{kit_path}")', "",
                "for _, p in ipairs(PLACEMENTS) do", "\tlocal template = kit:FindFirstChild(p[3])",
                "\tif not template then error(\"kit template '\" .. p[3] .. \"' was not found in the kit folder\") end", "\tlocal clone = template:Clone()",
                "\tclone.Name = p[1]", "\tif p[8] ~= 1 then clone:ScaleTo(p[8]) end",
                "\tclone:PivotTo(CFrame.new(p[4], p[5], p[6]) * CFrame.Angles(0, math.rad(p[7]), 0))", '\tclone:SetAttribute("ModuleId", p[2])', "\tclone.Parent = root", "end"]
    code = "\n".join([
        f"-- GENERATED by setdress (scene {h}, {mode}) - review before running.",
        f'-- Creates/replaces ONE Folder named {root}; only a folder it created itself (AIGeneratedBy = "{MARKER}") is ever replaced.',
        "-- It does not publish, save, or make network requests.",
        f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "{root}"', "", *RESOLVE,
        "local target = resolveTarget(TARGET_PATH)", "local existing = target:FindFirstChild(ROOT_NAME)", "if existing then",
        f'\tif existing:GetAttribute("AIGeneratedBy") == "{MARKER}" then', "\t\texisting:Destroy()", "\telse",
        "\t\terror(\"refusing to replace '\" .. ROOT_NAME .. \"': it was not created by setdress\")", "\tend", "end", "",
        'local root = Instance.new("Folder")', "root.Name = ROOT_NAME", f'root:SetAttribute("AIGeneratedBy", "{MARKER}")', f'root:SetAttribute("AISceneHash", "{h}")', "",
        *body, "", "root.Parent = target", "",
        'local json = game:GetService("HttpService"):JSONEncode({status = "ok", root = root:GetFullName(), '
        f'scene_hash = "{h}", instances = {len(rows)}, mode = "{mode}"}})', "print(json)", "return json", ""])
    problems = lint(code)
    if problems:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(problems))
    return code


def build_remove_luau(scene_id: str, target_path: str = "workspace") -> str:
    if not ID_RE.match(scene_id):
        raise ValueError(f"scene id '{scene_id}' must match {ID_RE.pattern}")
    validate_path(target_path)
    code = "\n".join([f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "AI_SetDress_{scene_id}"', "", *RESOLVE,
                      "local target = resolveTarget(TARGET_PATH)", "local existing = target:FindFirstChild(ROOT_NAME)", 'local status = "not_found"', "if existing then",
                      f'\tif existing:GetAttribute("AIGeneratedBy") == "{MARKER}" then', "\t\texisting:Destroy()", '\t\tstatus = "removed"', "\telse",
                      "\t\terror(\"refusing to remove '\" .. ROOT_NAME .. \"': it was not created by setdress\")", "\tend", "end",
                      'local json = game:GetService("HttpService"):JSONEncode({status = status})', "print(json)", "return json", ""])
    problems = lint(code)
    if problems:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(problems))
    return code


def build_inspect_luau(scene_id: str, target_path: str = "workspace") -> str:
    if not ID_RE.match(scene_id):
        raise ValueError(f"scene id '{scene_id}' must match {ID_RE.pattern}")
    validate_path(target_path)
    code = "\n".join(["-- GENERATED by setdress (inspect) - read-only.", f'local TARGET_PATH = "{target_path}"', f'local ROOT_NAME = "AI_SetDress_{scene_id}"', "", *RESOLVE,
                      "local root = resolveTarget(TARGET_PATH):FindFirstChild(ROOT_NAME)", 'if not root then error("scene \'" .. ROOT_NAME .. "\' was not found") end', "local out = {}",
                      "for _, d in ipairs(root:GetChildren()) do", '\tlocal row = {name = d.Name, class = d.ClassName, module = d:GetAttribute("ModuleId")}',
                      '\tif d:IsA("BasePart") then', "\t\trow.position = {d.Position.X, d.Position.Y, d.Position.Z}", "\t\trow.size = {d.Size.X, d.Size.Y, d.Size.Z}",
                      "\t\trow.yaw = d.Orientation.Y", "\tend", "\tout[#out + 1] = row", "end",
                      'local json = game:GetService("HttpService"):JSONEncode({status = "ok", root = root:GetFullName(), scene_hash = root:GetAttribute("AISceneHash"), items = out})',
                      "print(json)", "return json", ""])
    problems = lint(code)
    if problems:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(problems))
    return code


def compare_built(scene: dict, kit: dict, report: dict, tol: float = 0.05) -> dict:
    """Primitive mode only: compare the parts in Studio with what the scene says (missing, extra, moved, rotated)."""
    want = {r["id"]: r for r in primitive_rows(scene, kit)}
    have = {i["name"]: i for i in report["items"]}
    missing, extra = sorted(set(want) - set(have)), sorted(set(have) - set(want))
    moved = []
    for k in sorted(set(want) & set(have)):
        w, h = want[k], have[k]
        if "position" not in h:
            continue
        dp = max(abs(a - b) for a, b in zip(w["position"], h["position"]))
        dy = abs(geom.angle_diff(w["yaw"], h.get("yaw", 0.0)))
        if dp > tol or dy > 0.5:
            moved.append({"id": k, "position_delta": round(dp, 3), "yaw_delta": round(dy, 2)})
    return {"in_sync": not (missing or extra or moved), "missing": missing, "extra": extra, "moved": moved, "expected": len(want), "found": len(have)}
