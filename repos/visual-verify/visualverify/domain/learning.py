"""From a user's accept or reject plus the measurements it was about, to the signals guide-core's improvement loop groups into proposals.

The rule (this repository's own, because the spec asks for thresholds tuned toward what the user accepts):

* ``accept`` of a result with a FAILED check: the user accepted something the threshold would have refused, so the threshold may be too strict.
  Signal: override of ``param:<threshold>`` in the RELAXING direction (a ``>=`` limit goes down, a ``<=`` limit goes up).
* ``reject`` or ``revise`` naming a dimension (silhouette, palette, value, edges, tiling, texture, diff): the user refused something. For each named
  dimension, the PASSED check that passed by the narrowest margin (relative to its parameter's range) is the best candidate for being too lenient.
  Signal: override of that threshold in the TIGHTENING direction. A reject that names no dimension gives no signal (it is not known what to tighten).
* Every threshold that was evaluated in the run is listed as ``considered``, so "overridden in 3 of 4 runs" has a true denominator.

A signal is only evidence. guide-core's ``propose.find_patterns`` needs the same signal in at least ``min_runs`` runs at ``min_rate``, makes ONE bounded step,
runs the gate, and a person approves. Nothing in this module changes a threshold.
"""

from __future__ import annotations

RELAX = {">=": "down", "<=": "up"}
TIGHTEN = {">=": "up", "<=": "down"}


def signals_for(decision: str, rows: list[dict], corrections: list[dict], meta: dict) -> tuple[list[dict], list[str]]:
    """``(signals, considered)`` for one decision on one recorded measurement. ``rows`` are the compact checks stored with the run; ``meta`` is style/thresholds.yaml."""
    usable = [r for r in rows if r.get("param") in meta and meta[r["param"]].get("kind") == "check" and not meta[r["param"]].get("locked") and r.get("passed") is not None]
    considered = sorted({f"param:{r['param']}" for r in usable})
    signals: list[dict] = []
    if decision == "accept":
        for r in usable:
            if r["passed"] is False and r["op"] in RELAX:
                signals.append({"kind": "override", "target": f"param:{r['param']}", "direction": RELAX[r["op"]]})
    elif decision in ("reject", "revise"):
        dims = sorted({c["dimension"] for c in corrections or []})
        for d in dims:
            cands = [r for r in usable if r["passed"] is True and r.get("dimension") == d and r["op"] in TIGHTEN]
            if not cands:
                continue
            best = min(cands, key=lambda r: (_margin(r, meta[r["param"]]), r["param"]))
            signals.append({"kind": "override", "target": f"param:{best['param']}", "direction": TIGHTEN[best["op"]]})
    return signals, considered


def _margin(row: dict, spec: dict) -> float:
    span = float(spec["max"]) - float(spec["min"])
    return abs(float(row["value"]) - float(row["limit"])) / span if span else 0.0


def regression_case(decision: str, rows: list[dict], corrections: list[dict]) -> dict | None:
    """An executable case for the improvement gate, kept only for decisions guide-core counts (accept, revise).

    accept: every check of that measurement must pass under the thresholds being tested.
    revise: at least one check in each corrected dimension must fail (the user said it needed changes there)."""
    if decision not in ("accept", "revise") or not rows:
        return None
    cmp_rows = [{k: r[k] for k in ("id", "param", "op", "limit", "value", "dimension") if k in r} for r in rows if r.get("param") and r.get("value") is not None]
    if not cmp_rows:
        return None
    if decision == "accept":
        return {"input": {"op": "recheck", "rows": cmp_rows}, "checks": [{"type": "all_pass"}]}
    dims = sorted({c["dimension"] for c in corrections or []})
    if not dims:
        return None
    return {"input": {"op": "recheck", "rows": cmp_rows}, "checks": [{"type": "fails_in_dimension", "dimension": d} for d in dims]}


def recheck(rows: list[dict], th) -> dict:
    """Re-evaluate stored measured values against the thresholds in force (``th(name)``): what the gate's solver returns for a regression case."""
    ops = {">=": lambda v, l: v >= l, "<=": lambda v, l: v <= l}
    res = []
    for r in rows:
        limit = th(r["param"])
        res.append({"id": r["id"], "dimension": r.get("dimension"), "passed": bool(ops[r["op"]](r["value"], limit)), "limit": limit})
    return {"results": res, "failed_ids": [x["id"] for x in res if not x["passed"]], "failed_dimensions": sorted({x["dimension"] for x in res if not x["passed"]}),
            "all_pass": all(x["passed"] for x in res)}
