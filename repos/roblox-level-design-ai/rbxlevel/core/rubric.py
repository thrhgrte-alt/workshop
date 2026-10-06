"""Rubrics: weighted criteria mixing automated measurements and human/model judgment.

Subjective criteria are *judgments*, not ground truth. ``score_rubric`` keeps
automated and manual scores separate and reports which criteria are still
unscored, so a partial score is never presented as a final verdict.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def load_rubric(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    problems = validate_rubric(data)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return data


def validate_rubric(rubric: dict) -> list[str]:
    problems = []
    criteria = rubric.get("criteria")
    if not criteria:
        return ["rubric has no criteria"]
    ids = [c.get("id") for c in criteria]
    if len(ids) != len(set(ids)):
        problems.append("duplicate criterion ids")
    for c in criteria:
        if c.get("method") not in ("auto", "manual", "hybrid"):
            problems.append(f"criterion '{c.get('id')}' needs method auto|manual|hybrid")
        if not c.get("weight", 0) > 0:
            problems.append(f"criterion '{c.get('id')}' needs a positive weight")
    return problems


def _as_score(v) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return max(0.0, min(1.0, float(v)))


def score_rubric(rubric: dict, auto: dict | None = None, manual: dict | None = None) -> dict:
    auto, manual = auto or {}, manual or {}
    rows, total_w, got = [], 0.0, 0.0
    unscored = []
    for c in rubric["criteria"]:
        cid, w = c["id"], float(c["weight"])
        a = auto.get(cid)
        m = manual.get(cid)
        method = c["method"]
        if method == "auto":
            value = None if a is None else _as_score(a)
        elif method == "manual":
            value = None if m is None else _as_score(m)
        else:  # hybrid: need both, take the lower (a human can veto a metric, and vice versa)
            value = None if (a is None or m is None) else min(_as_score(a), _as_score(m))
        if value is None:
            unscored.append(cid)
        else:
            total_w += w
            got += w * value
        rows.append({"id": cid, "method": method, "weight": w, "auto": a, "manual": m, "score": value,
                     "passed": None if value is None else value >= c.get("pass_at", 0.6),
                     "subjective": method != "auto"})
    score = got / total_w if total_w else None
    must_fail = [r["id"] for r, c in zip(rows, rubric["criteria"]) if c.get("required") and r["passed"] is False]
    return {
        "score": None if score is None else round(score, 3),
        "complete": not unscored,
        "unscored": unscored,
        "required_failures": must_fail,
        "passed": bool(score is not None and not unscored and not must_fail and score >= rubric.get("pass_at", 0.7)),
        "criteria": rows,
        "note": "Subjective criteria are judgments, not objective ground truth.",
    }
