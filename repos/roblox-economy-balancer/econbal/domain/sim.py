"""The deterministic, seeded progression simulator. Pure Python, no I/O.

MODEL (every analysis in this package is a view of this one model; nothing else produces a number)
-------------------------------------------------------------------------------------------------
Time.   The simulated player plays ACTIVE minutes back to back. An archetype plays ``sessions_per_day`` sessions of
        ``session_minutes`` each, so one calendar day is ``sessions_per_day * session_minutes`` active minutes. Time between
        sessions, offline earnings and idle waiting are not modelled; calendar days are active minutes divided by that length.
Income. A source earns ``rate`` currency per active minute::

            rate = (per_minute + sum(add effects of owned upgrades/boosts on that source)) * product(mult effects) * rebirth multiplier

        ``add`` effects apply before ``mult`` effects. A source with ``unlock`` earns nothing until that upgrade is owned. The
        archetype multiplies every source by ``efficiency`` and, if ``variance`` > 0, each SESSION by a factor
        ``1 + variance * (2u - 1)`` where ``u`` is drawn from ``random.Random("<seed>:<archetype>")``. With ``variance = 0``
        nothing random happens and the run is plain arithmetic. Same spec + seed => identical output.
Sinks.  ``tax`` sinks remove a fraction of gross income of their currency. ``upkeep`` sinks remove ``value`` per active minute, but never
        more than the balance (a balance cannot go negative). Upgrades and rebirths are the other sinks.
Buying. Whenever the player can decide, ``policy`` chooses ONE target among the available upgrades, and the player SAVES for it (buys nothing
        else meanwhile). A purchase happens at the end of the first whole minute in which the balance covers the cost.
        ``cheapest_payback`` (default): rank by payback = cost / (increase of net income per active minute the upgrade causes, measured in the
        cost currency at the current state, efficiency applied, no jitter). Upgrades with no income effect rank last; ties break on cost then spec order.
        ``cheapest_cost``: the cheapest available upgrade. ``spec_order``: the first available upgrade in the spec.
        An upgrade is available when it is not owned, no alternative at its (track, tier) was bought, tier 1 or an upgrade of the same track at
        tier - 1 is owned, every ``requires`` is owned, and ``min_rebirths`` is met. Upgrades whose cost currency earns nothing are skipped.
Boosts. A boost owned by the archetype (or all/none via ``boost_mode``) applies its effects from minute 0, exactly like an upgrade.
Rebirth. When ``rebirth: ladder_complete`` (default) and every track in ``resets.tracks`` is complete (an upgrade at its top tier is owned), the
        rebirth becomes a target ranked after income-improving upgrades. Buying it pays ``cost``, removes the upgrades of the reset tracks,
        resets the listed currencies to their start value (leftovers are counted as the sink ``rebirth_reset``), and multiplies the bonus
        sources by ``1 + mult_per_rebirth * n`` (or ``mult_compound ** n``) after ``n`` rebirths, up to ``max``.
Output. ``purchases`` (with the gap in active minutes since the previous purchase), per-day flows, balances and income rates.

Granularity: whole active minutes. Purchases are found by fast-forwarding to the exact minute, which gives the same answer as stepping
minute by minute (a test checks this against a naive stepper).
"""

from __future__ import annotations

import math
import random
from typing import Any

from . import spec as S

EPS = 1e-9


def _ceil(x: float) -> int:
    return int(math.ceil(x - EPS))


class Model:
    """Read-only lookup tables built once from a normalised spec."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.sources = spec["sources"]
        self.src_ids = [s["id"] for s in self.sources]
        self.upgrades = {u["id"]: u for u in spec["upgrades"]}
        self.order = {u["id"]: i for i, u in enumerate(spec["upgrades"])}
        self.boosts = {b["id"]: b for b in spec["boosts"]}
        self.currencies = list(spec["currencies"])
        self.groups: dict[tuple[str, int], list[str]] = {}
        for u in spec["upgrades"]:
            self.groups.setdefault((u["track"], int(u["tier"])), []).append(u["id"])
        self.track_top = {}
        for (track, tier) in self.groups:
            self.track_top[track] = max(self.track_top.get(track, 0), tier)
        self.tax: dict[str, list[tuple[str, float]]] = {c: [] for c in self.currencies}
        self.upkeep: dict[str, list[tuple[str, float]]] = {c: [] for c in self.currencies}
        for sk in spec["sinks"]:
            (self.tax if sk["kind"] == "tax" else self.upkeep)[sk["currency"]].append((sk["id"], float(sk["value"])))
        self.rebirth = spec["rebirths"][0] if spec["rebirths"] else None
        self.days = spec["simulation"]["days"]
        self.seed = spec["simulation"]["seed"]
        self.policy = spec["simulation"]["policy"]

    def group_key(self, uid: str) -> tuple[str, int]:
        u = self.upgrades[uid]
        return u["track"], int(u["tier"])


def source_rates(m: Model, owned: set[str], boosts: list[str], rebirths: int) -> dict[str, float]:
    """Income per active minute of every source for this state (before archetype efficiency and jitter)."""
    effects: list[dict] = []
    for uid in owned:
        effects.extend(m.upgrades[uid]["effects"])
    for bid in boosts:
        effects.extend(m.boosts[bid]["effects"])
    bonus_mult = 1.0
    bonus_src = None
    if m.rebirth and rebirths > 0:
        b = m.rebirth["bonus"]
        bonus_src = b["source"]
        bonus_mult = (1.0 + b["mult_per_rebirth"] * rebirths) if "mult_per_rebirth" in b else b["mult_compound"] ** rebirths
    out = {}
    for s in m.sources:
        if s.get("unlock") and s["unlock"] not in owned:
            out[s["id"]] = 0.0
            continue
        add, mult = 0.0, 1.0
        for e in effects:
            if e["source"] in (s["id"], "*"):
                if "add" in e:
                    add += e["add"]
                else:
                    mult *= e["mult"]
        if bonus_src in (s["id"], "*"):
            mult *= bonus_mult
        out[s["id"]] = (s["per_minute"] + add) * mult
    return out


def currency_rates(m: Model, rates: dict[str, float], eff: float, f: float = 1.0) -> dict[str, dict[str, float]]:
    """Per currency: gross income, tax fraction, upkeep and the resulting net income per active minute."""
    out = {}
    for c in m.currencies:
        gross = sum(rates[s["id"]] for s in m.sources if s["currency"] == c) * eff * f
        tax = min(1.0, sum(v for _, v in m.tax[c]))
        upkeep = sum(v for _, v in m.upkeep[c])
        out[c] = {"gross": gross, "tax": tax, "upkeep": upkeep, "net": gross * (1.0 - tax) - upkeep}
    return out


def net_rate(m: Model, owned: set[str], boosts: list[str], rebirths: int, currency: str, eff: float) -> float:
    return currency_rates(m, source_rates(m, owned, boosts, rebirths), eff)[currency]["net"]


def available(m: Model, owned: set[str], blocked: set[str], rebirths: int, force: dict | None) -> list[str]:
    out = []
    for uid, u in m.upgrades.items():
        if uid in owned or uid in blocked:
            continue
        key = (u["track"], int(u["tier"]))
        if force and key in force and force[key] != uid:
            continue
        if u["tier"] > 1 and not any(o in owned for o in m.groups.get((u["track"], int(u["tier"]) - 1), [])):
            continue
        if any(r not in owned for r in u["requires"]):
            continue
        if rebirths < u["min_rebirths"]:
            continue
        out.append(uid)
    return out


def ladder_complete(m: Model, owned: set[str]) -> bool:
    rb = m.rebirth
    if not rb:
        return False
    for track in rb["resets"]["tracks"]:
        top = m.track_top.get(track, 0)
        if not any(o in owned for o in m.groups.get((track, top), [])):
            return False
    return True


def marginal(m: Model, owned: set[str], boosts: list[str], rebirths: int, uid: str, eff: float) -> float:
    """Increase of net income per active minute (in the upgrade's cost currency) if ``uid`` were bought now."""
    c = m.upgrades[uid]["currency"]
    before = net_rate(m, owned, boosts, rebirths, c, eff)
    after = net_rate(m, owned | {uid}, boosts, rebirths, c, eff)
    return after - before


def payback_minutes(m: Model, owned: set[str], boosts: list[str], rebirths: int, uid: str, eff: float) -> float | None:
    d = marginal(m, owned, boosts, rebirths, uid, eff)
    return None if d <= EPS else m.upgrades[uid]["cost"] / d


def resolve_boosts(m: Model, archetype: dict, boost_mode: Any) -> list[str]:
    if boost_mode in (None, "archetype"):
        return list(archetype.get("boosts") or [])
    if boost_mode == "none":
        return []
    if boost_mode == "all":
        return list(m.boosts)
    if isinstance(boost_mode, (list, tuple)):
        unknown = [b for b in boost_mode if b not in m.boosts]
        if unknown:
            raise ValueError(f"unknown boost(s) {unknown}. Known: {sorted(m.boosts)}")
        return list(boost_mode)
    raise ValueError("boost_mode must be 'archetype', 'none', 'all' or a list of boost ids")


def archetype_minutes(a: dict) -> tuple[int, int, int]:
    sm, spd = int(a["session_minutes"]), int(a["sessions_per_day"])
    return sm, spd, sm * spd


def simulate(spec: dict, archetype: str, *, seed: int | None = None, days: int | None = None, boost_mode: Any = None,
             force: dict | None = None, policy: str | None = None) -> dict:
    """Simulate one archetype. ``spec`` must be normalised and valid. Returns plain dicts (floats are unrounded)."""
    m = Model(spec)
    if archetype not in spec["archetypes"]:
        raise ValueError(f"unknown archetype '{archetype}'. Known: {sorted(spec['archetypes'])}")
    a = spec["archetypes"][archetype]
    policy = policy or a.get("policy") or m.policy
    if policy not in S.POLICIES:
        raise ValueError(f"policy must be one of {S.POLICIES}")
    seed = m.seed if seed is None else seed
    days = m.days if days is None else days
    if not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")
    boosts = resolve_boosts(m, a, boost_mode)
    eff = float(a["efficiency"])
    variance = float(a["variance"])
    sm, spd, dpm = archetype_minutes(a)
    horizon = days * dpm
    rng = random.Random(f"{seed}:{archetype}")
    jitter = [1.0 + variance * (2.0 * rng.random() - 1.0) for _ in range(days * spd)]

    owned: set[str] = set()
    blocked: set[str] = set()
    bal = {c: float(v.get("start", 0)) for c, v in spec["currencies"].items()}
    rebirths = 0
    minute = 0
    last_event = 0
    purchases: list[dict] = []
    daily: list[dict] = [{"sources": {c: 0.0 for c in m.currencies}, "sinks": {c: {} for c in m.currencies}} for _ in range(days)]
    snapshots: dict[int, dict] = {}
    rate_trace: list[dict] = []
    exhausted_at: int | None = None
    stalled_at: int | None = None
    target: dict | None = None
    dirty = True
    rb = m.rebirth
    rebirth_ok = bool(rb) and a["rebirth"] == "ladder_complete"

    def sink_add(day: int, cur: str, name: str, amount: float) -> None:
        if amount:
            d = daily[day]["sinks"][cur]
            d[name] = d.get(name, 0.0) + amount

    def trace() -> None:
        cr = currency_rates(m, source_rates(m, owned, boosts, rebirths), eff)
        rate_trace.append({"minute": minute, "rates": {c: cr[c]["net"] for c in m.currencies}, "gross": {c: cr[c]["gross"] for c in m.currencies}})

    def choose() -> tuple[dict | None, bool]:
        """(target, any_candidate_left)."""
        avail = available(m, owned, blocked, rebirths, force)
        cr = currency_rates(m, source_rates(m, owned, boosts, rebirths), eff)
        cand = []
        for uid in avail:
            u = m.upgrades[uid]
            reach = cr[u["currency"]]["net"] > EPS or bal[u["currency"]] + EPS >= u["cost"]
            pb = payback_minutes(m, owned, boosts, rebirths, uid, eff)
            cand.append((uid, u, reach, pb))
        rb_cand = None
        if rebirth_ok and rebirths < rb["max"] and ladder_complete(m, owned):
            reach = cr[rb["currency"]]["net"] > EPS or bal[rb["currency"]] + EPS >= rb["cost"]
            rb_cand = {"kind": "rebirth", "id": rb["id"], "currency": rb["currency"], "cost": float(rb["cost"]), "reach": reach}
        anything = bool(cand) or rb_cand is not None
        reach = [c for c in cand if c[2]]
        if policy == "cheapest_payback":
            fin = sorted((c for c in reach if c[3] is not None), key=lambda c: (c[3], c[1]["cost"], m.order[c[0]]))
            zero = sorted((c for c in reach if c[3] is None), key=lambda c: (c[1]["cost"], m.order[c[0]]))
            ranked: list[Any] = fin + ([rb_cand] if rb_cand and rb_cand["reach"] else []) + zero
        else:
            key = (lambda c: (c[1]["cost"], m.order[c[0]])) if policy == "cheapest_cost" else (lambda c: m.order[c[0]])
            ranked = sorted(reach, key=key) + ([rb_cand] if rb_cand and rb_cand["reach"] else [])
        if not ranked:
            return None, anything
        top = ranked[0]
        if isinstance(top, dict):
            return top, True
        uid, u, _, pb = top
        return {"kind": "upgrade", "id": uid, "currency": u["currency"], "cost": float(u["cost"]), "payback": pb}, True

    def snap(day: int) -> None:
        if day in snapshots or not 0 <= day < days:
            return
        cr = currency_rates(m, source_rates(m, owned, boosts, rebirths), eff)
        snapshots[day] = {"balance_end": dict(bal), "income_rate_end": {c: cr[c]["net"] for c in m.currencies}}

    trace()
    while True:
        # --- buy everything the policy wants and the balance covers (several purchases may share a minute) ---
        while True:
            if dirty:
                target, anything = choose()
                dirty = False
                if not anything and exhausted_at is None:
                    exhausted_at = minute
                if anything and target is None and stalled_at is None:
                    stalled_at = minute
            if target is None or bal[target["currency"]] + EPS * max(1.0, target["cost"]) < target["cost"]:
                break
            day = max(0, (minute - 1) // dpm) if minute > 0 else 0
            day = min(day, days - 1)
            rows_before = None
            if target["kind"] == "upgrade":
                u = m.upgrades[target["id"]]
                rate_before = net_rate(m, owned, boosts, rebirths, u["currency"], eff)
                rows_before = frozenset(owned)
                bal[u["currency"]] -= u["cost"]
                sink_add(day, u["currency"], "upgrades", u["cost"])
                owned.add(u["id"])
                for sib in m.groups[(u["track"], int(u["tier"]))]:
                    if sib != u["id"]:
                        blocked.add(sib)
                rate_after = net_rate(m, owned, boosts, rebirths, u["currency"], eff)
                purchases.append({"kind": "upgrade", "id": u["id"], "track": u["track"], "tier": int(u["tier"]), "currency": u["currency"],
                                  "cost": float(u["cost"]), "minute": minute, "day": day, "lap": rebirths, "gap_minutes": minute - last_event,
                                  "rate_before": rate_before, "rate_after": rate_after,
                                  "payback_minutes": None if rate_after - rate_before <= EPS else u["cost"] / (rate_after - rate_before),
                                  "options": [x for x in m.groups[(u["track"], int(u["tier"]))]], "_owned_before": rows_before,
                                  "_rebirths": rebirths})
            else:
                cur = rb["currency"]
                rate_before = net_rate(m, owned, boosts, rebirths, cur, eff)
                rows_before = frozenset(owned)
                bal[cur] -= rb["cost"]
                sink_add(day, cur, "rebirth", rb["cost"])
                for track in rb["resets"]["tracks"]:
                    for uid in [x for x in owned if m.upgrades[x]["track"] == track]:
                        owned.discard(uid)
                    for uid in [x for x in blocked if m.upgrades[x]["track"] == track]:
                        blocked.discard(uid)
                for c in rb["resets"]["currencies"]:
                    start = float(spec["currencies"][c].get("start", 0))
                    left = bal[c] - start
                    if left > 0:
                        sink_add(day, c, "rebirth_reset", left)
                    elif left < 0:
                        daily[day]["sources"][c] += -left  # a start balance above what was held is created currency
                    bal[c] = start
                rebirths += 1
                rate_after = net_rate(m, owned, boosts, rebirths, cur, eff)
                purchases.append({"kind": "rebirth", "id": rb["id"], "track": None, "tier": None, "currency": cur, "cost": float(rb["cost"]),
                                  "minute": minute, "day": day, "lap": rebirths - 1, "gap_minutes": minute - last_event,
                                  "rate_before": rate_before, "rate_after": rate_after, "payback_minutes": None, "options": [],
                                  "_owned_before": rows_before, "_rebirths": rebirths - 1})
            last_event = minute
            dirty = True
            trace()
        if minute >= horizon:
            break
        if minute > 0 and minute % dpm == 0:
            snap(minute // dpm - 1)
        # --- advance time to the next event: session end, day end, horizon, or the minute the target becomes affordable ---
        sess_left = (minute // sm + 1) * sm - minute
        day_left = (minute // dpm + 1) * dpm - minute
        k = min(sess_left, day_left, horizon - minute)
        f = jitter[min(minute // sm, len(jitter) - 1)]
        cr = currency_rates(m, source_rates(m, owned, boosts, rebirths), eff, f)
        if target is not None:
            net_t = cr[target["currency"]]["net"]
            if net_t > EPS:
                need = max(1, _ceil((target["cost"] - bal[target["currency"]]) / net_t))
                k = min(k, need)
        day = minute // dpm
        for c in m.currencies:
            r = cr[c]
            g = r["gross"] * k
            tax_total = g * r["tax"]
            req = r["upkeep"] * k
            new_bal = max(0.0, bal[c] + g - tax_total - req)
            upkeep_paid = bal[c] + g - tax_total - new_bal
            bal[c] = new_bal
            daily[day]["sources"][c] += g
            tsum = sum(v for _, v in m.tax[c])
            for name, v in m.tax[c]:
                sink_add(day, c, name, tax_total * v / tsum if tsum else 0.0)
            usum = sum(v for _, v in m.upkeep[c])
            for name, v in m.upkeep[c]:
                sink_add(day, c, name, upkeep_paid * v / usum if usum else 0.0)
        minute += k
    snap(days - 1)
    pending = None
    if target is not None:
        net_end = rate_trace[-1]["rates"][target["currency"]]
        need = target["cost"] - bal[target["currency"]]
        waited = minute - last_event
        pending = {"kind": target["kind"], "id": target["id"], "currency": target["currency"], "cost": target["cost"], "balance": bal[target["currency"]],
                   "waited_minutes": waited, "lap": rebirths,
                   "projected_gap_minutes": (waited + _ceil(need / net_end)) if net_end > EPS and need > 0 else None}
        if target["kind"] == "upgrade":
            pending["tier"] = int(m.upgrades[target["id"]]["tier"])
            pending["track"] = m.upgrades[target["id"]]["track"]
    for d in range(days):
        if d not in snapshots:  # a day boundary that coincided with the horizon is covered above; fill any gap defensively
            snapshots[d] = snapshots.get(d - 1, {"balance_end": dict(bal), "income_rate_end": {}})
    rows = []
    for d in range(days):
        rows.append({"day": d, "sources": daily[d]["sources"], "sinks": daily[d]["sinks"], **snapshots[d]})
    return {
        "archetype": archetype, "seed": seed, "days": days, "policy": policy, "boosts": boosts, "efficiency": eff,
        "session_minutes": sm, "sessions_per_day": spd, "minutes_per_day": dpm, "horizon_minutes": horizon,
        "purchases": purchases, "rebirths": rebirths, "daily": rows, "rate_trace": rate_trace,
        "exhausted_at_minute": exhausted_at, "stalled_at_minute": stalled_at, "pending": pending,
        "end": {"balance": dict(bal), "owned": sorted(owned), "rates": rate_trace[-1]["rates"]},
        "currencies": list(m.currencies),
    }


def simulate_all(spec: dict, *, archetypes: list[str] | None = None, **kw: Any) -> dict[str, dict]:
    names = archetypes or list(spec["archetypes"])
    return {n: simulate(spec, n, **kw) for n in names}


def reference_stepper(spec: dict, archetype: str, minutes: int) -> list[tuple[int, str]]:
    """A deliberately naive minute-by-minute implementation of the same model for ``cheapest_payback`` without upkeep, tax, jitter or rebirth.

    Used only by tests to check the fast-forwarding simulator. Returns [(minute, upgrade id)].
    """
    m = Model(spec)
    a = spec["archetypes"][archetype]
    eff = float(a["efficiency"])
    boosts = list(a.get("boosts") or [])
    owned: set[str] = set()
    blocked: set[str] = set()
    bal = {c: float(v.get("start", 0)) for c, v in spec["currencies"].items()}
    out = []

    def buy_all(t: int) -> None:
        while True:
            av = available(m, owned, blocked, 0, None)
            cr = currency_rates(m, source_rates(m, owned, boosts, 0), eff)
            cand = []
            for uid in av:
                u = m.upgrades[uid]
                if cr[u["currency"]]["net"] <= EPS and bal[u["currency"]] + EPS < u["cost"]:
                    continue
                pb = payback_minutes(m, owned, boosts, 0, uid, eff)
                cand.append(((pb if pb is not None else float("inf")), u["cost"], m.order[uid], uid))
            if not cand:
                return
            uid = sorted(cand)[0][3]
            u = m.upgrades[uid]
            if bal[u["currency"]] + EPS * max(1, u["cost"]) < u["cost"]:
                return
            bal[u["currency"]] -= u["cost"]
            owned.add(uid)
            for sib in m.groups[(u["track"], int(u["tier"]))]:
                if sib != uid:
                    blocked.add(sib)
            out.append((t, uid))

    buy_all(0)
    for t in range(1, minutes + 1):
        cr = currency_rates(m, source_rates(m, owned, boosts, 0), eff)
        for c in m.currencies:
            bal[c] += cr[c]["gross"]
        buy_all(t)
    return out
