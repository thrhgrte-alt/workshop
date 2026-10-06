"""Rebalance proposals: the SMALLEST set of value changes that makes one finding go away, never touching locked values.

Method (deterministic, no randomness):

1. Pick the finding (by code and optional upgrade/archetype/currency/track, or the first fixable error/warning).
2. List candidate values that push the finding the right way (for a slow step: its cost down, the income of the previous
   purchase up, the source rate up ...). Locked values are removed from the list and reported as skipped.
3. For each candidate, walk a fixed grid of relative changes from small to large; at each step re-simulate the whole economy and accept the
   first step where (a) the finding is gone and (b) no NEW warning/error appeared anywhere. That is the smallest change for that value.
4. Keep the candidate with the smallest relative change. If no single value works and ``max_changes`` allows, try pairs.
5. Report before and after timing for every upgrade. ``dry_run=False`` only writes the proposed spec to a NEW file; the input is never modified.
"""

from __future__ import annotations

import itertools
from typing import Any

from . import analysis as A
from . import sim as M
from . import spec as S

TIMING = {"wall", "over_band", "under_band"}
SCOPE = {"wall": TIMING, "over_band": TIMING, "under_band": TIMING}
MATCH_KEYS = {"wall": ("archetype", "upgrade"), "over_band": ("archetype", "upgrade"), "under_band": ("archetype", "upgrade"),
              "gap_cliff": ("archetype", "upgrade"), "slow_for_archetype": ("archetype", "upgrade"), "cost_cliff": ("upgrade",),
              "dead_option": ("upgrade",), "dominant_option": ("track", "tier"), "dominated_option": ("upgrade",),
              "runaway_inflation": ("archetype",), "content_exhausted": ("archetype",), "pay_to_win": ("archetype",), "free_cliff": ("archetype", "upgrade")}
FIXABLE = tuple(MATCH_KEYS)
FAMILY = {"wall", "over_band", "under_band", "gap_cliff", "cost_cliff", "slow_for_archetype"}
DOWN_GRID = (0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.925, 0.94, 0.95, 0.97)
UP_GRID = (0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0)
PAIR_GRID = (0.10, 0.25, 0.50)


def same_target(f: dict, target: dict) -> bool:
    code = target["code"]
    return f["code"] in SCOPE.get(code, {code}) and all(f.get(k) == target.get(k) for k in MATCH_KEYS[code])


def still_present(target: dict, strict: bool):
    """Predicate: is this finding (or, when ``strict``, any pacing/cost finding on the same upgrade) still reported?"""
    if strict and target["code"] in FAMILY:
        return lambda f: f["code"] in FAMILY and f.get("upgrade") == target.get("upgrade") and f.get("archetype") in (None, target.get("archetype"))
    return lambda f: same_target(f, target)


def identity(f: dict) -> tuple:
    return tuple(f.get(k) for k in ("code", "archetype", "upgrade", "currency", "track", "strategy"))


def select_finding(findings: list[dict], code: str | None, **want: Any) -> dict:
    pool = [f for f in findings if f["code"] in FIXABLE and f["severity"] in ("error", "warning")] if code is None else [f for f in findings if f["code"] == code]
    for k, v in want.items():
        if v is not None:
            pool = [f for f in pool if f.get(k) == v]
    if not pool:
        known = sorted({f["code"] for f in findings})
        if code is not None and code not in FIXABLE:
            raise ValueError(f"no automatic rebalance exists for '{code}'. Fixable codes: {list(FIXABLE)}")
        raise ValueError(f"no matching finding to fix (code={code!r}, {({k: v for k, v in want.items() if v is not None})}). Findings present: {known or 'none'}. "
                         f"Run check_economy first.")
    return pool[0]


def _round_value(old: Any, new: float) -> Any:
    if isinstance(old, int) and not isinstance(old, bool):
        return max(1, int(round(new))) if old >= 1 else int(round(new))
    return float(f"{new:.6g}")


def _prev_purchase(sm: dict, upgrade: str) -> dict | None:
    prev = None
    for p in sm["purchases"]:
        if p["id"] == upgrade:
            return prev
        prev = p
    return prev


def _effect_paths(spec: dict, entity: dict, section: str) -> list[str]:
    return [f"{section}.{entity['id']}.effects.{i}.{'add' if 'add' in e else 'mult'}" for i, e in enumerate(entity.get("effects", []))]


def candidates(ctx: A.Ctx, target: dict) -> list[tuple[str, int, str]]:
    """(value path, direction, why) in priority order. direction: -1 lower the value, +1 raise it."""
    spec, code = ctx.spec, target["code"]
    ups = S.by_id(spec["upgrades"])
    out: list[tuple[str, int, str]] = []
    base = ctx.sims()

    def prev_income(arch: str, uid: str) -> None:
        sm = base[arch]
        p = _prev_purchase(sm, uid)
        if p and p["kind"] == "upgrade":
            for path in _effect_paths(spec, ups[p["id"]], "upgrades"):
                out.append((path, +1, f"income gained by the previous purchase '{p['id']}'"))
        elif p and p["kind"] == "rebirth":
            rb = spec["rebirths"][0]
            for k in ("mult_per_rebirth", "mult_compound"):
                if k in rb["bonus"]:
                    out.append((f"rebirths.{rb['id']}.bonus.{k}", +1, "rebirth bonus"))
        cur = ups[uid]["currency"] if uid in ups else spec["rebirths"][0]["currency"]
        for s in spec["sources"]:
            if s["currency"] == cur:
                out.append((f"sources.{s['id']}.per_minute", +1, f"base rate of source '{s['id']}'"))

    if code in ("wall", "over_band", "gap_cliff", "slow_for_archetype", "free_cliff"):
        uid, arch = target["upgrade"], target["archetype"]
        if uid in ups:
            out.append((f"upgrades.{uid}.cost", -1, f"cost of '{uid}'"))
        else:
            out.append((f"rebirths.{uid}.cost", -1, f"cost of rebirth '{uid}'"))
        prev_income(arch, uid)
    elif code == "under_band":
        uid, arch = target["upgrade"], target["archetype"]
        out.append((f"upgrades.{uid}.cost", +1, f"cost of '{uid}'"))
        p = _prev_purchase(base[arch], uid)
        if p and p["kind"] == "upgrade":
            for path in _effect_paths(spec, ups[p["id"]], "upgrades"):
                out.append((path, -1, f"income gained by the previous purchase '{p['id']}'"))
    elif code == "cost_cliff":
        uid = target["upgrade"]
        out.append((f"upgrades.{uid}.cost", -1, f"cost of '{uid}'"))
        track, tier = ups[uid]["track"], int(ups[uid]["tier"])
        for o in spec["upgrades"]:
            if o["track"] == track and int(o["tier"]) == tier - 1:
                out.append((f"upgrades.{o['id']}.cost", +1, f"cost of the previous tier '{o['id']}'"))
    elif code == "dead_option":
        uid = target["upgrade"]
        out.append((f"upgrades.{uid}.cost", -1, f"cost of '{uid}'"))
        for path in _effect_paths(spec, ups[uid], "upgrades"):
            out.append((path, +1, f"income gained by '{uid}'"))
    elif code == "dominated_option":
        uid = target["upgrade"]
        for path in _effect_paths(spec, ups[uid], "upgrades"):
            out.append((path, +1, f"income gained by '{uid}'"))
        out.append((f"upgrades.{uid}.cost", -1, f"cost of '{uid}'"))
    elif code == "dominant_option":
        win = target["upgrade"]
        for path in _effect_paths(spec, ups[win], "upgrades"):
            out.append((path, -1, f"income gained by dominant '{win}'"))
        out.append((f"upgrades.{win}.cost", +1, f"cost of dominant '{win}'"))
        for o in target.get("versus", []):
            for path in _effect_paths(spec, ups[o], "upgrades"):
                out.append((path, +1, f"income gained by alternative '{o}'"))
            out.append((f"upgrades.{o}.cost", -1, f"cost of alternative '{o}'"))
    elif code in ("runaway_inflation", "content_exhausted"):
        for s in spec["sources"]:
            out.append((f"sources.{s['id']}.per_minute", -1, f"base rate of source '{s['id']}'"))
        for track in {u["track"] for u in spec["upgrades"]}:
            top = max(int(u["tier"]) for u in spec["upgrades"] if u["track"] == track)
            for u in spec["upgrades"]:
                if u["track"] == track and int(u["tier"]) == top:
                    out.append((f"upgrades.{u['id']}.cost", +1, f"cost of the last tier '{u['id']}'"))
        for sk in spec["sinks"]:
            out.append((f"sinks.{sk['id']}.value", +1, f"sink '{sk['id']}'"))
        for rb in spec["rebirths"]:
            out.append((f"rebirths.{rb['id']}.cost", +1, f"cost of rebirth '{rb['id']}'"))
    elif code == "pay_to_win":
        for b in spec["boosts"]:
            for i, e in enumerate(b["effects"]):
                if "mult" in e:
                    out.append((f"boosts.{b['id']}.effects.{i}.mult", -1, f"multiplier of boost '{b['id']}'"))
    seen, uniq = set(), []
    for c in out:
        if c[0] not in seen:
            seen.add(c[0])
            uniq.append(c)
    return uniq


def _grid(direction: int) -> tuple[float, ...]:
    return DOWN_GRID if direction < 0 else UP_GRID


def _trial(ctx: A.Ctx, pred, base_ids: set[tuple], changes: list[tuple[str, Any]], seed: int | None, days: int | None) -> dict | None:
    spec2 = ctx.spec
    for path, val in changes:
        spec2 = S.set_value(spec2, path, val)
    if S.errors_of(S.validate(spec2)):
        return None
    ctx2 = A.Ctx(S.normalize(spec2), ctx.style, ctx.rules)
    res = A.check(ctx2, seed=seed, days=days)
    if any(pred(f) for f in res["findings"]):
        return None
    new = [f for f in res["findings"] if f["severity"] in ("error", "warning") and identity(f) not in base_ids]
    if new:
        return None
    return {"result": res, "spec": spec2, "ctx": ctx2}


def propose(ctx: A.Ctx, base_check: dict, target: dict, *, max_changes: int = 2, seed: int | None = None, days: int | None = None) -> dict:
    """Search for the smallest fix. ``base_check`` is ``analysis.check(ctx)``; ``target`` one of its findings."""
    if target["code"] not in FIXABLE:
        raise ValueError(f"no automatic rebalance exists for '{target['code']}'. Fixable codes: {list(FIXABLE)}")
    locked = set(S.locked_paths(ctx.spec))
    values = S.value_paths(ctx.spec)
    base_ids = {identity(f) for f in base_check["findings"] if f["severity"] in ("error", "warning")}
    cands, skipped = [], []
    for path, direction, why in candidates(ctx, target):
        if path in locked:
            skipped.append({"path": path, "reason": "locked", "why_considered": why})
        elif path not in values:
            skipped.append({"path": path, "reason": "not a numeric value in this spec", "why_considered": why})
        elif values[path] == 0:
            skipped.append({"path": path, "reason": "value is 0, a relative change cannot move it", "why_considered": why})
        else:
            cands.append((path, direction, why))
    tried = 0
    chosen = None
    strict_used = False
    for strict in ((True, False) if target["code"] in FAMILY else (False,)):
        pred = still_present(target, strict)
        best: list[tuple[tuple, list[tuple[str, Any]], dict, list[float]]] = []
        for path, direction, why in cands:
            old = values[path]
            for frac in _grid(direction):
                new = _round_value(old, old * (1 + direction * frac))
                if new == old or (isinstance(new, (int, float)) and new <= 0):
                    continue
                tried += 1
                t = _trial(ctx, pred, base_ids, [(path, new)], seed, days)
                if t:
                    best.append(((1, frac, len(best)), [(path, new)], t, [frac]))
                    break
        chosen = min(best, key=lambda b: b[0]) if best else None
        if chosen is None and max_changes >= 2 and len(cands) >= 2:
            for (p1, d1, _), (p2, d2, _) in itertools.combinations(cands, 2):
                for f1, f2 in itertools.product(PAIR_GRID, PAIR_GRID):
                    n1 = _round_value(values[p1], values[p1] * (1 + d1 * f1))
                    n2 = _round_value(values[p2], values[p2] * (1 + d2 * f2))
                    if n1 == values[p1] or n2 == values[p2] or n1 <= 0 or n2 <= 0:
                        continue
                    tried += 1
                    t = _trial(ctx, pred, base_ids, [(p1, n1), (p2, n2)], seed, days)
                    if t:
                        best.append(((2, f1 + f2, len(best)), [(p1, n1), (p2, n2)], t, [f1, f2]))
            chosen = min(best, key=lambda b: b[0]) if best else None
        if chosen is not None:
            strict_used = strict
            break
    if chosen is None:
        return {"found": False, "target": target, "candidates_considered": [c[0] for c in cands], "skipped": skipped, "trials": tried,
                "reason": ("every candidate value is locked" if not cands and skipped else "no change on the search grid fixes the finding without creating a new warning or error"),
                "hint": "Unlock a value, change a different one by hand, or revisit the band (suggest_band_adjustments)."}
    _, changes, trial, _ = chosen
    after_res = trial["result"]
    rows = []
    sa = base_check["parts"]["timing"]["sims"]
    sb = after_res["parts"]["timing"]["sims"]
    for name in sa:
        pa = {p["id"]: p for p in sa[name]["purchases"] if p["lap"] == 0}
        pb = {p["id"]: p for p in sb[name]["purchases"] if p["lap"] == 0}
        for uid in sorted(set(pa) | set(pb), key=lambda u: (pa.get(u) or pb.get(u))["minute"]):
            a, b = pa.get(uid), pb.get(uid)
            rows.append({"archetype": name, "upgrade": uid, "tier": (a or b)["tier"], "gap_before": a and a["gap_minutes"], "gap_after": b and b["gap_minutes"],
                         "minute_before": a and a["minute"], "minute_after": b and b["minute"]})
    change_rows = [{"path": p, "before": values[p], "after": v, "relative_change": (v - values[p]) / values[p]} for p, v in changes]
    assert not ({c["path"] for c in change_rows} & locked), "internal error: a locked value was selected"
    before_ids = {identity(f) for f in base_check["findings"]}
    return {"found": True, "target": target, "changes": change_rows, "n_changes": len(change_rows), "before_after": rows,
            "resolved": [f for f in base_check["findings"] if same_target(f, target)],
            "findings_after": [f for f in after_res["findings"] if identity(f) not in before_ids],
            "related_findings_also_cleared": strict_used, "remaining_on_upgrade": [f for f in after_res["findings"] if f.get("upgrade") == target.get("upgrade") and f["code"] in FAMILY],
            "skipped": skipped, "trials": tried, "verdict_before": base_check["verdict"], "verdict_after": after_res["verdict"],
            "proposed_spec": trial["spec"]}


def apply_to_raw(raw: dict, changes: list[dict]) -> dict:
    """Apply the changes to the user's own (un-normalised) spec so the written file keeps their format."""
    out = raw
    for c in changes:
        try:
            out = S.set_value(out, c["path"], c["after"])
        except ValueError:
            raise ValueError(f"cannot write '{c['path']}' into the original spec file (the value is a default that the file does not state); add it to the file first")
    return out
