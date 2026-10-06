"""Kit metadata: modules with footprint, pivot, sockets, tags and compatibility, plus search and a metadata report.

The kit is the approved asset set. Placement tools only use modules from it and, when a module is unknown, suggest the
nearest approved ones instead of inventing a replacement. Metadata the kit does not state is *defaulted* and listed by
``report`` as unresolved, so missing information is visible rather than silently assumed.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import yaml

from setdress.core.retrieval import bm25_scores, tokens

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
PIVOTS = ("bottom_center", "center", "corner")
DEFAULTS = {"pivot": "bottom_center", "collision": True, "priority": 0, "tags": [], "sockets": [], "scale": {"min": 1.0, "max": 1.0}}
TRACKED = ("pivot", "collision", "material", "color", "tags", "scale")


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


def size_class(m: dict) -> str:
    big = max(m["footprint"])
    if m["height"] >= 8 or big >= 12:
        return "large"
    if m["height"] < 3 and big < 4:
        return "small"
    return "medium"


def load_kit(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "modules" not in data:
        raise ValueError(f"{path}: kit file needs 'modules'")
    return data


def normalize_kit(kit: dict) -> tuple[dict, list[dict]]:
    """Fill defaults; remember which fields were defaulted (``_defaulted``). Returns (kit, findings)."""
    out = {**kit, "modules": []}
    kit_defaults = kit.get("defaults", {})
    f: list[dict] = []
    ids = [m.get("id") for m in kit["modules"]]
    for dup in {i for i in ids if ids.count(i) > 1}:
        f.append(finding("error", "duplicate_id", f"module id '{dup}' is used twice", dup))
    socket_types: set[str] = set()
    for raw in kit["modules"]:
        m = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in {**kit_defaults, **raw}.items()}
        mid = str(m.get("id"))
        defaulted = [k for k in TRACKED if k not in raw and k not in kit_defaults]
        if not ID_RE.match(mid):
            f.append(finding("error", "bad_id", f"module id '{mid}' must match {ID_RE.pattern}", mid))
        fp = m.get("footprint")
        if not (isinstance(fp, list) and len(fp) == 2 and all(isinstance(v, (int, float)) and v > 0 for v in fp)):
            f.append(finding("error", "bad_footprint", f"module '{mid}' needs footprint [width, depth] > 0", mid))
            continue
        h = m.get("height")
        if not isinstance(h, (int, float)) or h <= 0:
            f.append(finding("error", "bad_height", f"module '{mid}' needs a height > 0", mid))
            continue
        for k, v in DEFAULTS.items():
            m.setdefault(k, list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
        m.setdefault("title", mid)
        if m["pivot"] not in PIVOTS:
            f.append(finding("error", "bad_pivot", f"module '{mid}' pivot must be one of {PIVOTS}", mid))
        sc = m["scale"]
        if sc["min"] > sc["max"] or sc["min"] <= 0:
            f.append(finding("error", "bad_scale", f"module '{mid}' scale range is invalid", mid))
        if not 0 <= m["priority"] <= 5:
            f.append(finding("error", "bad_priority", f"module '{mid}' priority must be 0-5", mid))
        if m.get("color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", m["color"]):
            f.append(finding("error", "bad_color", f"module '{mid}' color must be '#rrggbb'", mid))
        m["footprint_offset"] = [fp[0] / 2, fp[1] / 2] if m["pivot"] == "corner" else [0.0, 0.0]
        m["y_offset"] = -h / 2 if m["pivot"] == "center" else 0.0
        names = set()
        good_sockets = []
        for s in m["sockets"]:
            if s.get("name") in names:
                f.append(finding("error", "duplicate_socket", f"module '{mid}' has two sockets named '{s.get('name')}'", mid))
            names.add(s.get("name"))
            if not (isinstance(s.get("pos"), list) and len(s["pos"]) == 3 and isinstance(s.get("yaw", 0), (int, float))) or not s.get("type") or not s.get("name"):
                f.append(finding("error", "bad_socket", f"module '{mid}' socket '{s.get('name')}' needs name, pos [x,y,z], yaw and type", mid))
                continue
            s.setdefault("yaw", 0)
            s.setdefault("mates", [s["type"]])
            socket_types.add(s["type"])
            good_sockets.append(s)
        m["sockets"] = good_sockets
        m["size_class"] = size_class(m)
        m["_defaulted"] = defaulted
        if not m["tags"]:
            f.append(finding("warning", "no_tags", f"module '{mid}' has no tags; it cannot be found or ruled by tag", mid))
        out["modules"].append(m)
    for m in out["modules"]:
        for s in m["sockets"]:
            for t in s.get("mates", []):
                if t not in socket_types:
                    f.append(finding("warning", "orphan_socket_type", f"module '{m['id']}' socket '{s['name']}' mates with type '{t}' that no module offers", m["id"]))
    return out, f


def index(kit: dict) -> dict[str, dict]:
    return {m["id"]: m for m in kit["modules"]}


def get(kit: dict, module_id: str) -> dict:
    mods = index(kit)
    if module_id not in mods:
        close = difflib.get_close_matches(module_id, list(mods), n=3, cutoff=0.5)
        raise ValueError(f"module '{module_id}' is not in the approved kit." + (f" Did you mean: {close}?" if close else "")
                         + " Use search_kit to find an approved module instead of creating a replacement.")
    return mods[module_id]


def _doc(m: dict) -> list[str]:
    parts = [m["id"], m.get("title", ""), " ".join(m["tags"]) * 3, m.get("material", ""), m["size_class"], " ".join(s["type"] for s in m["sockets"])]
    return tokens(" ".join(parts))


def search(kit: dict, query: str = "", *, tags_all=(), tags_any=(), max_footprint: tuple | None = None, max_height: float | None = None,
           socket_type: str | None = None, size_class_: str | None = None, k: int = 8) -> list[dict]:
    mods = [m for m in kit["modules"]
            if set(tags_all) <= set(m["tags"]) and (not tags_any or set(tags_any) & set(m["tags"]))
            and (max_footprint is None or (m["footprint"][0] <= max_footprint[0] and m["footprint"][1] <= max_footprint[1]))
            and (max_height is None or m["height"] <= max_height)
            and (socket_type is None or any(s["type"] == socket_type for s in m["sockets"]))
            and (size_class_ is None or m["size_class"] == size_class_)]
    scores = bm25_scores(query, [_doc(m) for m in mods]) if query else [0.0] * len(mods)
    ranked = sorted(zip(scores, mods), key=lambda t: (-t[0], t[1]["id"]))
    out = []
    for s, m in ranked:
        if query and s <= 0:
            continue
        out.append({"id": m["id"], "title": m["title"], "score": round(s, 3), "footprint": m["footprint"], "height": m["height"], "tags": m["tags"],
                    "size_class": m["size_class"], "material": m.get("material"), "priority": m["priority"],
                    "sockets": [f"{s_['name']}:{s_['type']}" for s_ in m["sockets"]], "template": m.get("template")})
    return out[:k]


def report(kit: dict) -> dict:
    """Which metadata is missing (defaulted) per module, and kit-wide statistics."""
    unresolved = {m["id"]: m["_defaulted"] for m in kit["modules"] if m["_defaulted"]}
    total = len(kit["modules"])
    return {"modules": total, "unresolved": unresolved,
            "fully_specified": sorted(m["id"] for m in kit["modules"] if not m["_defaulted"]),
            "by_size_class": {c: sum(1 for m in kit["modules"] if m["size_class"] == c) for c in ("large", "medium", "small")},
            "socket_types": sorted({s["type"] for m in kit["modules"] for s in m["sockets"]}),
            "tags": sorted({t for m in kit["modules"] for t in m["tags"]}),
            "note": "Defaulted fields are assumptions: pivot=bottom_center, collision=true, scale fixed at 1. State kit-wide defaults under 'defaults:' to make them explicit."}
