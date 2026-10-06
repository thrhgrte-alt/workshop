"""Performance baselines and the comparison against them.

``playqa.baseline/1`` (written by ``save_baseline``, kept per place under the scoped workspace with a version history)::

    {"schema": "playqa.baseline/1", "name": "default", "taken_at": "...", "minutes": 5, "note": "...",
     "start": {"part_count": 1200, "script_count": 40, "memory_mb": 410.0, "frame_ms": 16.6},
     "end":   {... the same keys after N minutes ...}}

Comparison: for each measure and phase, ``pct = (current - baseline) / baseline * 100``; a measure fails when ``pct`` is above that measure's allowed increase (a range in
style/style.yaml, a PLACEHOLDER). A decrease never fails. A baseline of 0 with a non-zero current value has no percentage (reported as such, and counted as failing).
"""

from __future__ import annotations

from typing import Any

from .schema import BASELINE_SCHEMA

METRICS = ("part_count", "script_count", "memory_mb", "frame_ms")
LIMIT_OF = {"part_count": "perf_part_count_increase_pct", "script_count": "perf_script_count_increase_pct", "memory_mb": "perf_memory_increase_pct", "frame_ms": "perf_frame_ms_increase_pct"}


def snapshot(measures: dict) -> dict:
    return {m: measures.get(m) for m in METRICS}


def make_baseline(name: str, start: dict, end: dict, minutes: float, taken_at: str, note: str = "") -> dict:
    return {"schema": BASELINE_SCHEMA, "name": name, "taken_at": taken_at, "minutes": minutes, "note": note, "start": snapshot(start), "end": snapshot(end)}


def validate_baseline(doc: Any) -> list[str]:
    p = []
    if not isinstance(doc, dict) or doc.get("schema") != BASELINE_SCHEMA:
        return [f"not a {BASELINE_SCHEMA} document"]
    for ph in ("start", "end"):
        if not isinstance(doc.get(ph), dict):
            p.append(f"missing '{ph}' snapshot")
            continue
        for m in METRICS:
            v = doc[ph].get(m)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                p.append(f"{ph}.{m} must be a non-negative number or null")
    return p


def pct_change(current: float, base: float) -> float | None:
    if base == 0:
        return 0.0 if current == 0 else None
    return (current - base) / base * 100.0


def compare(base: dict, start: dict, end: dict, ranges: dict) -> list[dict]:
    """One row per measure and phase: ``{metric, phase, baseline, current, pct, limit_pct, ok, note}``; ``ok`` is None when it could not be compared."""
    rows = []
    for phase, cur in (("start", start), ("end", end)):
        for m in METRICS:
            b, c = base[phase].get(m), cur.get(m)
            limit = ranges[LIMIT_OF[m]]["max"]
            row = {"metric": m, "phase": phase, "baseline": b, "current": c, "pct": None, "limit_pct": limit, "ok": None, "note": ""}
            if b is None or c is None:
                row["note"] = "not measured in the " + ("baseline" if b is None else "current run")
            else:
                pct = pct_change(c, b)
                if pct is None:
                    row.update(ok=False, note="baseline is 0 and the current value is not")
                else:
                    row.update(pct=round(pct, 2), ok=pct <= limit + 1e-9)
            rows.append(row)
    return rows


def growth(start: dict, end: dict, ranges: dict) -> dict:
    """Memory growth between the start and end snapshot of ONE run, against ``perf_memory_growth_pct_over_run``."""
    a, b = start.get("memory_mb"), end.get("memory_mb")
    limit = ranges["perf_memory_growth_pct_over_run"]["max"]
    if a is None or b is None:
        return {"metric": "memory_mb", "start": a, "end": b, "pct": None, "limit_pct": limit, "ok": None, "note": "memory not measured"}
    pct = pct_change(b, a)
    if pct is None:
        return {"metric": "memory_mb", "start": a, "end": b, "pct": None, "limit_pct": limit, "ok": False, "note": "start memory is 0"}
    return {"metric": "memory_mb", "start": a, "end": b, "pct": round(pct, 2), "limit_pct": limit, "ok": pct <= limit + 1e-9, "note": ""}
