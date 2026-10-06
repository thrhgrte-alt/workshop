"""Learning from feedback, deterministically. Nothing here changes a model or edits a file; it reads saved runs and decisions and returns SUGGESTIONS.

What is read (all saved with the shared ``record_run`` / ``record_decision`` tools):

* Playtest notes: a decision whose correction has ``dimension: pacing``. The step is ``tier`` (key, or "tier 4" in the note) and the feeling is
  ``felt`` = ``too_slow`` | ``too_fast`` (key), else ``direction`` (``less`` = waits should be shorter = too slow, ``more`` = too fast), else keywords in the note.
  Optional ``archetype`` (default: the first reference archetype).
* Archetype observations: ``dimension: archetype_assumption`` with ``archetype``, ``field`` (session_minutes, sessions_per_day, efficiency, variance) and numeric ``observed``.
* Accepted and rejected rebalances: a run whose ``constraints.rebalance.changes`` lists ``{path, before, after}`` followed by an accept/reject decision.

Rules (thresholds ``learning.min_votes``, ``shrink``, ``grow`` live in style/style.yaml):

* A tier needs at least ``min_votes`` net reports of one feeling. "Too slow" inside the band lowers the band maximum to ``(1 - shrink) x`` the simulated step time;
  "too fast" raises the band minimum to ``(1 + grow) x`` it. If the simulated step is already outside the band, the economy (not the band) is the thing to change.
* An archetype field is suggested to move to the MEDIAN of at least ``min_votes`` observations.
* A value path rejected at least ``min_votes`` times and never accepted is a candidate for the spec's ``locked`` list.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from . import analysis as A

SLOW_WORDS = ("slow", "grind", "drag", "too long", "tedious", "wall", "stuck")
FAST_WORDS = ("fast", "trivial", "instant", "too easy", "rushed", "no wait")
FIELDS = ("session_minutes", "sessions_per_day", "efficiency", "variance")


def _feeling(c: dict) -> str | None:
    felt = c.get("felt")
    if felt in ("too_slow", "too_fast"):
        return felt
    d = c.get("direction")
    if d == "less":
        return "too_slow"
    if d == "more":
        return "too_fast"
    note = (c.get("note") or "").lower()
    slow, fast = any(w in note for w in SLOW_WORDS), any(w in note for w in FAST_WORDS)
    if slow != fast:
        return "too_slow" if slow else "too_fast"
    return None


def _tier(c: dict) -> int | None:
    if isinstance(c.get("tier"), int) and not isinstance(c.get("tier"), bool):
        return c["tier"]
    m = re.search(r"tier\s*(\d+)", (c.get("note") or "").lower())
    return int(m.group(1)) if m else None


def gather(runs: list[dict], decisions: list[dict]) -> dict:
    by_run = {r["run_id"]: r for r in runs}
    pacing, arch, rebal = [], [], []
    for d in decisions:
        for c in d.get("corrections") or []:
            if c.get("dimension") == "pacing":
                pacing.append({"run_id": d["run_id"], "tier": _tier(c), "felt": _feeling(c), "archetype": c.get("archetype"), "note": c.get("note")})
            elif c.get("dimension") == "archetype_assumption" and c.get("archetype") and c.get("field") in FIELDS and isinstance(c.get("observed"), (int, float)):
                arch.append({"run_id": d["run_id"], "archetype": c["archetype"], "field": c["field"], "observed": float(c["observed"])})
        run = by_run.get(d["run_id"], {})
        rb = (run.get("constraints") or {}).get("rebalance")
        if rb and d.get("decision") in ("accept", "reject"):
            for ch in rb.get("changes", []):
                if ch.get("path"):
                    rebal.append({"run_id": d["run_id"], "path": ch["path"], "decision": d["decision"], "reason": d.get("reason", "")})
    return {"pacing": pacing, "archetype": arch, "rebalance": rebal}


def suggest(ctx: A.Ctx, runs: list[dict], decisions: list[dict]) -> dict:
    learn = ctx.style.get("learning", {})
    min_votes = int(learn.get("min_votes", 2))
    shrink, grow = float(learn.get("shrink", 0.2)), float(learn.get("grow", 0.25))
    data = gather(runs, decisions)
    out: list[dict] = []
    notes: list[str] = []
    ref = ctx.reference[0]
    sims = ctx.sims()
    # --- pacing -> bands
    votes: dict[tuple[str, int], dict[str, int]] = {}
    unparsed = 0
    for p in data["pacing"]:
        if p["tier"] is None or p["felt"] is None:
            unparsed += 1
            continue
        a = p["archetype"] if p["archetype"] in ctx.spec["archetypes"] else ref
        v = votes.setdefault((a, p["tier"]), {"too_slow": 0, "too_fast": 0})
        v[p["felt"]] += 1
    if unparsed:
        notes.append(f"{unparsed} pacing note(s) had no readable tier or feeling and were ignored; save corrections with tier and felt (too_slow|too_fast).")
    band_edits: dict[str, list[dict]] = {}
    for (arch, tier), v in sorted(votes.items()):
        net = v["too_slow"] - v["too_fast"]
        band = ctx.band(tier)
        gaps = [p["gap_minutes"] for p in sims[arch]["purchases"] if p["kind"] == "upgrade" and p["tier"] == tier and p["lap"] == 0]
        base = {"tier": tier, "archetype": arch, "reports": dict(v)}
        if abs(net) < min_votes:
            out.append({**base, "type": "need_more_reports", "message": f"tier {tier}: net {net:+d} report(s); {min_votes} needed before changing anything."})
            continue
        if not gaps:
            out.append({**base, "type": "not_reached", "message": f"tier {tier} is not reached by {arch} in the simulated horizon, so there is no simulated step time to calibrate against."})
            continue
        gap = max(gaps)
        if band is None:
            out.append({**base, "type": "no_band", "simulated_gap_minutes": gap, "message": f"no band covers tier {tier}; add one in style/style.yaml around {gap:g} active minutes."})
            continue
        if net > 0:
            if gap > band["max_minutes"]:
                out.append({**base, "type": "fix_economy", "simulated_gap_minutes": gap, "band": band["id"],
                            "message": f"tier {tier} felt too slow and the simulation agrees ({gap:g} min > band maximum {band['max_minutes']:g}); change the economy (propose_rebalance), not the band."})
            else:
                new_max = max(round(gap * (1 - shrink), 1), band["min_minutes"])
                edit = {**base, "type": "lower_band_max", "band": band["id"], "field": "max_minutes", "current": band["max_minutes"], "suggested": new_max, "simulated_gap_minutes": gap,
                        "message": f"tier {tier} felt too slow at a simulated {gap:g} min that band '{band['id']}' allows; suggest max_minutes {band['max_minutes']:g} -> {new_max:g}."}
                out.append(edit)
                band_edits.setdefault(band["id"], []).append(edit)
        else:
            if gap < band["min_minutes"]:
                out.append({**base, "type": "fix_economy", "simulated_gap_minutes": gap, "band": band["id"],
                            "message": f"tier {tier} felt too fast and the simulation agrees ({gap:g} min < band minimum {band['min_minutes']:g}); change the economy (propose_rebalance), not the band."})
            else:
                new_min = min(round(gap * (1 + grow), 1), band["max_minutes"])
                edit = {**base, "type": "raise_band_min", "band": band["id"], "field": "min_minutes", "current": band["min_minutes"], "suggested": new_min, "simulated_gap_minutes": gap,
                        "message": f"tier {tier} felt too fast at a simulated {gap:g} min that band '{band['id']}' allows; suggest min_minutes {band['min_minutes']:g} -> {new_min:g}."}
                out.append(edit)
                band_edits.setdefault(band["id"], []).append(edit)
    for bid, edits in band_edits.items():
        kinds = {e["type"] for e in edits}
        if len(kinds) > 1:
            notes.append(f"band '{bid}' received both 'too slow' and 'too fast' reports for different tiers; split the band instead of moving it.")
    # --- archetype observations
    obs: dict[tuple[str, str], list[float]] = {}
    for a in data["archetype"]:
        obs.setdefault((a["archetype"], a["field"]), []).append(a["observed"])
    for (name, field), vals in sorted(obs.items()):
        if name not in ctx.spec["archetypes"]:
            notes.append(f"archetype observation for unknown archetype '{name}' ignored.")
            continue
        cur = ctx.spec["archetypes"][name].get(field)
        if len(vals) < min_votes:
            out.append({"type": "need_more_reports", "archetype": name, "field": field, "reports": len(vals),
                        "message": f"{name}.{field}: {len(vals)} observation(s); {min_votes} needed."})
            continue
        med = statistics.median(vals)
        med = int(round(med)) if field in ("session_minutes", "sessions_per_day") else round(med, 3)
        if med != cur:
            out.append({"type": "change_archetype", "archetype": name, "field": field, "path": f"archetypes.{name}.{field}", "current": cur, "suggested": med, "observations": sorted(vals),
                        "message": f"{name}.{field}: median of {len(vals)} observation(s) is {med}; the spec assumes {cur}."})
    # --- accepted / rejected rebalances
    tally: dict[str, dict[str, Any]] = {}
    for r in data["rebalance"]:
        t = tally.setdefault(r["path"], {"accept": 0, "reject": 0, "reasons": []})
        t[r["decision"]] += 1
        if r["decision"] == "reject" and r["reason"]:
            t["reasons"].append(r["reason"])
    locked = set(ctx.spec["locked"])
    for path, t in sorted(tally.items()):
        if t["reject"] >= min_votes and t["accept"] == 0 and path not in locked:
            out.append({"type": "consider_locking", "path": path, "rejected": t["reject"], "accepted": t["accept"], "reasons": t["reasons"][-3:],
                        "message": f"{path} was rejected {t['reject']} time(s) and never accepted; consider adding it to 'locked' so proposals stop touching it."})
    return {"suggestions": out, "notes": notes, "evidence": {"pacing_reports": len(data["pacing"]), "archetype_observations": len(data["archetype"]),
                                                              "rebalance_decisions": len(data["rebalance"])},
            "rules": {"min_votes": min_votes, "shrink": shrink, "grow": grow},
            "applied": False, "next": "These are suggestions only. Edit style/style.yaml (bands) or the spec (archetypes, locked) yourself, then add an eval task that locks the lesson in."}
