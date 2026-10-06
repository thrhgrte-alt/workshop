"""Checks: a measured number, the limit it was compared with, the operator, and whether it passed. The shape every tool reports.

Wording rules (a test enforces them): a result never says an image 'looks good' or 'is fine'; it says what was measured, against which limit, and passed or
failed. What the numbers cannot say (taste, anatomy, perspective ...) is always listed under ``not_measured``.
"""

from __future__ import annotations

from typing import Any

BASE_NOT_MEASURED = ["taste", "anatomy", "perspective"]
OPS = {">=": lambda v, l: v >= l, "<=": lambda v, l: v <= l, ">": lambda v, l: v > l, "<": lambda v, l: v < l, "==": lambda v, l: v == l,
       "in": lambda v, l: l[0] <= v <= l[1]}  # "in": the limit is a [min, max] pair (inclusive)


def _r(v: Any, nd: int = 4) -> Any:
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, (list, tuple)):
        return [_r(x, nd) for x in v]
    return v


def check(cid: str, value: float | int | None, op: str, limit: float | int | list, *, source: str = "", param: str | None = None, dimension: str = "", note: str = "", skipped: str = "") -> dict:
    """One check. ``value=None`` (or ``skipped``) records that it could not be measured, which is neither a pass nor a fail."""
    row: dict[str, Any] = {"id": cid, "op": op, "limit": _r(limit), "dimension": dimension or cid.split(".")[0]}
    if param:
        row["param"] = param
    if value is None or skipped:
        row.update(value=None, passed=None, skipped=skipped or "not measurable for this image")
        return row
    ok = bool(OPS[op](value, limit))
    near = limit if op != "in" else (limit[0] if value < limit[0] else limit[1])
    row.update(value=_r(value), passed=ok, _raw=float(value), _raw_limit=float(near))
    if source:
        row["source"] = source
    if note and not ok:
        row["note"] = note
    return row


def rank(rows: list[dict]) -> list[dict]:
    """Failed first (largest relative miss first), then skipped, then passed; stable by id."""
    def key(r: dict):
        if r["passed"] is False:
            miss = abs(r["_raw"] - r["_raw_limit"]) / (abs(r["_raw_limit"]) + 1e-9)
            return (0, -miss, r["id"])
        return (1 if r["passed"] is None else 2, 0.0, r["id"])

    return sorted(rows, key=key)


def public(row: dict) -> dict:
    """The check as reported to a client (private raw fields removed)."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def counts(rows: list[dict]) -> dict[str, int]:
    return {"passed": sum(r["passed"] is True for r in rows), "failed": sum(r["passed"] is False for r in rows), "skipped": sum(r["passed"] is None for r in rows)}


def fmt(r: dict) -> str:
    return f"{r['id']} {r['value']} {_negate(r['op'])} {r['limit']}" if r["passed"] is False else f"{r['id']} {r['value']} {r['op']} {r['limit']}"


def _negate(op: str) -> str:
    return {">=": "<", "<=": ">", ">": "<=", "<": ">=", "==": "!=", "in": "outside"}[op]


def verdict(rows: list[dict], subject: str, label: str, *, limit: int = 10, extra: str = "") -> str:
    """The one-line summary. Always states the counts, names the worst failures, and says how to read it."""
    c = counts(rows)
    total = len(rows)
    head = f"{subject}: {c['passed']} of {total} checks passed"
    if c["skipped"]:
        head += f", {c['skipped']} not measurable"
    if c["failed"]:
        worst = [fmt(r) for r in rank(rows) if r["passed"] is False][: min(3, limit)]
        head += f"; FAILED {c['failed']}: " + "; ".join(worst) + (" ..." if c["failed"] > len(worst) else "")
    else:
        head += "; none failed"
    return f"{head}. Thresholds are placeholder defaults unless stated. Measured numbers only: taste, anatomy and perspective are not measured. [{label}]" + (f" {extra}" if extra else "")


def findings(rows: list[dict], cap: int) -> tuple[list[dict], int]:
    """Failed checks as findings (ranked, capped). Returns (findings, how many more were left out)."""
    failed = [r for r in rank(rows) if r["passed"] is False]
    out = [{"check": r["id"], "value": r["value"], "op": r["op"], "limit": r["limit"], **({"note": r["note"]} if r.get("note") else {})} for r in failed[:cap]]
    return out, max(0, len(failed) - cap)
