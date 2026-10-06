"""Node catalog loading and the (optional) result of probing a real Designer install."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SLOT_TYPES = ("color", "gray", "any")
PARAM_TYPES = ("float", "int", "bool", "enum", "color")


@dataclass(frozen=True)
class Slot:
    id: str
    type: str
    required: bool = False
    follows: str | None = None


@dataclass(frozen=True)
class Param:
    id: str
    type: str
    default: object = None
    min: float | None = None
    max: float | None = None
    values: dict | None = None


@dataclass(frozen=True)
class NodeSpec:
    key: str
    source: str  # atomic | library
    definition: str | None  # atomic: sbs::compositing::xxx
    label: str | None  # library: display label looked up at runtime
    category: str
    inputs: tuple
    outputs: tuple
    params: dict = field(default_factory=dict)
    verified: bool = False

    def input(self, slot_id: str) -> Slot | None:
        return next((s for s in self.inputs if s.id == slot_id), None)

    def output(self, slot_id: str) -> Slot | None:
        return next((s for s in self.outputs if s.id == slot_id), None)


def _slot(raw: dict) -> Slot:
    if raw["type"] not in SLOT_TYPES:
        raise ValueError(f"slot '{raw['id']}': type must be one of {SLOT_TYPES}")
    return Slot(raw["id"], raw["type"], bool(raw.get("required", False)), raw.get("follows"))


def load_catalog(path: Path) -> dict[str, NodeSpec]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    catalog: dict[str, NodeSpec] = {}
    for key, raw in data["nodes"].items():
        if raw["source"] not in ("atomic", "library"):
            raise ValueError(f"{key}: source must be atomic|library")
        if raw["source"] == "atomic" and not raw.get("definition"):
            raise ValueError(f"{key}: atomic nodes need a 'definition'")
        if raw["source"] == "library" and not raw.get("label"):
            raise ValueError(f"{key}: library nodes need a 'label'")
        params = {}
        for pid, p in raw.get("params", {}).items():
            if p["type"] not in PARAM_TYPES:
                raise ValueError(f"{key}.{pid}: type must be one of {PARAM_TYPES}")
            params[pid] = Param(pid, p["type"], p.get("default"), p.get("min"), p.get("max"), p.get("values"))
        catalog[key] = NodeSpec(
            key=key, source=raw["source"], definition=raw.get("definition"), label=raw.get("label"),
            category=raw.get("category", "filter"),
            inputs=tuple(_slot(s) for s in raw.get("inputs", [])),
            outputs=tuple(_slot(s) for s in raw.get("outputs", [])),
            params=params, verified=bool(raw.get("verified", False)),
        )
    return catalog


def load_probe(path: Path | None) -> dict | None:
    """The JSON written by the probe script when run inside Designer (or None)."""
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def diff_against_probe(catalog: dict[str, NodeSpec], probe: dict) -> dict:
    """Compare the draft catalog with what a real Designer reported.

    ``probe['atomic']``  : {definition: {"inputs": [ids], "params": [ids], "outputs": [ids]} | {"error": str}}
    ``probe['library']`` : {label: {"inputs": [...], "params": [...], "outputs": [...]} | None when not found}
    """
    problems, ok = [], []
    for key, spec in catalog.items():
        info = probe["atomic"].get(spec.definition) if spec.source == "atomic" else probe["library"].get(spec.label)
        where = spec.definition or spec.label
        if not info or "error" in info:
            problems.append({"node": key, "issue": "not creatable/found on this install", "detail": (info or {}).get("error", where)})
            continue
        node_problems = []
        for kind, declared in (("inputs", [s.id for s in spec.inputs]), ("outputs", [s.id for s in spec.outputs]),
                               ("params", list(spec.params))):
            have = set(info.get(kind, []))
            for d in declared:
                if d not in have:
                    node_problems.append({"kind": kind, "missing": d, "available": sorted(have)})
        if node_problems:
            problems.append({"node": key, "issue": "ids differ from this install", "detail": node_problems})
        else:
            ok.append(key)
    return {"designer_version": probe.get("designer_version"), "verified_ok": ok, "problems": problems,
            "clean": not problems}
