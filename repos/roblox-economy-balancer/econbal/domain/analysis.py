"""Analyses over simulator runs: pacing against target bands, flows and inflation, dominant and dead options, walls and cliffs,
monetisation checks, sensitivity and spec comparison.

Every number returned here is computed from ``sim.simulate``; thresholds come from ``style/style.yaml`` and the severity and wording of
each finding from ``rules/findings.yaml``. Nothing is hard-coded except the arithmetic.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..guide_adapter import config

check_ranges = config.check_ranges
from . import sim as M
from . import spec as S

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def load_rules(root: Path) -> dict:
    data = yaml.safe_load((Path(root) / "rules" / "findings.yaml").read_text(encoding="utf-8")) or {}
    if "findings" not in data:
        raise ValueError("rules/findings.yaml must define 'findings'")
    return data["findings"]


@dataclass
class Ctx:
    """A validated, normalised spec plus the style thresholds and finding catalogue that judge it."""

    spec: dict
    style: dict
    rules: dict
    _cache: dict = field(default_factory=dict)

    @property
    def model(self) -> M.Model:
        return M.Model(self.spec)

    def rng(self, name: str, key: str, default: float | None = None) -> float | None:
        return (self.style.get("ranges", {}).get(name) or {}).get(key, default)

    @property
    def reference(self) -> list[str]:
        ref = [a for a in self.style.get("reference_archetypes", ["regular"]) if a in self.spec["archetypes"]]
        return ref or [next(iter(self.spec["archetypes"]))]

    def band(self, tier: int | None) -> dict | None:
        if tier is None:
            return None
        for b in self.style.get("target_bands", []):
            lo, hi = b["tiers"]
            if lo <= tier <= hi:
                return b
        return None

    def band_archetypes(self, band: dict | None) -> list[str]:
        names = (band or {}).get("archetypes") or self.reference
        return [n for n in names if n in self.spec["archetypes"]]

    def finding(self, code: str, message: str, **ctx: Any) -> dict:
        rule = self.rules.get(code)
        if rule is None:
            raise ValueError(f"finding code '{code}' is not defined in rules/findings.yaml")
        return {"code": code, "severity": rule["severity"], "title": rule["title"], "message": message, **ctx}

    def sims(self, archetypes: list[str] | None = None, *, boost_mode: Any = None, seed: int | None = None, days: int | None = None) -> dict[str, dict]:
        key = (tuple(archetypes or self.spec["archetypes"]), repr(boost_mode), seed, days)
        if key not in self._cache:
            self._cache[key] = M.simulate_all(self.spec, archetypes=list(key[0]), boost_mode=boost_mode, seed=seed, days=days)
        return self._cache[key]


def make_ctx(spec: dict, style: dict, rules: dict) -> Ctx:
    return Ctx(S.normalize(spec), style, rules)


def sort_findings(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 3), f["code"], str(f.get("archetype", "")), f.get("tier") or 0, str(f.get("upgrade", ""))))


def clean(obj: Any, ndigits: int = 4) -> Any:
    """Drop private ``_`` keys and round floats so results are compact and stable."""
    if isinstance(obj, dict):
        return {k: clean(v, ndigits) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [clean(v, ndigits) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(clean(v, ndigits) for v in obj)
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None
        r = round(obj, ndigits)
        return int(r) if r == int(r) and abs(r) < 1e15 else r
    return obj


# ---------------------------------------------------------------------------------------------------------------------------
# time to next upgrade
# ---------------------------------------------------------------------------------------------------------------------------
def timing_rows(ctx: Ctx, sims: dict[str, dict], laps: str = "first") -> list[dict]:
    """One row per purchase: when it happened and how long after the previous purchase (active minutes), against its band."""
    rows = []
    for name, sm in sims.items():
        dpm = sm["minutes_per_day"]
        for p in sm["purchases"]:
            if laps == "first" and p["lap"] != 0:
                continue
            band = ctx.band(p["tier"]) if p["kind"] == "upgrade" else None
            in_scope = name in ctx.band_archetypes(band) if band else name in ctx.reference
            status = "no_band"
            if band and in_scope:
                status = "under_band" if p["gap_minutes"] < band["min_minutes"] else "over_band" if p["gap_minutes"] > band["max_minutes"] else "ok"
            elif band:
                status = "not_in_band_scope"
            rows.append({"archetype": name, "kind": p["kind"], "upgrade": p["id"], "track": p["track"], "tier": p["tier"], "lap": p["lap"],
                         "minute": p["minute"], "calendar_day": p["day"] + 1, "gap_minutes": p["gap_minutes"], "gap_days": p["gap_minutes"] / dpm,
                         "cost": p["cost"], "band": band["id"] if band else None, "band_min": band["min_minutes"] if band else None,
                         "band_max": band["max_minutes"] if band else None, "status": status, "in_scope": in_scope})
        pend = sm.get("pending")
        if pend and pend["lap"] == 0 and pend["projected_gap_minutes"] is not None and laps in ("first", "all"):
            tier = pend.get("tier")
            band = ctx.band(tier) if pend["kind"] == "upgrade" else None
            in_scope = name in ctx.band_archetypes(band) if band else name in ctx.reference
            g = pend["projected_gap_minutes"]
            status = "no_band"
            if band and in_scope:
                status = "over_band" if g > band["max_minutes"] else "ok"
            elif band:
                status = "not_in_band_scope"
            rows.append({"archetype": name, "kind": pend["kind"], "upgrade": pend["id"], "track": pend.get("track"), "tier": tier, "lap": 0, "minute": None, "projected": True,
                         "calendar_day": None, "gap_minutes": g, "gap_days": g / dpm, "cost": pend["cost"], "band": band["id"] if band else None,
                         "band_min": band["min_minutes"] if band else None, "band_max": band["max_minutes"] if band else None, "status": status, "in_scope": in_scope})
    return rows


def unreached(ctx: Ctx, sims: dict[str, dict]) -> list[dict]:
    """First tier of each track that an archetype did not buy within the horizon (lap 0 only)."""
    m = ctx.model
    out = []
    for name, sm in sims.items():
        bought = {(p["track"], p["tier"]) for p in sm["purchases"] if p["kind"] == "upgrade" and p["lap"] == 0}
        for track, top in sorted(m.track_top.items()):
            for tier in range(1, top + 1):
                if (track, tier) not in bought:
                    out.append({"archetype": name, "track": track, "first_missing_tier": tier, "tiers_missing": top - tier + 1,
                                "options": m.groups[(track, tier)]})
                    break
    return out


def timing_findings(ctx: Ctx, rows: list[dict], sims: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    wall_factor = ctx.rng("wall_factor", "max", 3.0)
    idle = ctx.rng("max_idle_minutes", "max", 240)
    gap_jump = ctx.rng("max_gap_jump_ratio", "max", 4.0)
    cost_jump = ctx.rng("max_cost_jump_ratio", "max", 5.0)
    max_days = ctx.rng("max_days_per_step", "max", 7.0)
    seen_cost: set[str] = set()
    for name, sm in sims.items():
        prev = None
        track_prev: dict[str, dict] = {}
        for p in sm["purchases"]:
            if p["lap"] != 0:
                break
            row = next(r for r in rows if r["archetype"] == name and r["upgrade"] == p["id"] and r["lap"] == 0 and r["minute"] == p["minute"])
            ref = name in ctx.reference
            if row["in_scope"] or (ref and row["band"] is None):
                limit_wall = wall_factor * row["band_max"] if row["band_max"] is not None else idle
                if row["gap_minutes"] > limit_wall:
                    basis = f"{wall_factor:g} x band maximum {row['band_max']:g}" if row["band_max"] is not None else f"max_idle_minutes {idle:g}"
                    out.append(ctx.finding("wall", f"{name}: '{p['id']}' (tier {p['tier']}) takes {row['gap_minutes']:g} active minutes, over the wall limit "
                                                   f"{limit_wall:g} ({basis}).", archetype=name, upgrade=p["id"], tier=p["tier"], value=row["gap_minutes"], limit=limit_wall))
                elif row["status"] == "over_band":
                    out.append(ctx.finding("over_band", f"{name}: '{p['id']}' (tier {p['tier']}) takes {row['gap_minutes']:g} active minutes; band '{row['band']}' "
                                                        f"allows {row['band_min']:g}-{row['band_max']:g}.", archetype=name, upgrade=p["id"], tier=p["tier"],
                                           value=row["gap_minutes"], limit=row["band_max"], band=row["band"]))
                elif row["status"] == "under_band":
                    out.append(ctx.finding("under_band", f"{name}: '{p['id']}' (tier {p['tier']}) takes {row['gap_minutes']:g} active minutes; band '{row['band']}' "
                                                         f"requires at least {row['band_min']:g}.", archetype=name, upgrade=p["id"], tier=p["tier"],
                                           value=row["gap_minutes"], limit=row["band_min"], band=row["band"]))
                if prev is not None and prev["gap_minutes"] >= 1 and p["kind"] == "upgrade" and prev["kind"] == "upgrade" and ref:
                    ratio = p["gap_minutes"] / prev["gap_minutes"]
                    if ratio > gap_jump:
                        out.append(ctx.finding("gap_cliff", f"{name}: '{p['id']}' takes {ratio:.2f}x as long as the step before it ('{prev['id']}'); limit {gap_jump:g}x.",
                                               archetype=name, upgrade=p["id"], tier=p["tier"], value=ratio, limit=gap_jump))
            if p["kind"] == "upgrade":
                tp = track_prev.get(p["track"])
                if tp and ref and p["id"] not in seen_cost:
                    ratio = p["cost"] / tp["cost"]
                    if ratio > cost_jump:
                        seen_cost.add(p["id"])
                        out.append(ctx.finding("cost_cliff", f"'{p['id']}' costs {ratio:.2f}x '{tp['id']}', the previous step of track '{p['track']}'; limit {cost_jump:g}x.",
                                               upgrade=p["id"], tier=p["tier"], track=p["track"], value=ratio, limit=cost_jump))
                track_prev[p["track"]] = p
            if p["kind"] == "upgrade" and max_days is not None and row["gap_days"] > max_days:
                out.append(ctx.finding("slow_for_archetype", f"{name}: '{p['id']}' takes {row['gap_days']:.2f} calendar days ({row['gap_minutes']:g} active minutes); limit {max_days:g}.",
                                       archetype=name, upgrade=p["id"], tier=p["tier"], value=row["gap_days"], limit=max_days))
            prev = p
        pend = next((r for r in rows if r["archetype"] == name and r.get("projected")), None)
        if pend and pend["kind"] == "upgrade" and max_days is not None and pend["gap_days"] > max_days:
            out.append(ctx.finding("slow_for_archetype", f"{name}: '{pend['upgrade']}' is not bought within the simulated days; projected {pend['gap_days']:.2f} calendar days "
                                                         f"({pend['gap_minutes']:g} active minutes); limit {max_days:g}.", archetype=name, upgrade=pend["upgrade"], tier=pend["tier"],
                                   value=pend["gap_days"], limit=max_days, projected=True))
        if pend and (pend["in_scope"] or (name in ctx.reference and pend["band"] is None)):
            limit_wall = wall_factor * pend["band_max"] if pend["band_max"] is not None else idle
            ident = dict(archetype=name, upgrade=pend["upgrade"], tier=pend["tier"], value=pend["gap_minutes"])
            if pend["gap_minutes"] > limit_wall:
                out.append(ctx.finding("wall", f"{name}: '{pend['upgrade']}' (tier {pend['tier']}) is not bought within the simulated days; it is projected to take "
                                               f"{pend['gap_minutes']:g} active minutes, over the wall limit {limit_wall:g}.", limit=limit_wall, projected=True, **ident))
            elif pend["status"] == "over_band":
                out.append(ctx.finding("over_band", f"{name}: '{pend['upgrade']}' (tier {pend['tier']}) is not bought within the simulated days; it is projected to take "
                                                    f"{pend['gap_minutes']:g} active minutes; band '{pend['band']}' allows up to {pend['band_max']:g}.", limit=pend["band_max"],
                                       band=pend["band"], projected=True, **ident))
    for u in unreached(ctx, sims):
        out.append(ctx.finding("not_reached", f"{u['archetype']} did not reach tier {u['first_missing_tier']} of track '{u['track']}' within {sims[u['archetype']]['days']} days "
                                              f"({u['tiers_missing']} tier(s) missing).", archetype=u["archetype"], track=u["track"], tier=u["first_missing_tier"]))
    return out


def time_to_upgrade(ctx: Ctx, *, archetypes: list[str] | None = None, boost_mode: Any = None, seed: int | None = None, days: int | None = None,
                    laps: str = "first") -> dict:
    if laps not in ("first", "all"):
        raise ValueError("laps must be 'first' or 'all'")
    sims = ctx.sims(archetypes, boost_mode=boost_mode, seed=seed, days=days)
    rows = timing_rows(ctx, sims, laps)
    findings = timing_findings(ctx, rows, sims)
    tiers: dict[tuple[str, int], dict] = {}
    for r in rows:
        if r["kind"] != "upgrade" or r["lap"] != 0:
            continue
        t = tiers.setdefault((r["archetype"], r["tier"]), {"archetype": r["archetype"], "tier": r["tier"], "upgrades": [], "gap_minutes_min": None,
                                                           "gap_minutes_max": None, "band": r["band"], "band_min": r["band_min"], "band_max": r["band_max"]})
        t["upgrades"].append(r["upgrade"])
        t["gap_minutes_min"] = r["gap_minutes"] if t["gap_minutes_min"] is None else min(t["gap_minutes_min"], r["gap_minutes"])
        t["gap_minutes_max"] = r["gap_minutes"] if t["gap_minutes_max"] is None else max(t["gap_minutes_max"], r["gap_minutes"])
    return {"rows": rows, "per_tier": [tiers[k] for k in sorted(tiers, key=lambda k: (k[1], k[0]))], "findings": findings, "unreached": unreached(ctx, sims),
            "sims": sims}


# ---------------------------------------------------------------------------------------------------------------------------
# flows and inflation
# ---------------------------------------------------------------------------------------------------------------------------
def flow(ctx: Ctx, *, archetypes: list[str] | None = None, boost_mode: Any = None, seed: int | None = None, days: int | None = None) -> dict:
    sims = ctx.sims(archetypes, boost_mode=boost_mode, seed=seed, days=days)
    thr = ctx.rng("max_tail_net_share", "max", 0.8)
    min_days = ctx.rng("min_content_days", "min", None)
    report: dict[str, Any] = {}
    findings: list[dict] = []
    for name, sm in sims.items():
        ndays = sm["days"]
        tail = max(1, math.ceil(ndays * 0.25))
        per_cur: dict[str, Any] = {}
        for c in sm["currencies"]:
            day_rows = []
            for d in sm["daily"]:
                src = d["sources"][c]
                sinks = d["sinks"][c]
                total_sink = sum(sinks.values())
                day_rows.append({"day": d["day"] + 1, "sources": src, "sinks": total_sink, "sinks_by": dict(sinks), "net": src - total_sink,
                                 "balance_end": d["balance_end"][c], "income_rate_end": d["income_rate_end"].get(c)})
            tot_src = sum(r["sources"] for r in day_rows)
            tot_sink = sum(r["sinks"] for r in day_rows)
            tail_rows = day_rows[-tail:]
            tail_src = sum(r["sources"] for r in tail_rows)
            tail_net = sum(r["net"] for r in tail_rows)
            share = (tail_net / tail_src) if tail_src > 0 else 0.0
            per_cur[c] = {"days": day_rows, "total_sources": tot_src, "total_sinks": tot_sink, "net_inflation": tot_src - tot_sink,
                          "sink_ratio": (tot_sink / tot_src) if tot_src > 0 else None, "tail_days": tail, "tail_net_share": share,
                          "start_balance": float(ctx.spec["currencies"][c].get("start", 0)), "end_balance": sm["end"]["balance"][c]}
        ex = sm["exhausted_at_minute"]
        ex_day = None if ex is None else ex / sm["minutes_per_day"]
        report[name] = {"currencies": per_cur, "exhausted_at_minute": ex, "exhausted_after_days": ex_day, "stalled_at_minute": sm["stalled_at_minute"],
                        "rebirths": sm["rebirths"]}
        if name in ctx.reference:
            if ex is not None:
                worst = max(per_cur.items(), key=lambda kv: kv[1]["tail_net_share"])
                if worst[1]["tail_net_share"] > thr:
                    findings.append(ctx.finding("runaway_inflation", f"{name}: content is exhausted after {ex_day:.2f} days and {worst[1]['tail_net_share']:.0%} of '{worst[0]}' "
                                                                     f"earned in the last {worst[1]['tail_days']} day(s) is unspent (limit {thr:.0%}).",
                                                archetype=name, currency=worst[0], value=worst[1]["tail_net_share"], limit=thr))
                if min_days is not None and ex_day < min_days:
                    findings.append(ctx.finding("content_exhausted", f"{name} has bought everything after {ex_day:.2f} days; at least {min_days:g} expected.",
                                                archetype=name, value=ex_day, limit=min_days))
        if sm["stalled_at_minute"] is not None:
            findings.append(ctx.finding("stalled", f"{name}: from minute {sm['stalled_at_minute']} upgrades remain but none can ever be afforded (cost currency without income).",
                                        archetype=name, value=sm["stalled_at_minute"]))
    return {"flows": report, "findings": findings, "sims": sims}


# ---------------------------------------------------------------------------------------------------------------------------
# dead options
# ---------------------------------------------------------------------------------------------------------------------------
def _state_for(ctx: Ctx, sm: dict, uid: str) -> tuple[set[str], int, str] | None:
    """The (owned set, rebirths) at which ``uid`` was, or would have been, decided. None if the archetype never got there."""
    m = ctx.model
    buys = [p for p in sm["purchases"] if p["kind"] == "upgrade"]
    p = next((p for p in buys if p["id"] == uid), None)
    if p:
        return set(p["_owned_before"]), p["_rebirths"], "bought"
    key = m.group_key(uid)
    sib = next((p for p in buys if (p["track"], p["tier"]) == key), None)
    if sib:
        return set(sib["_owned_before"]), sib["_rebirths"], "alternative_bought"
    owned = set(sm["end"]["owned"])
    blocked = {s for o in owned for s in m.groups[m.group_key(o)] if s != o}
    if uid in M.available(m, owned, blocked, sm["rebirths"], None):
        return owned, sm["rebirths"], "available_at_end"
    return None


def dead_options(ctx: Ctx, *, archetypes: list[str] | None = None, boost_mode: Any = None, seed: int | None = None, days: int | None = None) -> dict:
    sims = ctx.sims(archetypes, boost_mode=boost_mode, seed=seed, days=days)
    m = ctx.model
    thr = ctx.rng("max_payback_minutes", "max", 240)
    rows, findings = [], []
    for uid, u in m.upgrades.items():
        by: dict[str, Any] = {}
        how: dict[str, str] = {}
        for name, sm in sims.items():
            st = _state_for(ctx, sm, uid)
            if st is None:
                continue
            owned, rb, basis = st
            eff = sm["efficiency"]
            d = M.marginal(m, owned, sm["boosts"], rb, uid, eff)
            by[name] = None if d <= M.EPS else u["cost"] / d
            how[name] = basis
        vals = [v for v in by.values() if v is not None]
        if not by:
            status = "unreached"
        elif not vals:
            status = "no_effect"
        elif min(vals) > thr:
            status = "dead"
        else:
            status = "ok"
        rows.append({"upgrade": uid, "track": u["track"], "tier": int(u["tier"]), "cost": float(u["cost"]), "payback_minutes": by, "evaluated_at": how,
                     "best_payback_minutes": min(vals) if vals else None, "status": status})
        if status == "dead":
            findings.append(ctx.finding("dead_option", f"'{uid}' (tier {u['tier']}) needs at least {min(vals):.1f} active minutes to repay its cost "
                                                       f"(limit {thr:g}, best archetype).", upgrade=uid, tier=int(u["tier"]), value=min(vals), limit=thr))
        elif status == "no_effect":
            findings.append(ctx.finding("no_effect", f"'{uid}' changes no income at the state where it is decided.", upgrade=uid, tier=int(u["tier"])))
    return {"rows": rows, "findings": findings, "max_payback_minutes": thr}


# ---------------------------------------------------------------------------------------------------------------------------
# dominant strategies
# ---------------------------------------------------------------------------------------------------------------------------
def dominant(ctx: Ctx, *, archetypes: list[str] | None = None, boost_mode: Any = None, seed: int | None = None, days: int | None = None,
             forced_check: bool = True) -> dict:
    m = ctx.model
    sims = ctx.sims(archetypes, boost_mode=boost_mode, seed=seed, days=days)
    margin = ctx.rng("dominance_margin", "min", 0.1)
    groups = {k: v for k, v in sorted(m.groups.items(), key=lambda kv: (kv[0][1], kv[0][0])) if len(v) > 1}
    out_groups, findings = [], []
    wins: dict[str, list[bool]] = {}
    for key, opts in groups.items():
        per_arch: dict[str, Any] = {}
        for name, sm in sims.items():
            first = next((p for p in sm["purchases"] if p["kind"] == "upgrade" and (p["track"], p["tier"]) == key), None)
            if first is None:
                continue
            owned, rb = set(first["_owned_before"]), first["_rebirths"]
            rows = {}
            for o in opts:
                d = M.marginal(m, owned, sm["boosts"], rb, o, sm["efficiency"])
                rows[o] = {"cost": float(m.upgrades[o]["cost"]), "income_gain_per_min": d, "payback_minutes": None if d <= M.EPS else m.upgrades[o]["cost"] / d}
            per_arch[name] = rows
        forced: dict[str, Any] = {}
        if forced_check:
            for name in ctx.reference:
                if name not in per_arch:
                    continue
                cur = m.upgrades[opts[0]]["currency"]
                for o in opts:
                    fs = M.simulate(ctx.spec, name, boost_mode=boost_mode, seed=seed, days=days, force={key: o})
                    forced.setdefault(name, {})[o] = {"income_rate_at_end": fs["end"]["rates"][cur], "purchases": len(fs["purchases"])}
        winner = None
        dominated: list[str] = []
        if per_arch:
            cands = []
            for o in opts:
                ok = True
                for name, rows in per_arch.items():
                    mine = rows[o]["payback_minutes"]
                    if mine is None:
                        ok = False
                        break
                    for o2 in opts:
                        if o2 == o:
                            continue
                        other = rows[o2]["payback_minutes"]
                        if other is not None and not mine <= (1.0 - margin) * other:
                            ok = False
                if ok and forced_check:
                    for name, fr in forced.items():
                        if any(fr[o]["income_rate_at_end"] + 1e-9 < fr[o2]["income_rate_at_end"] for o2 in opts if o2 != o):
                            ok = False
                if ok:
                    cands.append(o)
            if len(cands) == 1:
                winner = cands[0]
            for o in opts:
                for o2 in opts:
                    if o == o2:
                        continue
                    if all(rows[o2]["cost"] >= rows[o]["cost"] and rows[o2]["income_gain_per_min"] <= rows[o]["income_gain_per_min"]
                           and (rows[o2]["cost"] > rows[o]["cost"] or rows[o2]["income_gain_per_min"] < rows[o]["income_gain_per_min"]) for rows in per_arch.values()):
                        if o2 not in dominated:
                            dominated.append(o2)
        best = None
        if ctx.reference and ctx.reference[0] in per_arch:
            pbs = {o: r["payback_minutes"] for o, r in per_arch[ctx.reference[0]].items() if r["payback_minutes"] is not None}
            if pbs:
                lo = min(pbs.values())
                leaders = [o for o, v in pbs.items() if abs(v - lo) <= 1e-9]
                best = leaders[0] if len(leaders) == 1 else None
        for o in opts:
            strat = m.upgrades[o].get("strategy")
            if strat and per_arch:
                wins.setdefault(strat, []).append(best == o)
        out_groups.append({"track": key[0], "tier": key[1], "options": opts, "per_archetype": per_arch, "forced_outcomes": forced, "dominant": winner, "dominated": dominated,
                           "best_payback_option": best})
        if winner:
            others = [o for o in opts if o != winner]
            findings.append(ctx.finding("dominant_option", f"At track '{key[0]}' tier {key[1]}, '{winner}' repays faster than {others} for every archetype "
                                                           f"(margin {margin:.0%}) and ends with at least as much income, so the choice is not a choice.",
                                        track=key[0], tier=key[1], upgrade=winner, versus=others))
        for d in dominated:
            findings.append(ctx.finding("dominated_option", f"'{d}' (track '{key[0]}' tier {key[1]}) costs at least as much as an alternative and gives no more income.",
                                        track=key[0], tier=key[1], upgrade=d))
    strategies = []
    n_groups = len(groups)
    for strat, flags in sorted(wins.items()):
        strategies.append({"strategy": strat, "groups_won": sum(flags), "contested_groups": len(flags)})
        if len(flags) >= 2 and all(flags):
            findings.append(ctx.finding("dominant_strategy", f"Strategy '{strat}' has the best payback in all {len(flags)} contested choices.", strategy=strat, groups=len(flags)))
    return {"groups": out_groups, "strategies": strategies, "contested_groups": n_groups, "findings": findings, "dominance_margin": margin}


# ---------------------------------------------------------------------------------------------------------------------------
# monetisation
# ---------------------------------------------------------------------------------------------------------------------------
def monetisation(ctx: Ctx, *, seed: int | None = None, days: int | None = None) -> dict:
    m = ctx.model
    if not m.boosts:
        return {"boosts": [], "note": "the spec defines no boosts, so there is nothing to check", "rows": [], "per_boost": [], "findings": []}
    speed_thr = ctx.rng("max_boost_speedup", "max", 3.0)
    free_thr = ctx.rng("max_free_gap_over_band", "max", 2.0)
    findings, rows, per_boost = [], [], []
    for name in ctx.reference:
        free = ctx.sims([name], boost_mode="none", seed=seed, days=days)[name]
        boosted = ctx.sims([name], boost_mode="all", seed=seed, days=days)[name]
        fp = {p["id"]: p for p in free["purchases"] if p["kind"] == "upgrade" and p["lap"] == 0}
        bp = {p["id"]: p for p in boosted["purchases"] if p["kind"] == "upgrade" and p["lap"] == 0}
        worst = None
        for uid, p in fp.items():
            if uid not in bp:
                continue
            q = bp[uid]
            band = ctx.band(p["tier"])
            speed = (p["minute"] / q["minute"]) if q["minute"] > 0 else None
            rows.append({"archetype": name, "upgrade": uid, "tier": p["tier"], "minute_free": p["minute"], "minute_boosted": q["minute"], "gap_free": p["gap_minutes"],
                         "gap_boosted": q["gap_minutes"], "speedup": speed, "band": band["id"] if band else None})
            if speed is not None and (worst is None or speed > worst["speedup"]):
                worst = rows[-1]
            if band:
                if p["gap_minutes"] > free_thr * band["max_minutes"] and q["gap_minutes"] <= band["max_minutes"]:
                    findings.append(ctx.finding("free_cliff", f"{name} without boosts needs {p['gap_minutes']:g} active minutes for '{uid}' (tier {p['tier']}); that is more than "
                                                               f"{free_thr:g}x the band maximum {band['max_minutes']:g}, while boosted play needs {q['gap_minutes']:g}.",
                                                archetype=name, upgrade=uid, tier=p["tier"], value=p["gap_minutes"], limit=free_thr * band["max_minutes"]))
                if q["gap_minutes"] < band["min_minutes"] <= p["gap_minutes"]:
                    findings.append(ctx.finding("boost_trivialises", f"{name} with boosts needs only {q['gap_minutes']:g} active minutes for '{uid}' (band minimum {band['min_minutes']:g}).",
                                                archetype=name, upgrade=uid, tier=p["tier"], value=q["gap_minutes"], limit=band["min_minutes"]))
        if worst and worst["speedup"] > speed_thr:
            findings.append(ctx.finding("pay_to_win", f"{name} reaches '{worst['upgrade']}' (tier {worst['tier']}) {worst['speedup']:.2f}x faster with all boosts "
                                                      f"({worst['minute_boosted']:g} vs {worst['minute_free']:g} active minutes); limit {speed_thr:g}x.",
                                        archetype=name, upgrade=worst["upgrade"], tier=worst["tier"], value=worst["speedup"], limit=speed_thr))
        last = max(fp.values(), key=lambda p: p["minute"], default=None)
        for bid in m.boosts:
            solo = ctx.sims([name], boost_mode=[bid], seed=seed, days=days)[name]
            sp = {p["id"]: p for p in solo["purchases"] if p["kind"] == "upgrade" and p["lap"] == 0}
            if last and last["id"] in sp and sp[last["id"]]["minute"] > 0:
                per_boost.append({"archetype": name, "boost": bid, "reference_upgrade": last["id"], "minute_free": last["minute"], "minute_with_boost": sp[last["id"]]["minute"],
                                  "speedup": last["minute"] / sp[last["id"]]["minute"]})
            else:
                per_boost.append({"archetype": name, "boost": bid, "reference_upgrade": last["id"] if last else None, "minute_free": last["minute"] if last else None,
                                  "minute_with_boost": None, "speedup": None})
    return {"boosts": list(m.boosts), "rows": rows, "per_boost": per_boost, "findings": findings, "max_boost_speedup": speed_thr, "max_free_gap_over_band": free_thr}


# ---------------------------------------------------------------------------------------------------------------------------
# sensitivity and comparison
# ---------------------------------------------------------------------------------------------------------------------------
def _round_like(old: Any, new: float) -> Any:
    if isinstance(old, int) and not isinstance(old, bool):
        return int(round(new))
    return float(f"{new:.6g}")


def sensitivity(ctx: Ctx, path: str, *, values: list[float] | None = None, pct: list[float] | None = None, archetypes: list[str] | None = None,
                seed: int | None = None, days: int | None = None) -> dict:
    base = S.get_value(ctx.spec, path)
    if not S.is_number(base):
        raise ValueError(f"'{path}' is not a number (found {base!r})")
    if (values is None) == (pct is None):
        raise ValueError("pass exactly one of values (absolute numbers) or pct (percent changes such as [-20, -10, 10, 20])")
    variants: list[tuple[str, Any]] = [("base", base)]
    for v in values or []:
        variants.append((f"={v:g}", v))
    for p in pct or []:
        variants.append((f"{p:+g}%", _round_like(base, base * (1 + p / 100.0))))
    if len(variants) == 1:
        raise ValueError("give at least one value or pct to compare against the base")
    if len(variants) > 12:
        raise ValueError("at most 11 variants per call")
    names = archetypes or list(ctx.spec["archetypes"])
    tables: dict[str, dict[str, dict]] = {n: {} for n in names}
    for label, val in variants:
        spec2 = S.set_value(ctx.spec, path, val)
        errs = S.errors_of(S.validate(spec2))
        if errs:
            raise ValueError(f"the value {val} makes the spec invalid: {errs[0]['message']}")
        sims = M.simulate_all(spec2, archetypes=names, seed=seed, days=days)
        for n in names:
            for p in sims[n]["purchases"]:
                if p["lap"] != 0:
                    continue
                t = tables[n].setdefault(p["id"], {"upgrade": p["id"], "tier": p["tier"], "kind": p["kind"], "minutes": {}, "gaps": {}})
                t["minutes"][label] = p["minute"]
                t["gaps"][label] = p["gap_minutes"]
    out_tables = {}
    for n, tab in tables.items():
        rows = []
        for t in sorted(tab.values(), key=lambda t: min(t["minutes"].values())):
            b = t["minutes"].get("base")
            row = {"upgrade": t["upgrade"], "tier": t["tier"], "kind": t["kind"], "minute": {lab: t["minutes"].get(lab) for lab, _ in variants},
                   "gap_minutes": {lab: t["gaps"].get(lab) for lab, _ in variants},
                   "minute_delta_vs_base": {lab: (None if b is None or lab not in t["minutes"] else t["minutes"][lab] - b) for lab, _ in variants if lab != "base"}}
            rows.append(row)
        out_tables[n] = rows
    moved = {}
    for n, rows in out_tables.items():
        moved[n] = sum(1 for r in rows for d in r["minute_delta_vs_base"].values() if d not in (0, None))
    return {"path": path, "base_value": base, "variants": [{"label": l, "value": v} for l, v in variants], "tables": out_tables, "moved_cells": moved,
            "locked": path in S.locked_paths(ctx.spec),
            "note": "what-if analysis; a locked value can be varied here but a proposal can never change it"}


def compare(ctx_a: Ctx, ctx_b: Ctx, *, archetypes: list[str] | None = None, seed: int | None = None, days: int | None = None) -> dict:
    names = archetypes or [n for n in ctx_a.spec["archetypes"] if n in ctx_b.spec["archetypes"]]
    sa, sb = ctx_a.sims(names, seed=seed, days=days), ctx_b.sims(names, seed=seed, days=days)
    timing = {}
    for n in names:
        pa = {p["id"]: p for p in sa[n]["purchases"] if p["lap"] == 0}
        pb = {p["id"]: p for p in sb[n]["purchases"] if p["lap"] == 0}
        rows = []
        for uid in sorted(set(pa) | set(pb), key=lambda u: (pa.get(u) or pb.get(u))["minute"]):
            a, b = pa.get(uid), pb.get(uid)
            rows.append({"upgrade": uid, "tier": (a or b)["tier"], "minute_a": a and a["minute"], "minute_b": b and b["minute"], "gap_a": a and a["gap_minutes"],
                         "gap_b": b and b["gap_minutes"], "minute_delta": (b["minute"] - a["minute"]) if a and b else None})
        timing[n] = rows
    ca = {f["code"] + "|" + str(f.get("archetype", "")) + "|" + str(f.get("upgrade", "")) for f in check(ctx_a)["findings"]}
    cb = {f["code"] + "|" + str(f.get("archetype", "")) + "|" + str(f.get("upgrade", "")) for f in check(ctx_b)["findings"]}
    return {"value_changes": S.diff_values(ctx_a.spec, ctx_b.spec), "timing": timing, "findings_only_in_a": sorted(ca - cb), "findings_only_in_b": sorted(cb - ca)}


# ---------------------------------------------------------------------------------------------------------------------------
# everything at once
# ---------------------------------------------------------------------------------------------------------------------------
def metrics(ctx: Ctx, tt: dict, fl: dict, mon: dict) -> dict[str, float]:
    """Headline numbers measured against style.ranges (worst case over reference archetypes)."""
    m: dict[str, float] = {}
    rows = [r for r in tt["rows"] if r["kind"] == "upgrade" and r["lap"] == 0]
    ref = [r for r in rows if r["archetype"] in ctx.reference]
    ratios = [r["gap_minutes"] / r["band_max"] for r in ref if r["band_max"]]
    if ratios:
        m["wall_factor"] = max(ratios)
    gaps = []
    for name in ctx.reference:
        sm = tt["sims"][name]
        seq = [p for p in sm["purchases"] if p["lap"] == 0 and p["kind"] == "upgrade"]
        gaps += [b["gap_minutes"] / a["gap_minutes"] for a, b in zip(seq, seq[1:]) if a["gap_minutes"] >= 1]
        by_track: dict[str, dict] = {}
        for p in seq:
            prev = by_track.get(p["track"])
            if prev:
                m["max_cost_jump_ratio"] = max(m.get("max_cost_jump_ratio", 0.0), p["cost"] / prev["cost"])
            by_track[p["track"]] = p
        m["max_idle_minutes"] = max([m.get("max_idle_minutes", 0.0)] + [p["gap_minutes"] for p in sm["purchases"] if p["lap"] == 0 and ctx.band(p["tier"]) is None])
    if gaps:
        m["max_gap_jump_ratio"] = max(gaps)
    if rows:
        m["max_days_per_step"] = max(r["gap_days"] for r in rows)
    exh = [fl["flows"][n]["exhausted_after_days"] for n in ctx.reference]
    m["min_content_days"] = min([ctx.spec["simulation"]["days"] if e is None else e for e in exh])
    tails = [c["tail_net_share"] for n in ctx.reference for c in fl["flows"][n]["currencies"].values()]
    if tails:
        m["max_tail_net_share"] = max(tails) if any(fl["flows"][n]["exhausted_at_minute"] is not None for n in ctx.reference) else 0.0
    sp = [r["speedup"] for r in mon.get("rows", []) if r["speedup"]]
    if sp:
        m["max_boost_speedup"] = max(sp)
    return m


def check(ctx: Ctx, *, seed: int | None = None, days: int | None = None) -> dict:
    """Run every analysis and return one verdict. ``pass`` means no error and no warning findings."""
    tt = time_to_upgrade(ctx, seed=seed, days=days)
    fl = flow(ctx, seed=seed, days=days)
    dd = dead_options(ctx, seed=seed, days=days)
    dom = dominant(ctx, seed=seed, days=days)
    mon = monetisation(ctx, seed=seed, days=days)
    findings = sort_findings(tt["findings"] + fl["findings"] + dd["findings"] + dom["findings"] + mon["findings"])
    for f in S.validate(ctx.spec):
        if f["severity"] == "warning" and f["code"] == "zero_effect_gate":
            findings.append(ctx.finding("zero_effect_gate", f["message"], path=f["path"]))
    findings = sort_findings(findings)
    mets = metrics(ctx, tt, fl, mon)
    rng_findings = [f for f in check_ranges(mets, {k: v for k, v in ctx.style.get("ranges", {}).items() if k in mets}) if f["severity"] != "info"]
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
    return {"verdict": "pass" if not counts["error"] and not counts["warning"] else "fail", "counts": counts, "findings": findings, "metrics": mets,
            "range_findings": rng_findings, "parts": {"timing": tt, "flow": fl, "dead": dd, "dominant": dom, "monetisation": mon}}


RUBRIC_CODES = {
    "no_walls": {"wall"}, "no_runaway_currency": {"runaway_inflation"}, "pacing_in_band": {"over_band", "under_band"}, "smooth_curve": {"cost_cliff", "gap_cliff", "slow_for_archetype"},
    "choices_are_real": {"dominant_option", "dominant_strategy"}, "no_dead_options": {"dead_option"}, "content_lasts": {"content_exhausted"}, "monetisation_fair": {"pay_to_win", "free_cliff"},
}


def rubric_auto_scores(findings: list[dict]) -> dict[str, bool]:
    """Pass/fail for each automatic rubric criterion from a check() result's findings (info findings never fail a criterion)."""
    bad = {f["code"] for f in findings if f["severity"] in ("error", "warning")}
    scores = {cid: not (codes & bad) for cid, codes in RUBRIC_CODES.items()}
    scores["spec_valid"] = True  # check() refuses to run on an invalid spec
    scores["locks_respected"] = True  # enforced inside propose_rebalance; there is nothing to measure on a spec alone
    return scores
