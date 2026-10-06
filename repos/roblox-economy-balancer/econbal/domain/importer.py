"""Importer: normalise the JSON dumped by the generated read-only Luau (``econbal-import/1``) into an economy spec.

The dump is generic (names, class names and scalar fields of each child Instance of up to three containers). This module maps those raw fields
to economy concepts using documented, case-insensitive aliases and ``field_map`` overrides, and REPORTS everything it had to assume or could not
resolve instead of guessing silently. The result is a draft spec for a human to review; it is never applied anywhere.

Import JSON schema ``econbal-import/1``::

    {"format": "econbal-import/1", "game": "optional name", "truncated": false,
     "containers": {
        "vendor_items": {"path": "ReplicatedStorage.Shop.Items", "items": [{"name": "Drill2", "class": "Configuration", "fields": {"Price": 100, "Tier": 2}}]},
        "tool_stats":   {"path": "...", "items": [...]},
        "ores":         {"path": "...", "items": [...]}},
     "currencies": [{"id": "Coins", "start": 0}]}            # optional, hand-written; otherwise inferred from item fields

Field aliases (case-insensitive, non-alphanumerics ignored). First match wins; ``field_map`` {"price": "Cost"} forces a raw field name.
    vendor_items: price(price, cost) tier(tier, level) currency(currency, currencytype) track(track, category, type, group)
                  tool(tool, toolid, toolname) ore(ore, source, appliesto) mult(mult, multiplier, speedmult, valuemult, sellmult, incomemult)
                  add(add, bonus, yield, income) strategy(strategy)
    tool_stats:   tier(tier, level) mult(same list as above, all present ones are multiplied) add(add, bonus, yield, income)
    ores:         value(value, price, sellvalue, worth) currency(currency, currencytype) rate(itemsperminute, perminute, rate, spawnrate)
"""

from __future__ import annotations

import re
from typing import Any

from . import spec as S

FORMAT = "econbal-import/1"
ALIASES = {
    "vendor_items": {"price": ["price", "cost"], "tier": ["tier", "level"], "currency": ["currency", "currencytype"],
                     "track": ["track", "category", "type", "group"], "tool": ["tool", "toolid", "toolname"], "ore": ["ore", "source", "appliesto"],
                     "mult": ["mult", "multiplier", "speedmult", "valuemult", "sellmult", "incomemult"], "add": ["add", "bonus", "yield", "income"],
                     "strategy": ["strategy"]},
    "tool_stats": {"tier": ["tier", "level"], "mult": ["mult", "multiplier", "speedmult", "valuemult", "sellmult", "incomemult"], "add": ["add", "bonus", "yield", "income"]},
    "ores": {"value": ["value", "price", "sellvalue", "worth"], "currency": ["currency", "currencytype"],
             "rate": ["itemsperminute", "perminute", "rate", "spawnrate"]},
}
MULT_NAMES = ["mult", "multiplier", "speedmult", "valuemult", "sellmult", "incomemult"]
DEFAULT_ARCHETYPES = {
    "casual": {"session_minutes": 15, "sessions_per_day": 2, "efficiency": 0.8, "assumed": True},
    "regular": {"session_minutes": 25, "sessions_per_day": 3, "efficiency": 1.0, "assumed": True},
    "grinder": {"session_minutes": 60, "sessions_per_day": 4, "efficiency": 1.4, "assumed": True},
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def safe_id(raw: str, taken: set[str], prefix: str = "") -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"{prefix}{raw}")
    if not base or not base[0].isalpha():
        base = "x" + base
    base = base[:36].rstrip("_") or "x"
    cand, n = base, 2
    while cand in taken:
        cand = f"{base}_{n}"
        n += 1
    taken.add(cand)
    return cand


class _Fields:
    def __init__(self, kind: str, fields: dict, field_map: dict):
        self.kind, self.raw = kind, fields
        self.by_norm = {_norm(k): k for k in fields}
        self.map = {_norm(k): v for k, v in (field_map or {}).items()}

    def get(self, concept: str) -> tuple[Any, str | None]:
        forced = self.map.get(_norm(concept))
        if forced is not None and _norm(forced) in self.by_norm:
            k = self.by_norm[_norm(forced)]
            return self.raw[k], k
        for alias in ALIASES[self.kind].get(concept, [concept]):
            if alias in self.by_norm:
                k = self.by_norm[alias]
                return self.raw[k], k
        return None, None

    def all_mults(self) -> list[tuple[str, float]]:
        out = []
        for alias in MULT_NAMES:
            if alias in self.by_norm and isinstance(self.raw[self.by_norm[alias]], (int, float)) and not isinstance(self.raw[self.by_norm[alias]], bool):
                out.append((self.by_norm[alias], float(self.raw[self.by_norm[alias]])))
        return out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def normalise(data: dict, *, field_map: dict | None = None, game: str | None = None, days: int = 14, default_currency: str | None = None) -> dict:
    """Return ``{spec, assumptions, unresolved, mapping, findings, ok}``. Raises ValueError only for a malformed dump."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError(f"import JSON must be an object with \"format\": \"{FORMAT}\" (use emit_import_luau to produce it)")
    containers = data.get("containers")
    if not isinstance(containers, dict) or not containers:
        raise ValueError("import JSON has no 'containers'")
    for k in containers:
        if k not in ALIASES:
            raise ValueError(f"unknown container '{k}'. Expected: {sorted(ALIASES)}")
        if not isinstance(containers[k].get("items"), list):
            raise ValueError(f"container '{k}' needs an 'items' list")
    assumptions: list[str] = []
    unresolved: list[str] = []
    mapping: list[dict] = []
    taken: set[str] = set()
    cur_ids: dict[str, str] = {}
    currencies: dict[str, dict] = {}

    def currency(raw: Any) -> str:
        if raw is None or raw == "":
            raw = default_currency or (next(iter(cur_ids)) if cur_ids else "Coins")
            if default_currency is None:
                assumptions.append(f"an item had no currency; assumed '{raw}'")
        key = str(raw)
        if key not in cur_ids:
            cid = safe_id(key, taken, "")
            cur_ids[key] = cid
            currencies[cid] = {"start": 0}
            mapping.append({"kind": "currency", "original": key, "id": cid})
        return cur_ids[key]

    for c in data.get("currencies") or []:
        if isinstance(c, dict) and c.get("id"):
            cid = currency(c["id"])
            if isinstance(c.get("start"), (int, float)):
                currencies[cid]["start"] = c["start"]

    # ores -> sources
    sources: list[dict] = []
    ore_ids: dict[str, str] = {}
    for item in containers.get("ores", {}).get("items", []):
        f = _Fields("ores", item.get("fields") or {}, field_map or {})
        val, _ = f.get("value")
        rate, _ = f.get("rate")
        cur, _ = f.get("currency")
        if _num(val) is None:
            unresolved.append(f"ore '{item.get('name')}': no numeric value field (looked for {ALIASES['ores']['value']}); skipped")
            continue
        if _num(rate) is None:
            rate = 1
            assumptions.append(f"ore '{item.get('name')}': no items-per-minute field (looked for {ALIASES['ores']['rate']}); assumed 1 item per minute")
        sid = safe_id(item["name"], taken, "ore_")
        ore_ids[item["name"]] = sid
        mapping.append({"kind": "ore", "original": item["name"], "id": sid})
        sources.append({"id": sid, "currency": currency(cur), "per_minute": float(val) * float(rate)})
    if not sources:
        unresolved.append("no ores were imported, so there is no income source; add at least one source by hand")

    # tools
    tools: dict[str, dict] = {}
    for item in containers.get("tool_stats", {}).get("items", []):
        f = _Fields("tool_stats", item.get("fields") or {}, field_map or {})
        mults = f.all_mults()
        add, _ = f.get("add")
        eff: list[dict] = []
        if mults:
            prod = 1.0
            for _, v in mults:
                prod *= v
            eff.append({"source": "*", "mult": prod})
        tools[item["name"]] = {"effects": eff, "add": _num(add), "tier": f.get("tier")[0], "fields": [k for k, _ in mults]}

    # vendor items -> upgrades
    upgrades: list[dict] = []
    raw_items = []
    for item in containers.get("vendor_items", {}).get("items", []):
        f = _Fields("vendor_items", item.get("fields") or {}, field_map or {})
        price, _ = f.get("price")
        if _num(price) is None or price <= 0:
            unresolved.append(f"vendor item '{item.get('name')}': no positive numeric price (looked for {ALIASES['vendor_items']['price']}); skipped")
            continue
        raw_items.append((item, f))
    default_track = False
    by_track: dict[str, list] = {}
    for item, f in raw_items:
        track_raw, _ = f.get("track")
        if track_raw is None:
            default_track = True
            track_raw = "items"
        by_track.setdefault(str(track_raw), []).append((item, f))
    if default_track:
        assumptions.append("some vendor items had no track/category field; they were put in track 'items'")
    for track_raw, entries in by_track.items():
        track_id = safe_id(track_raw, set(), "")
        inferred = any(_num(f.get("tier")[0]) is None for _, f in entries)
        entries.sort(key=lambda e: (_num(e[1].get("tier")[0]) if _num(e[1].get("tier")[0]) is not None else 1e18, _num(e[1].get("price")[0]) or 0.0))
        if inferred:
            assumptions.append(f"track '{track_raw}': at least one item had no tier; tiers were assigned by ascending price")
        tier = 0
        for item, f in entries:
            raw_tier = _num(f.get("tier")[0])
            tier = int(raw_tier) if raw_tier is not None and not inferred else (tier + 1)
            uid = safe_id(item["name"], taken, "")
            mapping.append({"kind": "upgrade", "original": item["name"], "id": uid})
            eff: list[dict] = []
            ore_raw, _ = f.get("ore")
            src = ore_ids.get(str(ore_raw)) if ore_raw is not None else None
            if ore_raw is not None and src is None:
                unresolved.append(f"vendor item '{item['name']}': refers to ore '{ore_raw}' which was not imported; effect applied to all sources instead")
            mults = f.all_mults()
            if mults:
                prod = 1.0
                for _, v in mults:
                    prod *= v
                eff.append({"source": src or "*", "mult": prod})
            add, _ = f.get("add")
            if _num(add) is not None:
                tgt = src or (sources[0]["id"] if sources else None)
                if tgt:
                    eff.append({"source": tgt, "add": float(add)})
                    if not src:
                        assumptions.append(f"vendor item '{item['name']}': flat bonus applied to the first source '{tgt}' because no ore was named")
            tool_raw, _ = f.get("tool")
            if not eff and tool_raw is not None:
                t = tools.get(str(tool_raw))
                if t is None:
                    unresolved.append(f"vendor item '{item['name']}': refers to tool '{tool_raw}' which was not imported")
                else:
                    eff = [dict(e) for e in t["effects"]]
                    if t["add"] is not None and sources:
                        eff.append({"source": sources[0]["id"], "add": t["add"]})
                    if eff:
                        assumptions.append(f"vendor item '{item['name']}': income effect taken from tool '{tool_raw}' (product of {t['fields']})")
            if not eff:
                unresolved.append(f"upgrades.{uid}.effects: the dump gave no income effect for '{item['name']}'; the upgrade imports with NO effect and will be flagged until you fill it in")
            cur, _ = f.get("currency")
            up = {"id": uid, "track": track_id, "tier": tier, "currency": currency(cur), "cost": float(f.get("price")[0]), "effects": eff}
            strat, _ = f.get("strategy")
            if isinstance(strat, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", strat):
                up["strategy"] = strat
            upgrades.append(up)
    # tier uniqueness is NOT forced: equal tiers within a track become alternatives (this is how a choice is expressed)
    if not currencies:
        currencies[currency(None)] = {"start": 0}
    if data.get("truncated"):
        unresolved.append("the dump was truncated at max_items; re-run emit_import_luau with a larger max_items")
    assumptions.append("archetypes casual/regular/grinder are ASSUMED defaults (see 'assumed: true'); replace them with your players' real session data")
    assumptions.append("no sinks (upkeep, taxes), boosts or rebirths were imported; add them by hand if the game has them")
    spec = {"format": S.FORMAT, "id": safe_id(game or data.get("game") or "imported", set(), ""), "name": game or data.get("game") or "Imported economy",
            "description": "Draft imported from a Luau dump. Review every value before use.", "simulation": {"days": days, "seed": 0, "policy": "cheapest_payback"},
            "currencies": currencies, "sources": sources, "sinks": [], "upgrades": upgrades, "boosts": [],
            "archetypes": {k: dict(v) for k, v in DEFAULT_ARCHETYPES.items()}, "rebirths": [], "locked": [],
            "import_notes": assumptions + unresolved}
    findings = S.validate(spec)
    return {"spec": spec, "assumptions": assumptions, "unresolved": unresolved, "mapping": mapping, "findings": findings,
            "ok": not S.errors_of(findings)}
