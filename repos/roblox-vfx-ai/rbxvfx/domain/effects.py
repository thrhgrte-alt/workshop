"""Effect recipes -> typed, validated effect plans.

A recipe describes an instance tree (Attachments, ParticleEmitters, Beams, Trails, PointLights) with
named, range-checked parameters. ``build_plan`` resolves parameters and encodes every property into a
typed value (``{"t": "NumberSequence", "keys": [...]}``) according to the Roblox API snapshot.
``validate_plan`` then checks classes, property names, value types, enum members and Roblox's own
structural rules (sequences must start at 0 and end at 1, ranges ordered, ...). The same validator is used
on trees read back from a live Studio session, so "what I intended" and "what exists" are checked alike.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml

from . import api

EFFECT_KINDS = ("burst", "loop", "beam", "trail", "impact", "aura", "projectile", "ambient")
MOUNTS = ("part", "attachment")
OMIT = object()
TEXTURE_RE = re.compile(r"^(rbxassetid://\d+|rbxasset://[\w./-]+|https?://www\.roblox\.com/asset/\?id=\d+)$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# Sanity bounds. "min"/"max" are hard (invalid values); "warn_*" are soft heuristics, not Roblox limits.
NUMERIC = {
    ("ParticleEmitter", "Rate"): {"min": 0, "warn_max": 300},
    ("ParticleEmitter", "Lifetime"): {"min": 0, "warn_max": 10},
    ("ParticleEmitter", "Speed"): {"warn_max": 120},
    ("ParticleEmitter", "SpreadAngle"): {"min": 0, "max": 180},
    ("ParticleEmitter", "LightEmission"): {"min": 0, "max": 1},
    ("ParticleEmitter", "LightInfluence"): {"min": 0, "max": 1},
    ("ParticleEmitter", "Transparency"): {"min": 0, "max": 1},
    ("ParticleEmitter", "Size"): {"min": 0, "warn_max": 40},
    ("ParticleEmitter", "Drag"): {"min": 0},
    ("Beam", "Transparency"): {"min": 0, "max": 1},
    ("Beam", "Width0"): {"min": 0, "warn_max": 30},
    ("Beam", "Width1"): {"min": 0, "warn_max": 30},
    ("Beam", "Segments"): {"min": 1, "warn_max": 40},
    ("Beam", "LightEmission"): {"min": 0, "max": 1},
    ("Beam", "LightInfluence"): {"min": 0, "max": 1},
    ("Trail", "Lifetime"): {"min": 0, "warn_max": 4},
    ("Trail", "Transparency"): {"min": 0, "max": 1},
    ("Trail", "WidthScale"): {"min": 0, "warn_max": 20},
    ("Trail", "LightEmission"): {"min": 0, "max": 1},
    ("PointLight", "Range"): {"min": 0, "max": 60},
    ("PointLight", "Brightness"): {"min": 0, "warn_max": 8},
}
SEQUENCE_TYPES = ("NumberSequence", "ColorSequence")


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


# --- recipes + parameters ------------------------------------------------------------------------------------
def load_recipe(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError(f"{path}: recipe must be a mapping with an 'id'")
    return data


def list_recipes(directory: Path) -> list[dict]:
    out = []
    for f in sorted(Path(directory).glob("*.yaml")):
        r = load_recipe(f)
        out.append({"id": r["id"], "title": r.get("title", r["id"]), "kind": r.get("kind"),
                    "description": " ".join(r.get("description", "").split()), "file": f.name,
                    "mount": r.get("mount", "part"), "parameters": dict(r.get("parameters", {}))})
    return out


def _check_param(spec: dict, value: Any, name: str) -> list[str]:
    t = spec["type"]
    if t in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{name}: expected {t}, got {value!r}"]
        errs = []
        if t == "int" and int(value) != value:
            errs.append(f"{name}: expected an integer, got {value}")
        if "min" in spec and value < spec["min"]:
            errs.append(f"{name}: {value} is below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            errs.append(f"{name}: {value} is above maximum {spec['max']}")
        return errs
    if t == "bool":
        return [] if isinstance(value, bool) else [f"{name}: expected true/false, got {value!r}"]
    if t == "enum":
        return [] if value in spec.get("values", []) else [f"{name}: {value!r} is not one of {spec.get('values')}"]
    if t == "color":
        ok = isinstance(value, list) and len(value) == 3 and all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in value)
        return [] if ok else [f"{name}: expected [r, g, b] with values 0-1, got {value!r}"]
    if t == "string":
        return [] if isinstance(value, str) else [f"{name}: expected a string, got {value!r}"]
    return [f"{name}: unsupported parameter type '{t}'"]


def resolve_parameters(recipe: dict, overrides: dict | None = None) -> dict:
    declared = recipe.get("parameters", {})
    overrides = overrides or {}
    errors = [f"unknown parameter '{k}'. Available: {sorted(declared)}" for k in overrides if k not in declared]
    values = {}
    for name, spec in declared.items():
        value = overrides.get(name, spec.get("default"))
        if value is None:
            errors.append(f"{name}: no value and no default")
            continue
        errors.extend(_check_param(spec, value, name))
        values[name] = value
    if errors:
        raise ValueError("invalid parameters: " + "; ".join(errors))
    return values


# --- value encoding ---------------------------------------------------------------------------------------------
def _ref(raw: Any, params: dict) -> Any:
    if isinstance(raw, dict) and "from" in raw:
        if raw["from"] not in params:
            raise ValueError(f"parameter '{raw['from']}' is not declared")
        v = params[raw["from"]]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v * raw.get("scale", 1) + raw.get("offset", 0)
        return v
    return raw


def _num(raw: Any, params: dict, where: str) -> float:
    v = _ref(raw, params)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{where}: expected a number, got {v!r}")
    return float(v)


def _color(raw: Any, params: dict, where: str) -> list[float]:
    v = _ref(raw, params)
    if isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return [int(v[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    if isinstance(v, list) and len(v) in (3, 4) and all(isinstance(c, (int, float)) for c in v):
        return [float(c) for c in v[:3]]
    raise ValueError(f"{where}: expected a colour ([r,g,b] 0-1 or '#rrggbb'), got {v!r}")


def encode(prop_type: str, raw: Any, params: dict, where: str) -> Any:
    if prop_type == "float":
        return _num(raw, params, where)
    if prop_type == "int":
        return int(round(_num(raw, params, where)))
    if prop_type == "boolean":
        v = _ref(raw, params)
        if not isinstance(v, bool):
            raise ValueError(f"{where}: expected true/false, got {v!r}")
        return v
    if prop_type == "string":
        return str(_ref(raw, params))
    if prop_type in ("ContentId",):
        v = _ref(raw, params)
        return OMIT if v in ("", None) else str(v)
    if prop_type == "NumberRange":
        v = _ref(raw, params)
        pair = v if isinstance(v, list) else [v, v]
        if len(pair) != 2:
            raise ValueError(f"{where}: NumberRange needs 1 or 2 numbers")
        return {"t": "NumberRange", "min": _num(pair[0], params, where), "max": _num(pair[1], params, where)}
    if prop_type == "NumberSequence":
        v = raw["sequence"] if isinstance(raw, dict) and "sequence" in raw else _ref(raw, params)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"t": "NumberSequence", "keys": [[0.0, float(v), 0.0], [1.0, float(v), 0.0]]}
        keys = []
        for k in v:
            if not isinstance(k, list) or len(k) not in (2, 3):
                raise ValueError(f"{where}: sequence keys must be [time, value] or [time, value, envelope]")
            keys.append([_num(k[0], params, where), _num(k[1], params, where), _num(k[2], params, where) if len(k) == 3 else 0.0])
        return {"t": "NumberSequence", "keys": keys}
    if prop_type == "ColorSequence":
        v = raw["gradient"] if isinstance(raw, dict) and "gradient" in raw else raw
        if isinstance(v, list) and v and isinstance(v[0], list) and len(v[0]) == 2 and isinstance(v[0][1], (list, str, dict)):
            return {"t": "ColorSequence", "keys": [[_num(t, params, where), _color(c, params, where)] for t, c in v]}
        c = _color(v, params, where)
        return {"t": "ColorSequence", "keys": [[0.0, c], [1.0, c]]}
    if prop_type == "Color3":
        return {"t": "Color3", "rgb": _color(raw, params, where)}
    if prop_type in ("Vector2", "Vector3"):
        v = _ref(raw, params)
        n = 2 if prop_type == "Vector2" else 3
        if not (isinstance(v, list) and len(v) == n):
            raise ValueError(f"{where}: {prop_type} needs {n} numbers")
        return {"t": prop_type, "v": [_num(x, params, where) for x in v]}
    members = api.enum_values(prop_type)
    if members is not None:
        v = _ref(raw, params)
        return {"t": "Enum", "enum": prop_type, "name": v}
    raise ValueError(f"{where}: property type '{prop_type}' is not supported by recipes")


# --- plan -------------------------------------------------------------------------------------------------------------
def plan_hash(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:12]


def build_plan(recipe: dict, overrides: dict | None = None, *, name: str | None = None,
               snapshot: dict | None = None) -> dict:
    """Resolve parameters and encode every property. Raises ValueError listing all problems found."""
    snap = snapshot or api.load()
    params = resolve_parameters(recipe, overrides)
    name = name or recipe.get("effect_name", recipe["id"])
    problems: list[str] = []
    instances = []
    for spec in recipe.get("instances", []):
        cls = spec["class"]
        props: dict[str, Any] = {}
        refs: dict[str, str] = {}
        for pname, raw in spec.get("properties", {}).items():
            ptype = api.property_type(cls, pname, snap)
            where = f"{spec['id']}.{pname}"
            if ptype is None:
                problems.append(f"{where}: '{cls}' has no property '{pname}' in the API snapshot")
                continue
            if ptype == "Attachment":
                refs[pname] = raw
                continue
            try:
                value = encode(ptype, raw, params, where)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            if value is not OMIT:
                props[pname] = value
        instances.append({"id": spec["id"], "class": cls, "parent": spec.get("parent", "root"),
                          "name": spec.get("name", spec["id"]), "properties": props, "refs": refs})
    emit: dict[str, int] = {}
    for iid, raw in (recipe.get("emit") or {}).items():
        try:
            emit[iid] = max(0, int(round(_num(raw, params, f"emit.{iid}"))))
        except ValueError as exc:
            problems.append(str(exc))
    if problems:
        raise ValueError("effect recipe could not be built: " + "; ".join(problems))
    plan = {"recipe": recipe["id"], "name": name, "root_name": f"AIEffect_{name}", "kind": recipe.get("kind", "loop"),
            "mount": recipe.get("mount", "part"), "parameters": params, "instances": instances, "emit": emit}
    plan["duration_estimate"] = estimate_duration(plan)
    findings = validate_plan(plan, snap)
    errors = [f for f in findings if f["severity"] == "error"]
    if errors:
        raise ValueError("effect plan is invalid: " + "; ".join(f"[{e['code']}] {e['message']}" for e in errors))
    plan["warnings"] = [f["message"] for f in findings if f["severity"] == "warning"]
    return plan


def estimate_duration(plan: dict) -> float | None:
    """Seconds until a one-shot effect has fully faded; None for looping effects."""
    if plan["kind"] in ("loop", "aura", "ambient", "beam", "trail", "projectile"):
        return None
    best = 0.0
    for inst in plan["instances"]:
        if inst["class"] == "ParticleEmitter" and inst["id"] in plan["emit"]:
            lt = inst["properties"].get("Lifetime")
            best = max(best, lt["max"] if lt else 0.0)
    return round(best, 3)


# --- validation -----------------------------------------------------------------------------------------------------
def _value_matches(ptype: str, value: Any) -> str | None:
    """Return an error string when an encoded value does not fit the property type."""
    if ptype == "float":
        return None if isinstance(value, (int, float)) and not isinstance(value, bool) else "expected a number"
    if ptype == "int":
        return None if isinstance(value, int) and not isinstance(value, bool) else "expected an integer"
    if ptype == "boolean":
        return None if isinstance(value, bool) else "expected a boolean"
    if ptype in ("string", "ContentId"):
        return None if isinstance(value, str) else "expected a string"
    members = api.enum_values(ptype)
    if members is not None:
        if not (isinstance(value, dict) and value.get("t") == "Enum" and value.get("enum") == ptype):
            return f"expected an Enum.{ptype} member"
        return None if value["name"] in members else f"'{value['name']}' is not a member of Enum.{ptype} ({members})"
    if not (isinstance(value, dict) and value.get("t") == ptype):
        return f"expected a {ptype}"
    return None


def _seq_findings(ptype: str, key: str, value: dict, where: str) -> list[dict]:
    f = []
    keys = value["keys"]
    if len(keys) < 2:
        f.append(finding("error", "sequence_length", f"{where}: a {ptype} needs at least 2 keypoints", where))
        return f
    times = [k[0] for k in keys]
    if times[0] != 0 or times[-1] != 1:
        f.append(finding("error", "sequence_bounds", f"{where}: first keypoint must be at time 0 and last at time 1 (got {times[0]}..{times[-1]})", where))
    if any(b < a for a, b in zip(times, times[1:])) or any(b == a for a, b in zip(times, times[1:])):
        f.append(finding("error", "sequence_order", f"{where}: keypoint times must be strictly increasing", where))
    if ptype == "ColorSequence":
        for t, rgb in keys:
            if not all(0 <= c <= 1 for c in rgb):
                f.append(finding("error", "color_range", f"{where}: colour components must be 0-1", where))
    return f


def validate_plan(plan: dict, snapshot: dict | None = None) -> list[dict]:
    snap = snapshot or api.load()
    f: list[dict] = []
    ids = [i["id"] for i in plan["instances"]]
    if len(ids) != len(set(ids)):
        f.append(finding("error", "duplicate_id", "instance ids must be unique"))
    if not NAME_RE.match(plan["name"]):
        f.append(finding("error", "bad_name", f"effect name '{plan['name']}' must match {NAME_RE.pattern}"))
    if plan["kind"] not in EFFECT_KINDS:
        f.append(finding("error", "bad_kind", f"kind must be one of {EFFECT_KINDS}"))
    if plan["mount"] not in MOUNTS:
        f.append(finding("error", "bad_mount", f"mount must be one of {MOUNTS}"))
    by_id = {i["id"]: i for i in plan["instances"]}
    siblings: dict[str, list[str]] = {}
    for inst in plan["instances"]:
        iid, cls = inst["id"], inst["class"]
        if cls not in api.ALLOWED_CLASSES:
            f.append(finding("error", "class_not_allowed", f"class '{cls}' is not allowed; use one of {api.ALLOWED_CLASSES}", iid))
            continue
        parent = inst["parent"]
        if parent != "root" and parent not in by_id:
            f.append(finding("error", "unknown_parent", f"'{iid}' has unknown parent '{parent}'", iid))
        siblings.setdefault(parent, []).append(inst["name"])
        if cls == "Attachment" and plan["mount"] != "part":
            f.append(finding("error", "attachment_mount", "Attachment instances need mount: part (attachments must live in a BasePart)", iid))
        known = api.properties(cls, snap)
        for pname, value in inst["properties"].items():
            info = known.get(pname)
            where = f"{iid}.{pname}"
            if info is None:
                f.append(finding("error", "unknown_property", f"'{cls}' has no property '{pname}'", where))
                continue
            if info["deprecated"]:
                f.append(finding("warning", "deprecated_property", f"{where} is deprecated", where))
            bad = _value_matches(info["type"], value)
            if bad:
                f.append(finding("error", "type_mismatch", f"{where}: {bad}", where))
                continue
            if info["type"] in SEQUENCE_TYPES:
                f.extend(_seq_findings(info["type"], pname, value, where))
            if info["type"] == "NumberRange" and value["min"] > value["max"]:
                f.append(finding("error", "range_order", f"{where}: min {value['min']} is greater than max {value['max']}", where))
            if pname == "Texture" and value and not TEXTURE_RE.match(value):
                f.append(finding("error", "bad_texture", f"{where}: '{value}' is not an rbxassetid://, rbxasset:// or roblox.com/asset URL", where))
            rule = NUMERIC.get((cls, pname))
            if rule:
                nums = _numbers(info["type"], value)
                lo, hi = min(nums), max(nums)
                if "min" in rule and lo < rule["min"]:
                    f.append(finding("error", "out_of_range", f"{where}: {lo} is below the minimum {rule['min']}", where))
                if "max" in rule and hi > rule["max"]:
                    f.append(finding("error", "out_of_range", f"{where}: {hi} is above the maximum {rule['max']}", where))
                if "warn_max" in rule and hi > rule["warn_max"]:
                    f.append(finding("warning", "unusually_large", f"{where}: {hi} is unusually large (> {rule['warn_max']}); check the budget", where))
        if cls in ("Beam", "Trail"):
            if plan["mount"] != "part":
                f.append(finding("error", "attachment_mount", f"{cls} needs mount: part so it can use two Attachments", iid))
            a0, a1 = inst["refs"].get("Attachment0"), inst["refs"].get("Attachment1")
            for label, ref in (("Attachment0", a0), ("Attachment1", a1)):
                if ref is None:
                    f.append(finding("error", "missing_attachment", f"{cls} '{iid}' needs {label}", iid))
                elif ref not in by_id or by_id[ref]["class"] != "Attachment":
                    f.append(finding("error", "bad_attachment", f"{iid}.{label} must refer to an Attachment instance (got '{ref}')", iid))
            if a0 is not None and a0 == a1:
                f.append(finding("error", "same_attachment", f"{iid}: Attachment0 and Attachment1 must differ", iid))
    for parent, names in siblings.items():
        if len(names) != len(set(names)):
            f.append(finding("error", "duplicate_name", f"siblings under '{parent}' share a name", parent))
    for iid, count in plan.get("emit", {}).items():
        if iid not in by_id or by_id[iid]["class"] != "ParticleEmitter":
            f.append(finding("error", "bad_emit", f"emit target '{iid}' is not a ParticleEmitter", iid))
        elif count > 500:
            f.append(finding("warning", "unusually_large", f"emit count {count} for '{iid}' is very large", iid))
    if not any(i["class"] in ("ParticleEmitter", "Beam", "Trail") for i in plan["instances"]):
        f.append(finding("error", "nothing_visible", "effect has no ParticleEmitter, Beam or Trail"))
    return f


def _numbers(ptype: str, value: Any) -> list[float]:
    if ptype == "NumberSequence":
        return [k[1] for k in value["keys"]]
    if ptype == "NumberRange":
        return [value["min"], value["max"]]
    if ptype in ("Vector2", "Vector3"):
        return [float(c) for c in value["v"]]
    return [float(value)] if isinstance(value, (int, float)) and not isinstance(value, bool) else [0.0]
