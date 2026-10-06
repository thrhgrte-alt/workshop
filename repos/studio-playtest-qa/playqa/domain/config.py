"""The per-place playtest configuration (``projects/<project>/<place>/playtest.yaml``, named by the registry profile key ``playtest``).

It says WHAT to test in one place: the folders that must exist, the areas that must be reachable, the remotes to probe, the economy steps, the data hooks and the
test-mode switch. It never says how to reach a game that is not open in Studio. Every path is a dotted path of plain names (validated by the shared Luau safety helpers,
because it ends up inside generated Luau). Format (all sections optional; a check is only available when its section exists)::

    version: 1
    synthetic: true
    flaky: [remotes]                   # check ids to treat as flaky for this place (re-run N times before a failure is called)
    not_flaky: [perf_snapshot]         # check ids to treat as stable although the library marks them flaky
    overrides: {settings: {boot_window_seconds: 15}, ranges: {perf_memory_increase_pct: {max: 30}}}   # explicit numbers win over learned parameters
    log_ignore: ["regex"]              # console lines to leave out of the counts (still counted as ignored)
    boot:   {expected_services: [..], expected_paths: [..], use_log_service: true}
    spawn:  {spawn_path: .., invalid_surface_names: [..], invalid_surface_attribute: Hazard}
    reachability: {spawn_path: .., hops: false, areas: [{name: Cave, path: ..}]}
    remotes: {items: [{id: BuyUpgrade, path: .., class: RemoteFunction|RemoteEvent, valid_args: [[..]], bad_inputs: [{label: .., args: [..]}],
                       generic_probes: true, reject_values: [..]}]}
    economy: {player_values: {coins: {path: leaderstats.Coins, currency: true}},
              steps: [{id: mine, action: {kind: bindable|remote_function|remote_event, path: .., args: [..], times: 3},
                       expect: {ore: {delta: 3}}}]}   # expect per value: delta | delta_min/delta_max | sign: positive|negative|zero | range: gain|spend|unchanged (the bounds are placeholders in style/style.yaml)
    data: {test_mode: {switch: .., ack_hook: ..}, save_hook: .., load_hook: .., values: {coins: {path: leaderstats.Coins, marker: 777}}}
    performance: {minutes: 5, baseline: default}

A hook named here (a BindableFunction, or a remote) is something YOUR game provides for QA: this tool cannot create it.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

from ..guide_adapter import config as gc
from ..guide_adapter import luau_safety as LS

ID = r"^[A-Za-z][A-Za-z0-9_]{0,39}$"
PATH = r"^[A-Za-z][A-Za-z0-9_]{0,39}(\.[A-Za-z][A-Za-z0-9_]{0,39})*$"
CLASSES = ("RemoteFunction", "RemoteEvent")
ACTION_KINDS = ("bindable", "remote_function", "remote_event")
_ID = re.compile(ID)

ARG = {"description": "a JSON value; {$repeat, times} and {$number} markers are allowed"}
EXPECT = {"type": "object", "additionalProperties": False, "properties": {
    "delta": {"type": "number"}, "delta_min": {"type": "number"}, "delta_max": {"type": "number"}, "sign": {"enum": ["positive", "negative", "zero"]},
    "range": {"enum": ["gain", "spend", "unchanged"]}}}

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "version": {"const": 1}, "synthetic": {"type": "boolean"},
        "flaky": {"type": "array", "items": {"type": "string"}}, "not_flaky": {"type": "array", "items": {"type": "string"}},
        "overrides": {"type": "object", "additionalProperties": False, "properties": {"settings": {"type": "object"}, "ranges": {"type": "object"}}},
        "log_ignore": {"type": "array", "items": {"type": "string"}},
        "boot": {"type": "object", "additionalProperties": False, "properties": {
            "expected_services": {"type": "array", "items": {"type": "string", "pattern": ID}}, "expected_paths": {"type": "array", "items": {"type": "string", "pattern": PATH}},
            "use_log_service": {"type": "boolean"}}},
        "spawn": {"type": "object", "additionalProperties": False, "properties": {
            "spawn_path": {"type": "string", "pattern": PATH}, "invalid_surface_names": {"type": "array", "items": {"type": "string", "pattern": ID}},
            "invalid_surface_attribute": {"type": "string", "pattern": ID}}},
        "reachability": {"type": "object", "additionalProperties": False, "required": ["areas"], "properties": {
            "spawn_path": {"type": "string", "pattern": PATH}, "hops": {"type": "boolean"},
            "areas": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["name", "path"],
                                                                 "properties": {"name": {"type": "string", "pattern": ID}, "path": {"type": "string", "pattern": PATH}}}}}},
        "remotes": {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {
            "items": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["id", "path", "class"], "properties": {
                "id": {"type": "string", "pattern": ID}, "path": {"type": "string", "pattern": PATH}, "class": {"enum": list(CLASSES)},
                "valid_args": {"type": "array", "items": {"type": "array", "items": ARG}},
                "bad_inputs": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["label", "args"], "properties": {
                    "label": {"type": "string", "pattern": ID}, "args": {"type": "array", "items": ARG}}}},
                "generic_probes": {"type": "boolean"}, "reject_values": {"type": "array", "items": {"type": ["string", "number", "boolean"]}}}}}}},
        "economy": {"type": "object", "additionalProperties": False, "required": ["player_values", "steps"], "properties": {
            "player_values": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
                "path": {"type": "string", "pattern": PATH}, "currency": {"type": "boolean"}}}},
            "steps": {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False, "required": ["id", "action", "expect"], "properties": {
                "id": {"type": "string", "pattern": ID},
                "action": {"type": "object", "additionalProperties": False, "required": ["kind", "path"], "properties": {
                    "kind": {"enum": list(ACTION_KINDS)}, "path": {"type": "string", "pattern": PATH}, "args": {"type": "array", "items": ARG},
                    "times": {"type": "integer", "minimum": 1, "maximum": 50}}},
                "expect": {"type": "object", "minProperties": 1, "additionalProperties": EXPECT}}}}}},
        "data": {"type": "object", "additionalProperties": False, "properties": {
            "test_mode": {"type": "object", "additionalProperties": False, "properties": {
                "switch": {"type": "string", "pattern": PATH}, "ack_hook": {"type": "string", "pattern": PATH}}},
            "save_hook": {"type": "string", "pattern": PATH}, "load_hook": {"type": "string", "pattern": PATH},
            "values": {"type": "object", "minProperties": 1, "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["path", "marker"], "properties": {
                "path": {"type": "string", "pattern": PATH}, "marker": {"type": ["number", "string", "boolean"]}}}}}},
        "performance": {"type": "object", "additionalProperties": False, "properties": {"minutes": {"type": "number", "minimum": 1, "maximum": 120}, "baseline": {"type": "string", "pattern": ID}}},
    },
}

SECTION_OF = {"boot": "boot", "spawn": "spawn", "reachability": "reachability", "remotes": "remotes", "economy": "economy", "data": "data", "perf": "performance"}


def _walk_args(v: Any, where: str, problems: list[str], depth: int = 0) -> None:
    if depth > 4:
        problems.append(f"{where}: nested deeper than 4 levels")
    elif isinstance(v, dict):
        if "$repeat" in v:
            if set(v) != {"$repeat", "times"} or not isinstance(v["$repeat"], str) or not isinstance(v["times"], int) or isinstance(v["times"], bool) or not 1 <= v["times"] <= 20000:
                problems.append(f"{where}: $repeat needs exactly 'times' (1-20000) and a string")
            elif any(ord(c) < 32 for c in v["$repeat"]) or len(v["$repeat"]) > 20:
                problems.append(f"{where}: $repeat string must be at most 20 printable characters")
        elif "$number" in v:
            if set(v) != {"$number"} or v["$number"] not in ("nan", "inf", "-inf"):
                problems.append(f"{where}: $number must be nan, inf or -inf")
        else:
            for k, x in v.items():
                if not isinstance(k, str) or not _ID.fullmatch(k):
                    problems.append(f"{where}: key {k!r} must match {ID}")
                else:
                    _walk_args(x, f"{where}.{k}", problems, depth + 1)
    elif isinstance(v, list):
        for i, x in enumerate(v):
            _walk_args(x, f"{where}[{i}]", problems, depth + 1)
    elif isinstance(v, str):
        if any(ord(c) < 32 for c in v) or len(v) > 200:
            problems.append(f"{where}: strings must be printable and at most 200 characters")
    elif not (v is None or isinstance(v, (bool, int, float))):
        problems.append(f"{where}: unsupported value {type(v).__name__}")


def validate(cfg: Any) -> list[str]:
    """Every problem in a playtest config (empty list = usable)."""
    if not isinstance(cfg, dict):
        return ["the playtest config must be a mapping"]
    problems = gc.validate(cfg, SCHEMA)
    if problems:
        return problems
    # semantics the JSON Schema cannot say
    for sec, getp in (("boot", lambda c: c.get("expected_paths", [])), ):
        for p in getp(cfg.get(sec, {})):
            try:
                LS.validate_path(p, "expected path")
            except ValueError as exc:
                problems.append(str(exc))
    ids = [a["name"] for a in cfg.get("reachability", {}).get("areas", [])]
    if len(ids) != len(set(ids)):
        problems.append("reachability.areas: names must be unique")
    if "spawn" in ids:
        problems.append("reachability.areas: 'spawn' is the start node's name; pick another area name")
    rid = [r["id"] for r in cfg.get("remotes", {}).get("items", [])]
    if len(rid) != len(set(rid)):
        problems.append("remotes.items: ids must be unique")
    for r in cfg.get("remotes", {}).get("items", []):
        for i, args in enumerate(r.get("valid_args", [])):
            _walk_args(args, f"remotes.{r['id']}.valid_args[{i}]", problems)
        for b in r.get("bad_inputs", []):
            _walk_args(b["args"], f"remotes.{r['id']}.bad_inputs.{b['label']}", problems)
        if r["class"] == "RemoteEvent" and r.get("reject_values"):
            problems.append(f"remotes.{r['id']}: reject_values only apply to a RemoteFunction")
    sids = [s["id"] for s in cfg.get("economy", {}).get("steps", [])]
    if len(sids) != len(set(sids)):
        problems.append("economy.steps: ids must be unique")
    vals = set(cfg.get("economy", {}).get("player_values", {}))
    for v in vals:
        if not _ID.fullmatch(v):
            problems.append(f"economy.player_values: id {v!r} must match {ID}")
    for s in cfg.get("economy", {}).get("steps", []):
        _walk_args(s["action"].get("args", []), f"economy.{s['id']}.args", problems)
        for k, e in s["expect"].items():
            if k not in vals:
                problems.append(f"economy.steps.{s['id']}.expect: '{k}' is not one of the player_values {sorted(vals)}")
            keys = set(e)
            if not keys:
                problems.append(f"economy.steps.{s['id']}.expect.{k}: give delta, delta_min/delta_max or sign")
            if "range" in keys and keys != {"range"}:
                problems.append(f"economy.steps.{s['id']}.expect.{k}: range cannot be combined with another form")
            if "delta" in keys and keys & {"delta_min", "delta_max", "sign"}:
                problems.append(f"economy.steps.{s['id']}.expect.{k}: delta cannot be combined with another form")
            if "sign" in keys and keys & {"delta_min", "delta_max"}:
                problems.append(f"economy.steps.{s['id']}.expect.{k}: sign cannot be combined with delta_min/delta_max")
            if "delta_min" in e and "delta_max" in e and e["delta_min"] > e["delta_max"]:
                problems.append(f"economy.steps.{s['id']}.expect.{k}: delta_min is above delta_max")
    dv = cfg.get("data", {}).get("values", {})
    for v in dv:
        if not _ID.fullmatch(v):
            problems.append(f"data.values: id {v!r} must match {ID}")
    return problems


def load(path: Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    problems = validate(cfg)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems[:6]) + (f" (+{len(problems) - 6} more)" if len(problems) > 6 else ""))
    return cfg


def sections(cfg: dict) -> list[str]:
    return [s for s in ("boot", "spawn", "reachability", "remotes", "economy", "data", "performance") if s in cfg]


def apply_overrides(style: dict, cfg: dict) -> dict:
    """Explicit numbers in the place's config win over learned parameters. Returns the same object when there is nothing to apply."""
    ov = cfg.get("overrides") or {}
    if not ov.get("settings") and not ov.get("ranges"):
        return style
    out = copy.deepcopy(style)
    for k, v in (ov.get("settings") or {}).items():
        if k not in out.get("settings", {}):
            raise ValueError(f"overrides.settings: unknown setting '{k}'. Known: {sorted(out.get('settings', {}))}")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"overrides.settings.{k} must be a number")
        e = out["settings"][k]
        if not e["min"] <= v <= e["max"]:
            raise ValueError(f"overrides.settings.{k}={v} is outside its range [{e['min']}, {e['max']}]")
        e["value"] = v
        e["source"] = "playtest.yaml"
    for k, spec in (ov.get("ranges") or {}).items():
        if k not in out.get("ranges", {}):
            raise ValueError(f"overrides.ranges: unknown range '{k}'. Known: {sorted(out.get('ranges', {}))}")
        if out["ranges"][k].get("locked"):
            raise ValueError(f"overrides.ranges.{k} is locked and cannot be overridden")
        for bound, v in (spec or {}).items():
            if bound not in ("min", "max") or isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"overrides.ranges.{k}: only numeric min/max are allowed")
            out["ranges"][k][bound] = v
        out["ranges"][k]["source"] = "playtest.yaml"
    return out
