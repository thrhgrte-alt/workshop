"""Recipes -> validated graph plans.

A *recipe* is a declarative node graph with named, range-checked parameters. The model chooses a
recipe and parameter values (interpretation and judgment); this module does the exact work
(parameter validation, connection checks, ordering) so invalid graphs are rejected *before*
anything is sent to Designer.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .catalog import NodeSpec

USAGES = {
    "baseColor": "color", "normal": "color", "emissive": "color",
    "roughness": "gray", "metallic": "gray", "height": "gray", "ambientOcclusion": "gray", "opacity": "gray",
}
GRAPH_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
RESOLUTIONS = (128, 256, 512, 1024, 2048, 4096)


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


def load_recipe(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError(f"{path}: recipe must be a mapping with an 'id'")
    return data


def list_recipes(directory: Path) -> list[dict]:
    out = []
    for f in sorted(Path(directory).glob("*.yaml")):
        if f.name == "node_catalog.yaml":
            continue
        r = load_recipe(f)
        out.append({"id": r["id"], "title": r.get("title", r["id"]), "material_type": r.get("material_type"),
                    "description": r.get("description", ""), "file": f.name,
                    "parameters": dict(r.get("parameters", {}))})
    return out


# --- parameters ------------------------------------------------------------------------------------
def _check_value(spec: dict, value: Any, name: str) -> list[str]:
    t = spec["type"]
    errs = []
    if t in ("float", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{name}: expected {t}, got {value!r}"]
        if t == "int" and int(value) != value:
            errs.append(f"{name}: expected an integer, got {value}")
        if "min" in spec and value < spec["min"]:
            errs.append(f"{name}: {value} is below minimum {spec['min']}")
        if "max" in spec and value > spec["max"]:
            errs.append(f"{name}: {value} is above maximum {spec['max']}")
    elif t == "bool":
        if not isinstance(value, bool):
            errs.append(f"{name}: expected true/false, got {value!r}")
    elif t == "enum":
        if value not in spec.get("values", []):
            errs.append(f"{name}: {value!r} is not one of {spec.get('values')}")
    elif t == "color":
        ok = isinstance(value, list) and len(value) in (3, 4) and all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in value)
        if not ok:
            errs.append(f"{name}: expected [r,g,b(,a)] with values 0-1, got {value!r}")
    else:
        errs.append(f"{name}: unsupported parameter type '{t}'")
    return errs


def resolve_parameters(recipe: dict, overrides: dict | None = None) -> dict:
    """Defaults + overrides, fully validated. Raises ValueError listing every problem."""
    declared = recipe.get("parameters", {})
    overrides = overrides or {}
    errors = [f"unknown parameter '{k}'. Available: {sorted(declared)}" for k in overrides if k not in declared]
    values = {}
    for name, spec in declared.items():
        value = overrides.get(name, spec.get("default"))
        if value is None:
            errors.append(f"{name}: no value and no default")
            continue
        errors.extend(_check_value(spec, value, name))
        values[name] = value
    if errors:
        raise ValueError("invalid parameters: " + "; ".join(errors))
    return values


def _eval_param(value: Any, params: dict) -> Any:
    if isinstance(value, dict) and "from" in value:
        base = params[value["from"]]
        if isinstance(base, (int, float)) and not isinstance(base, bool):
            return base * value.get("scale", 1) + value.get("offset", 0)
        return base
    return value


def _split(ref: str) -> tuple[str, str]:
    node, _, slot = ref.partition(".")
    return node, slot


# --- validation ---------------------------------------------------------------------------------------
def validate_recipe(recipe: dict, catalog: dict[str, NodeSpec], *, required_outputs: tuple = (),
                    graph_name: str | None = None) -> list[dict]:
    f: list[dict] = []
    nodes = {}
    for n in recipe.get("nodes", []):
        if n["id"] in nodes:
            f.append(finding("error", "duplicate_node", f"node id '{n['id']}' is used twice", n["id"]))
        nodes[n["id"]] = n
    declared = recipe.get("parameters", {})

    name = graph_name or recipe.get("graph_name", recipe["id"])
    if not GRAPH_NAME_RE.match(name):
        f.append(finding("error", "bad_graph_name", f"graph name '{name}' must match {GRAPH_NAME_RE.pattern}"))
    if recipe.get("resolution", 1024) not in RESOLUTIONS:
        f.append(finding("error", "bad_resolution", f"resolution must be one of {RESOLUTIONS}"))

    for pname, spec in declared.items():  # defaults must satisfy their own constraints
        if "default" in spec:
            for e in _check_value(spec, spec["default"], pname):
                f.append(finding("error", "bad_default", e, pname))

    for nid, n in nodes.items():
        spec = catalog.get(n["node"])
        if spec is None:
            f.append(finding("error", "unknown_node", f"node type '{n['node']}' is not in the catalog", nid))
            continue
        if not spec.verified:
            f.append(finding("warning", "unverified_node",
                             f"'{n['node']}' ({spec.definition or spec.label}) is not verified on a real install; "
                             "run the probe (README: Verify on your install)", nid))
        for pid, val in n.get("params", {}).items():
            p = spec.params.get(pid)
            if p is None:
                f.append(finding("error", "unknown_param", f"'{n['node']}' has no parameter '{pid}'. Known: {sorted(spec.params)}", nid))
                continue
            if isinstance(val, dict) and "from" in val:
                src = declared.get(val["from"])
                if src is None:
                    f.append(finding("error", "unknown_param_ref", f"parameter '{val['from']}' is not declared by the recipe", nid))
                    continue
                lo, hi = (src.get("min"), src.get("max"))
                if p.min is not None and lo is not None and isinstance(lo, (int, float)):
                    s, o = val.get("scale", 1), val.get("offset", 0)
                    ends = sorted([lo * s + o, hi * s + o]) if hi is not None else [lo * s + o]
                    if ends[0] < p.min - 1e-9 or (p.max is not None and ends[-1] > p.max + 1e-9):
                        f.append(finding("error", "param_range",
                                         f"recipe parameter '{val['from']}' can drive '{pid}' to {ends} but the node allows [{p.min}, {p.max}]", nid))
            else:
                spec_dict = {"type": p.type, "min": p.min, "max": p.max, "values": list(p.values or [])}
                spec_dict = {k: v for k, v in spec_dict.items() if v is not None}
                for e in _check_value(spec_dict, val, f"{nid}.{pid}"):
                    f.append(finding("error", "param_range", e, nid))

    # connections: slots, types, fan-in
    incoming: dict[tuple[str, str], str] = {}
    adjacency: dict[str, set[str]] = {nid: set() for nid in nodes}
    resolved_out: dict[str, str] = {}
    conns = recipe.get("connections", [])
    for c in conns:
        fn, fs = _split(c["from"])
        tn, ts = _split(c["to"])
        where = f"{c['from']} -> {c['to']}"
        ok = True
        for nid in (fn, tn):
            if nid not in nodes:
                f.append(finding("error", "unknown_node_ref", f"connection refers to undeclared node '{nid}'", where))
                ok = False
        if not ok or nodes[fn]["node"] not in catalog or nodes[tn]["node"] not in catalog:
            continue
        sspec, tspec = catalog[nodes[fn]["node"]], catalog[nodes[tn]["node"]]
        if sspec.output(fs) is None:
            f.append(finding("error", "unknown_slot", f"'{fn}' ({sspec.key}) has no output '{fs}'. Outputs: {[s.id for s in sspec.outputs]}", where))
            continue
        if tspec.input(ts) is None:
            f.append(finding("error", "unknown_slot", f"'{tn}' ({tspec.key}) has no input '{ts}'. Inputs: {[s.id for s in tspec.inputs]}", where))
            continue
        if (tn, ts) in incoming:
            f.append(finding("error", "duplicate_input", f"input '{tn}.{ts}' already has a connection from {incoming[(tn, ts)]}", where))
            continue
        incoming[(tn, ts)] = c["from"]
        adjacency[fn].add(tn)

    # cycle check (Kahn)
    indeg = {nid: 0 for nid in nodes}
    for src, dsts in adjacency.items():
        for d in dsts:
            indeg[d] += 1
    queue = sorted(n for n, d in indeg.items() if d == 0)
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for d in sorted(adjacency[n]):
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    if len(order) != len(nodes):
        f.append(finding("error", "cycle", f"graph has a cycle involving {sorted(set(nodes) - set(order))}"))
        return f

    # type propagation in topological order, then type + required-input checks
    for nid in order:
        n = nodes[nid]
        spec = catalog.get(n["node"])
        if spec is None:
            continue
        for slot in spec.outputs:
            t = slot.type
            if slot.follows:
                src = incoming.get((nid, slot.follows))
                t = resolved_out.get(src, "any") if src else "any"
            resolved_out[f"{nid}.{slot.id}"] = t
        for slot in spec.inputs:
            src = incoming.get((nid, slot.id))
            if src is None:
                if slot.required:
                    f.append(finding("error", "missing_input", f"required input '{nid}.{slot.id}' is not connected", nid))
                continue
            st = resolved_out.get(src, "any")
            if slot.type != "any" and st not in ("any", slot.type):
                hint = " Insert a grayscale_conversion node." if st == "color" and slot.type == "gray" else ""
                f.append(finding("error", "type_mismatch", f"{src} is {st} but '{nid}.{slot.id}' needs {slot.type}.{hint}", nid))

    # outputs
    seen: set[str] = set()
    out_sources: set[str] = set()
    for o in recipe.get("outputs", []):
        usage = o["usage"]
        fn, fs = _split(o["from"])
        if usage not in USAGES:
            f.append(finding("error", "bad_usage", f"usage '{usage}' is not one of {sorted(USAGES)}", usage))
            continue
        if usage in seen:
            f.append(finding("error", "duplicate_usage", f"usage '{usage}' is produced twice", usage))
        seen.add(usage)
        if fn not in nodes or catalog.get(nodes[fn]["node"]) is None or catalog[nodes[fn]["node"]].output(fs) is None:
            f.append(finding("error", "bad_output_source", f"output '{usage}' reads '{o['from']}' which does not exist", usage))
            continue
        out_sources.add(fn)
        t = resolved_out.get(o["from"], "any")
        if t not in ("any", USAGES[usage]):
            f.append(finding("error", "output_type", f"'{usage}' needs a {USAGES[usage]} source but {o['from']} is {t}", usage))
    for need in required_outputs:
        if need not in seen:
            f.append(finding("error", "missing_output", f"style requires an output with usage '{need}'", need))

    # reachability: every node should contribute to an output
    reach = set(out_sources)
    changed = True
    while changed:
        changed = False
        for (tn, _), src in incoming.items():
            sn = _split(src)[0]
            if tn in reach and sn not in reach:
                reach.add(sn)
                changed = True
    for nid in nodes:
        if nid not in reach:
            f.append(finding("warning", "dangling_node", f"node '{nid}' does not contribute to any output", nid))
    return f


# --- compile -----------------------------------------------------------------------------------------------
def compile_recipe(recipe: dict, catalog: dict[str, NodeSpec], overrides: dict | None = None, *,
                   graph_name: str | None = None, required_outputs: tuple = ()) -> dict:
    """Validate and turn a recipe into an ordered, fully resolved *plan* (JSON-serializable)."""
    findings = validate_recipe(recipe, catalog, required_outputs=required_outputs, graph_name=graph_name)
    errors = [x for x in findings if x["severity"] == "error"]
    if errors:
        raise ValueError("recipe is invalid: " + "; ".join(f"[{e['code']}] {e['message']}" for e in errors))
    params = resolve_parameters(recipe, overrides)
    nodes = {n["id"]: n for n in recipe["nodes"]}
    adjacency: dict[str, set[str]] = {nid: set() for nid in nodes}
    for c in recipe.get("connections", []):
        adjacency[_split(c["from"])[0]].add(_split(c["to"])[0])
    indeg = {nid: 0 for nid in nodes}
    for dsts in adjacency.values():
        for d in dsts:
            indeg[d] += 1
    queue, order = sorted(n for n, d in indeg.items() if d == 0), []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for d in sorted(adjacency[n]):
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    plan_nodes = []
    for nid in order:
        n = nodes[nid]
        spec = catalog[n["node"]]
        values = {}
        for pid, p in spec.params.items():
            raw = n.get("params", {}).get(pid)
            if raw is None:
                continue  # leave the node's own default untouched
            value = _eval_param(raw, params)
            if p.type == "int":
                value = int(round(value))
            if p.type == "enum":
                value = {"name": value, "value": (p.values or {}).get(value, value)}
            values[pid] = value
        plan_nodes.append({"id": nid, "node": spec.key, "source": spec.source, "definition": spec.definition,
                           "label": spec.label, "params": values})
    conns = []
    for c in recipe.get("connections", []):
        (fn, fs), (tn, ts) = _split(c["from"]), _split(c["to"])
        conns.append({"from_node": fn, "from_slot": fs, "to_node": tn, "to_slot": ts})
    conns.sort(key=lambda c: (order.index(c["to_node"]), c["to_slot"]))
    outputs = [{"usage": o["usage"], "from_node": _split(o["from"])[0], "from_slot": _split(o["from"])[1]}
               for o in recipe.get("outputs", [])]
    return {
        "recipe": recipe["id"],
        "graph_name": graph_name or recipe.get("graph_name", recipe["id"]),
        "resolution": recipe.get("resolution", 1024),
        "parameters": params,
        "nodes": plan_nodes,
        "connections": conns,
        "outputs": outputs,
        "warnings": [x["message"] for x in findings if x["severity"] == "warning"],
        "unverified_nodes": sorted({catalog[n["node"]].key for n in recipe["nodes"] if not catalog[n["node"]].verified}),
    }
