"""The economy spec (``economy.yaml``): load, normalise, validate, address values by path, honour ``locked``.

Format ``econbal-economy/1``::

    format: econbal-economy/1
    id: my_game
    simulation: {days: 14, seed: 0, policy: cheapest_payback}
    currencies: {coins: {start: 0}}
    sources:   [{id: mining, currency: coins, per_minute: 10, unlock: null}]
    sinks:     [{id: fuel, currency: coins, kind: upkeep, value: 1.0}]        # upkeep = per minute, tax = fraction of income
    upgrades:  [{id: drill_1, track: drill, tier: 1, currency: coins, cost: 100,
                 effects: [{source: mining, add: 10}], requires: [], min_rebirths: 0, strategy: speed}]
    boosts:    [{id: double, kind: gamepass, price_robux: 399, effects: [{source: "*", mult: 2}]}]
    archetypes: {regular: {session_minutes: 20, sessions_per_day: 3, efficiency: 1.0, variance: 0.0,
                           boosts: [], rebirth: ladder_complete}}
    rebirths:  [{id: rebirth, currency: coins, cost: 1000000, resets: {tracks: [drill], currencies: [coins]},
                 bonus: {source: "*", mult_per_rebirth: 0.5}, max: 2}]
    locked:    ["upgrades.drill_1.cost", "sources.*"]      # value paths (fnmatch) or entity prefixes; never changed by a proposal

A value is addressed by a dotted path made of section, entity id and field, for example ``upgrades.drill_1.cost`` or
``upgrades.drill_1.effects.0.add``. Upgrades with the same ``track`` and ``tier`` are ALTERNATIVES (buying one forecloses the others).
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import yaml

FORMAT = "econbal-economy/1"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
POLICIES = ("cheapest_payback", "cheapest_cost", "spec_order")
REBIRTH_POLICIES = ("ladder_complete", "never")
SINK_KINDS = ("upkeep", "tax")
LIST_SECTIONS = ("sources", "sinks", "upgrades", "boosts", "rebirths")
MAP_SECTIONS = ("currencies", "archetypes")
TOP_KEYS = {"format", "id", "name", "description", "simulation", "currencies", "sources", "sinks", "upgrades", "boosts",
            "archetypes", "rebirths", "locked", "notes", "import_notes"}
ENTITY_KEYS = {
    "currencies": {"start", "name", "locked"},
    "sources": {"id", "currency", "per_minute", "unlock", "name", "locked"},
    "sinks": {"id", "currency", "kind", "value", "name", "locked"},
    "upgrades": {"id", "track", "tier", "currency", "cost", "effects", "requires", "min_rebirths", "strategy", "name", "locked"},
    "boosts": {"id", "kind", "price_robux", "effects", "name", "locked"},
    "archetypes": {"session_minutes", "sessions_per_day", "efficiency", "variance", "boosts", "rebirth", "policy", "name", "assumed", "locked"},
    "rebirths": {"id", "currency", "cost", "resets", "bonus", "max", "name", "locked"},
}
DEFAULT_ARCHETYPES = ("casual", "regular", "grinder")


# --- loading ---------------------------------------------------------------------------------------------------------
def load_spec(path: str | Path) -> dict:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"{p}: not valid YAML ({exc}). Hint: quote any text that contains ': '.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{p}: an economy spec must be a mapping at the top level")
    return data


def dump_yaml(spec: dict) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, default_flow_style=False, width=120)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]


def is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# --- normalisation ---------------------------------------------------------------------------------------------------
def normalize(spec: dict) -> dict:
    """A deep copy with defaults filled in. Does not validate; run ``validate`` first for anything user supplied."""
    s = copy.deepcopy(spec)
    s.setdefault("format", FORMAT)
    s.setdefault("id", "economy")
    sim = s.setdefault("simulation", {}) or {}
    s["simulation"] = sim
    sim.setdefault("days", 14)
    sim.setdefault("seed", 0)
    sim.setdefault("policy", "cheapest_payback")
    for sec in MAP_SECTIONS:
        s[sec] = s.get(sec) or {}
    for sec in LIST_SECTIONS:
        s[sec] = s.get(sec) or []
    s["locked"] = list(s.get("locked") or [])
    for cid, c in s["currencies"].items():
        if not isinstance(c, dict):
            s["currencies"][cid] = c = {"start": c}
        c.setdefault("start", 0)
    for src in s["sources"]:
        if isinstance(src, dict):
            src.setdefault("unlock", None)
    for up in s["upgrades"]:
        if isinstance(up, dict):
            up.setdefault("effects", [])
            up.setdefault("requires", [])
            up.setdefault("min_rebirths", 0)
    for b in s["boosts"]:
        if isinstance(b, dict):
            b.setdefault("effects", [])
    for aid, a in s["archetypes"].items():
        if isinstance(a, dict):
            a.setdefault("efficiency", 1.0)
            a.setdefault("variance", 0.0)
            a.setdefault("boosts", [])
            a.setdefault("rebirth", "ladder_complete")
    for r in s["rebirths"]:
        if isinstance(r, dict):
            r.setdefault("max", 1)
            r.setdefault("resets", {})
            if isinstance(r["resets"], dict):
                r["resets"].setdefault("tracks", [])
                r["resets"].setdefault("currencies", [])
    return s


def by_id(items: list[dict]) -> dict[str, dict]:
    return {i["id"]: i for i in items}


# --- validation ------------------------------------------------------------------------------------------------------
def _f(sev: str, code: str, path: str, msg: str) -> dict:
    return {"severity": sev, "code": code, "path": path, "message": msg}


def validate(raw: Any) -> list[dict]:
    """Return findings ``{severity, code, path, message}``. ``error`` findings make every analysis refuse to run."""
    out: list[dict] = []
    if not isinstance(raw, dict):
        return [_f("error", "bad_type", "<root>", "the spec must be a mapping")]
    for k in raw:
        if k not in TOP_KEYS:
            out.append(_f("warning", "unknown_key", k, f"unknown top-level key '{k}' (typo? known: {sorted(TOP_KEYS)})"))
    fmt = raw.get("format", FORMAT)
    if fmt != FORMAT:
        out.append(_f("error", "bad_format", "format", f"format must be '{FORMAT}' (got {fmt!r})"))
    for sec in MAP_SECTIONS:
        if raw.get(sec) is not None and not isinstance(raw[sec], dict):
            out.append(_f("error", "bad_type", sec, f"'{sec}' must be a mapping of id -> fields"))
    for sec in LIST_SECTIONS:
        if raw.get(sec) is not None and not isinstance(raw[sec], list):
            out.append(_f("error", "bad_type", sec, f"'{sec}' must be a list"))
    if raw.get("simulation") is not None and not isinstance(raw["simulation"], dict):
        out.append(_f("error", "bad_type", "simulation", "'simulation' must be a mapping"))
    if raw.get("locked") is not None and not (isinstance(raw["locked"], list) and all(isinstance(x, str) for x in raw["locked"])):
        out.append(_f("error", "bad_type", "locked", "'locked' must be a list of value paths (strings)"))
    if any(f["severity"] == "error" for f in out):
        return out
    for sec in LIST_SECTIONS:
        for i, item in enumerate(raw.get(sec) or []):
            if not isinstance(item, dict):
                out.append(_f("error", "bad_type", f"{sec}.{i}", f"each entry of '{sec}' must be a mapping"))
    for sec in MAP_SECTIONS:
        for k, v in (raw.get(sec) or {}).items():
            if sec == "archetypes" and not isinstance(v, dict):
                out.append(_f("error", "bad_type", f"{sec}.{k}", "an archetype must be a mapping"))
    if any(f["severity"] == "error" for f in out):
        return out
    s = normalize(raw)

    # simulation
    sim = s["simulation"]
    if not (isinstance(sim["days"], int) and not isinstance(sim["days"], bool) and 1 <= sim["days"] <= 365):
        out.append(_f("error", "bad_value", "simulation.days", "days must be an integer from 1 to 365"))
    if not (isinstance(sim["seed"], int) and not isinstance(sim["seed"], bool)):
        out.append(_f("error", "bad_value", "simulation.seed", "seed must be an integer"))
    if sim["policy"] not in POLICIES:
        out.append(_f("error", "bad_value", "simulation.policy", f"policy must be one of {POLICIES}"))
    for k in sim:
        if k not in ("days", "seed", "policy"):
            out.append(_f("warning", "unknown_key", f"simulation.{k}", f"unknown simulation key '{k}'"))

    # ids
    seen: dict[str, str] = {}
    for sec in LIST_SECTIONS:
        for i, item in enumerate(s[sec]):
            iid = item.get("id")
            where = f"{sec}.{iid if isinstance(iid, str) else i}"
            if not isinstance(iid, str) or not ID_RE.match(iid):
                out.append(_f("error", "bad_id", f"{sec}.{i}", f"id {iid!r} must match {ID_RE.pattern} (letters, digits, underscore)"))
                continue
            if (sec, iid) in seen:
                out.append(_f("error", "duplicate_id", where, f"duplicate id '{iid}' in '{sec}'"))
            seen[(sec, iid)] = where
    for sec in MAP_SECTIONS:
        for iid in s[sec]:
            if not (isinstance(iid, str) and ID_RE.match(iid)):
                out.append(_f("error", "bad_id", f"{sec}.{iid}", f"id {iid!r} must match {ID_RE.pattern}"))
    if any(f["severity"] == "error" for f in out):
        return out

    def keys_check(sec: str, where: str, d: dict) -> None:
        for k in d:
            if k not in ENTITY_KEYS[sec]:
                out.append(_f("warning", "unknown_key", f"{where}.{k}", f"unknown field '{k}' in {sec} (typo? known: {sorted(ENTITY_KEYS[sec])})"))

    def num(where: str, v: Any, *, lo: float | None = None, hi: float | None = None, strict_lo: bool = False, integer: bool = False) -> bool:
        if not is_number(v):
            out.append(_f("error", "bad_value", where, f"{where} must be a finite number (got {v!r})"))
            return False
        if integer and (isinstance(v, float) and not v.is_integer()):
            out.append(_f("error", "bad_value", where, f"{where} must be an integer (got {v!r})"))
            return False
        if lo is not None and (v < lo or (strict_lo and v == lo)):
            out.append(_f("error", "bad_value", where, f"{where} must be {'>' if strict_lo else '>='} {lo} (got {v})"))
            return False
        if hi is not None and v > hi:
            out.append(_f("error", "bad_value", where, f"{where} must be <= {hi} (got {v})"))
            return False
        return True

    currencies = set(s["currencies"])
    if not currencies:
        out.append(_f("error", "missing_key", "currencies", "define at least one currency"))
    for cid, c in s["currencies"].items():
        keys_check("currencies", f"currencies.{cid}", c)
        num(f"currencies.{cid}.start", c.get("start"), lo=0)

    sources = {x["id"]: x for x in s["sources"]}
    upgrades = {x["id"]: x for x in s["upgrades"]}
    boosts = {x["id"]: x for x in s["boosts"]}
    if not sources:
        out.append(_f("error", "missing_key", "sources", "define at least one source of income"))
    for src in s["sources"]:
        w = f"sources.{src['id']}"
        keys_check("sources", w, src)
        if src.get("currency") not in currencies:
            out.append(_f("error", "unknown_reference", f"{w}.currency", f"currency {src.get('currency')!r} is not defined in 'currencies'"))
        num(f"{w}.per_minute", src.get("per_minute"), lo=0)
        if src.get("unlock") is not None and src["unlock"] not in upgrades:
            out.append(_f("error", "unknown_reference", f"{w}.unlock", f"unlock upgrade {src['unlock']!r} does not exist"))
    for sk in s["sinks"]:
        w = f"sinks.{sk['id']}"
        keys_check("sinks", w, sk)
        if sk.get("currency") not in currencies:
            out.append(_f("error", "unknown_reference", f"{w}.currency", f"currency {sk.get('currency')!r} is not defined"))
        if sk.get("kind") not in SINK_KINDS:
            out.append(_f("error", "bad_value", f"{w}.kind", f"kind must be one of {SINK_KINDS}"))
        elif sk["kind"] == "tax":
            num(f"{w}.value", sk.get("value"), lo=0, hi=1)
        else:
            num(f"{w}.value", sk.get("value"), lo=0)

    def check_effects(where: str, effects: Any) -> None:
        if not isinstance(effects, list):
            out.append(_f("error", "bad_type", where, "effects must be a list"))
            return
        for i, e in enumerate(effects):
            ew = f"{where}.{i}"
            if not isinstance(e, dict):
                out.append(_f("error", "bad_type", ew, "an effect must be a mapping like {source: mining, add: 5}"))
                continue
            for k in e:
                if k not in ("source", "add", "mult"):
                    out.append(_f("warning", "unknown_key", f"{ew}.{k}", f"unknown effect key '{k}'"))
            src = e.get("source")
            if src != "*" and src not in sources:
                out.append(_f("error", "unknown_reference", f"{ew}.source", f"effect source {src!r} is not a defined source (use '*' for all)"))
            if ("add" in e) == ("mult" in e):
                out.append(_f("error", "bad_value", ew, "an effect needs exactly one of 'add' or 'mult'"))
            elif "add" in e:
                if src == "*":
                    out.append(_f("error", "star_add", ew, "'add' cannot target '*'; use 'mult' for all sources or name the source"))
                num(f"{ew}.add", e["add"], lo=0)
            else:
                num(f"{ew}.mult", e["mult"], lo=0)

    tracks: dict[str, dict[int, list[str]]] = {}
    for up in s["upgrades"]:
        w = f"upgrades.{up['id']}"
        keys_check("upgrades", w, up)
        if not (isinstance(up.get("track"), str) and ID_RE.match(up["track"])):
            out.append(_f("error", "bad_id", f"{w}.track", f"track {up.get('track')!r} must match {ID_RE.pattern}"))
        if num(f"{w}.tier", up.get("tier"), lo=1, integer=True) and isinstance(up.get("track"), str):
            tracks.setdefault(up["track"], {}).setdefault(int(up["tier"]), []).append(up["id"])
        if up.get("currency") not in currencies:
            out.append(_f("error", "unknown_reference", f"{w}.currency", f"currency {up.get('currency')!r} is not defined"))
        num(f"{w}.cost", up.get("cost"), lo=0, strict_lo=True)
        num(f"{w}.min_rebirths", up.get("min_rebirths"), lo=0, integer=True)
        check_effects(f"{w}.effects", up.get("effects"))
        if not isinstance(up.get("requires"), list):
            out.append(_f("error", "bad_type", f"{w}.requires", "requires must be a list of upgrade ids"))
        else:
            for r in up["requires"]:
                if r not in upgrades:
                    out.append(_f("error", "unknown_reference", f"{w}.requires", f"required upgrade {r!r} does not exist"))
                elif r == up["id"]:
                    out.append(_f("error", "cycle", f"{w}.requires", "an upgrade cannot require itself"))
    for track, tiers in tracks.items():
        top = max(tiers)
        for t in range(1, top + 1):
            if t not in tiers:
                out.append(_f("error", "tier_gap", f"upgrades.track.{track}", f"track '{track}' has tier {top} but no upgrade at tier {t}; tiers must be contiguous from 1"))
                break
    # requires cycles
    state: dict[str, int] = {}

    def visit(u: str, stack: list[str]) -> None:
        if state.get(u) == 2:
            return
        if state.get(u) == 1:
            out.append(_f("error", "cycle", f"upgrades.{u}.requires", "requires cycle: " + " -> ".join(stack + [u])))
            return
        state[u] = 1
        for r in upgrades[u].get("requires") or []:
            if r in upgrades and r != u:
                visit(r, stack + [u])
        state[u] = 2

    for u in upgrades:
        visit(u, [])

    for b in s["boosts"]:
        w = f"boosts.{b['id']}"
        keys_check("boosts", w, b)
        check_effects(f"{w}.effects", b.get("effects"))
        if b.get("price_robux") is not None:
            num(f"{w}.price_robux", b["price_robux"], lo=0)

    if not s["archetypes"]:
        out.append(_f("error", "missing_key", "archetypes", "define at least one archetype (casual, regular, grinder)"))
    for aid, a in s["archetypes"].items():
        w = f"archetypes.{aid}"
        keys_check("archetypes", w, a)
        num(f"{w}.session_minutes", a.get("session_minutes"), lo=0, strict_lo=True, integer=True)
        num(f"{w}.sessions_per_day", a.get("sessions_per_day"), lo=0, strict_lo=True, integer=True)
        num(f"{w}.efficiency", a.get("efficiency"), lo=0, strict_lo=True)
        if num(f"{w}.variance", a.get("variance"), lo=0):
            if a["variance"] >= 1:
                out.append(_f("error", "bad_value", f"{w}.variance", "variance must be below 1 (a session may not earn nothing)"))
        if a.get("rebirth") not in REBIRTH_POLICIES:
            out.append(_f("error", "bad_value", f"{w}.rebirth", f"rebirth must be one of {REBIRTH_POLICIES}"))
        if a.get("policy") is not None and a["policy"] not in POLICIES:
            out.append(_f("error", "bad_value", f"{w}.policy", f"policy must be one of {POLICIES}"))
        if not isinstance(a.get("boosts"), list):
            out.append(_f("error", "bad_type", f"{w}.boosts", "boosts must be a list of boost ids"))
        else:
            for bid in a["boosts"]:
                if bid not in boosts:
                    out.append(_f("error", "unknown_reference", f"{w}.boosts", f"boost {bid!r} does not exist"))
    missing = [a for a in DEFAULT_ARCHETYPES if a not in s["archetypes"]]
    if missing and s["archetypes"]:
        out.append(_f("warning", "missing_archetypes", "archetypes", f"standard archetypes missing: {missing}. Bands default to 'regular'; add it or edit style/style.yaml reference_archetypes."))

    if len(s["rebirths"]) > 1:
        out.append(_f("error", "multiple_rebirths", "rebirths", "the simulator supports at most one rebirth definition"))
    for r in s["rebirths"][:1]:
        w = f"rebirths.{r['id']}"
        keys_check("rebirths", w, r)
        if r.get("currency") not in currencies:
            out.append(_f("error", "unknown_reference", f"{w}.currency", f"currency {r.get('currency')!r} is not defined"))
        num(f"{w}.cost", r.get("cost"), lo=0, strict_lo=True)
        num(f"{w}.max", r.get("max"), lo=1, integer=True)
        rs = r.get("resets")
        if not isinstance(rs, dict):
            out.append(_f("error", "bad_type", f"{w}.resets", "resets must be a mapping {tracks: [...], currencies: [...]}"))
        else:
            for t in rs.get("tracks") or []:
                if t not in tracks:
                    out.append(_f("error", "unknown_reference", f"{w}.resets.tracks", f"track {t!r} has no upgrades"))
            for c in rs.get("currencies") or []:
                if c not in currencies:
                    out.append(_f("error", "unknown_reference", f"{w}.resets.currencies", f"currency {c!r} is not defined"))
            if not rs.get("tracks"):
                out.append(_f("error", "bad_value", f"{w}.resets.tracks", "list at least one track (the ladder that must be complete before a rebirth)"))
        bonus = r.get("bonus")
        if not isinstance(bonus, dict):
            out.append(_f("error", "bad_type", f"{w}.bonus", "bonus must be {source: '*', mult_per_rebirth: 0.5} or {source: '*', mult_compound: 1.5}"))
        else:
            if bonus.get("source") != "*" and bonus.get("source") not in sources:
                out.append(_f("error", "unknown_reference", f"{w}.bonus.source", f"bonus source {bonus.get('source')!r} is not defined"))
            if ("mult_per_rebirth" in bonus) == ("mult_compound" in bonus):
                out.append(_f("error", "bad_value", f"{w}.bonus", "bonus needs exactly one of mult_per_rebirth or mult_compound"))
            for k in ("mult_per_rebirth", "mult_compound"):
                if k in bonus:
                    num(f"{w}.bonus.{k}", bonus[k], lo=0)
            for k in bonus:
                if k not in ("source", "mult_per_rebirth", "mult_compound"):
                    out.append(_f("warning", "unknown_key", f"{w}.bonus.{k}", f"unknown bonus key '{k}'"))

    # semantic warnings
    income_currencies = {src["currency"] for src in s["sources"] if is_number(src.get("per_minute")) and src["per_minute"] > 0}
    for up in s["upgrades"]:
        if up.get("currency") in currencies and up["currency"] not in income_currencies:
            out.append(_f("warning", "unreachable_currency", f"upgrades.{up['id']}.currency",
                          f"currency '{up['currency']}' has no source with income, so '{up['id']}' can never be afforded (unless bought outside the model)"))
    required_by = {r for up in s["upgrades"] for r in up.get("requires") or []}
    for up in s["upgrades"]:
        if isinstance(up.get("effects"), list) and not up["effects"]:
            gates = up["id"] in required_by or any(o.get("track") == up.get("track") and is_number(o.get("tier")) and is_number(up.get("tier")) and o["tier"] > up["tier"] for o in s["upgrades"]) \
                or any(src.get("unlock") == up["id"] for src in s["sources"])
            if gates and not any(src.get("unlock") == up["id"] for src in s["sources"]):
                out.append(_f("warning", "zero_effect_gate", f"upgrades.{up['id']}", f"'{up['id']}' has no income effect but gates later steps; a payback-ranked player would buy it last"))
    # locked
    if not any(f["severity"] == "error" for f in out):
        paths = list(value_paths(s))
        for pat in s["locked"]:
            if not any(path_matches(p, pat) for p in paths):
                out.append(_f("error", "locked_matches_nothing", "locked", f"locked pattern {pat!r} matches no value path (check spelling; use value paths like 'upgrades.drill_1.cost')"))
    return out


def errors_of(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["severity"] == "error"]


# --- value addressing ------------------------------------------------------------------------------------------------
def value_paths(spec: dict, sections: tuple[str, ...] | None = None) -> dict[str, float]:
    """Every numeric value of the (normalised) spec, addressed by dotted path, in a stable order."""
    s = spec if "simulation" in spec and isinstance(spec.get("currencies"), dict) else normalize(spec)
    want = set(sections) if sections else None
    out: dict[str, float] = {}

    def ok(sec: str) -> bool:
        return want is None or sec in want

    if ok("currencies"):
        for cid, c in s["currencies"].items():
            if is_number(c.get("start")):
                out[f"currencies.{cid}.start"] = c["start"]
    if ok("sources"):
        for x in s["sources"]:
            if is_number(x.get("per_minute")):
                out[f"sources.{x['id']}.per_minute"] = x["per_minute"]
    if ok("sinks"):
        for x in s["sinks"]:
            if is_number(x.get("value")):
                out[f"sinks.{x['id']}.value"] = x["value"]
    for sec in ("upgrades", "boosts"):
        if not ok(sec):
            continue
        for x in s[sec]:
            if sec == "upgrades":
                if is_number(x.get("cost")):
                    out[f"upgrades.{x['id']}.cost"] = x["cost"]
                if is_number(x.get("min_rebirths")):
                    out[f"upgrades.{x['id']}.min_rebirths"] = x["min_rebirths"]
            if sec == "boosts" and is_number(x.get("price_robux")):
                out[f"boosts.{x['id']}.price_robux"] = x["price_robux"]
            for i, e in enumerate(x.get("effects") or []):
                for k in ("add", "mult"):
                    if isinstance(e, dict) and is_number(e.get(k)):
                        out[f"{sec}.{x['id']}.effects.{i}.{k}"] = e[k]
    if ok("archetypes"):
        for aid, a in s["archetypes"].items():
            for k in ("session_minutes", "sessions_per_day", "efficiency", "variance"):
                if is_number(a.get(k)):
                    out[f"archetypes.{aid}.{k}"] = a[k]
    if ok("rebirths"):
        for r in s["rebirths"]:
            if is_number(r.get("cost")):
                out[f"rebirths.{r['id']}.cost"] = r["cost"]
            if is_number(r.get("max")):
                out[f"rebirths.{r['id']}.max"] = r["max"]
            for k in ("mult_per_rebirth", "mult_compound"):
                if isinstance(r.get("bonus"), dict) and is_number(r["bonus"].get(k)):
                    out[f"rebirths.{r['id']}.bonus.{k}"] = r["bonus"][k]
    return out


def _resolve(spec: dict, path: str) -> tuple[Any, str]:
    """(container, key) of the value at ``path`` so it can be read or written."""
    parts = path.split(".")
    if len(parts) < 2:
        raise ValueError(f"'{path}' is not a value path; expected like 'upgrades.drill_1.cost'")
    sec = parts[0]
    if sec in MAP_SECTIONS:
        node: Any = spec.get(sec, {})
        if parts[1] not in node:
            raise ValueError(f"'{path}': no {sec[:-1] if sec.endswith('s') else sec} '{parts[1]}'. Known: {sorted(node)}")
        node = node[parts[1]]
        rest = parts[2:]
    elif sec in LIST_SECTIONS:
        match = [x for x in spec.get(sec, []) if x.get("id") == parts[1]]
        if not match:
            raise ValueError(f"'{path}': no entry '{parts[1]}' in '{sec}'. Known: {[x.get('id') for x in spec.get(sec, [])]}")
        node = match[0]
        rest = parts[2:]
    else:
        raise ValueError(f"'{path}': unknown section '{sec}'. Sections: {list(MAP_SECTIONS + LIST_SECTIONS)}")
    if not rest:
        raise ValueError(f"'{path}' names an entity, not a value")
    for p in rest[:-1]:
        node = node[int(p)] if isinstance(node, list) and p.isdigit() else (node.get(p) if isinstance(node, dict) else None)
        if node is None:
            raise ValueError(f"'{path}': nothing at '{p}'")
    last = rest[-1]
    if isinstance(node, dict) and last in node:
        return node, last
    raise ValueError(f"'{path}': no field '{last}'")


def get_value(spec: dict, path: str) -> Any:
    node, key = _resolve(spec, path)
    return node[key]


def set_value(spec: dict, path: str, value: Any) -> dict:
    """Return a deep copy of ``spec`` with one value changed. Never mutates the input."""
    out = copy.deepcopy(spec)
    node, key = _resolve(out, path)
    node[key] = value
    return out


def path_matches(path: str, pattern: str) -> bool:
    """A pattern is an fnmatch glob over the whole path, or an entity prefix such as ``upgrades.drill_2``."""
    return fnmatch.fnmatchcase(path, pattern) or path == pattern or path.startswith(pattern.rstrip(".") + ".")


def locked_paths(spec: dict) -> list[str]:
    """Every value path that a proposal must not change (``locked`` patterns plus entities marked ``locked: true``)."""
    s = normalize(spec)
    paths = list(value_paths(s))
    locked = {p for p in paths if any(path_matches(p, pat) for pat in s["locked"])}
    for sec in LIST_SECTIONS:
        for x in s[sec]:
            if x.get("locked") is True:
                locked |= {p for p in paths if p.startswith(f"{sec}.{x['id']}.")}
    for sec in MAP_SECTIONS:
        for k, x in s[sec].items():
            if isinstance(x, dict) and x.get("locked") is True:
                locked |= {p for p in paths if p.startswith(f"{sec}.{k}.")}
    return sorted(locked)


def diff_values(a: dict, b: dict) -> list[dict]:
    """Numeric value differences between two specs by path (added/removed entities show as before/after None)."""
    va, vb = value_paths(a), value_paths(b)
    rows = []
    for p in sorted(set(va) | set(vb)):
        if va.get(p) != vb.get(p):
            rows.append({"path": p, "before": va.get(p), "after": vb.get(p)})
    return rows
