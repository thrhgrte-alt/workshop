"""Failure reports: evidence, most likely cause, what a pass states.

Every failure carries the CHECK, the EVIDENCE (the measured value, the log lines, the screenshot paths that were supplied) and the MOST LIKELY CAUSE. The cause comes from
``rules/causes.yaml`` (first matching rule) and is labelled a heuristic reading of the evidence, with the rule id, never a verified diagnosis. A pass lists exactly what was
checked and what was not.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .checks import setting

SHOWN_LOG_LINES = 3


def load_causes(root: Path) -> list[dict]:
    data = yaml.safe_load((Path(root) / "rules" / "causes.yaml").read_text(encoding="utf-8")) or {}
    rules = data.get("rules", [])
    for r in rules:
        for k in ("id", "kind", "assertion", "cause", "next"):
            if k not in r:
                raise ValueError(f"rules/causes.yaml: rule {r.get('id')!r} is missing '{k}'")
    ids = [r["id"] for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("rules/causes.yaml: duplicate rule ids")
    if not rules or rules[-1]["id"] != "generic":
        raise ValueError("rules/causes.yaml: the last rule must be the 'generic' fallback")
    return rules


def match_cause(rules: list[dict], kind: str, f: dict) -> dict:
    text = "\n".join(list(f["evidence"].get("log_lines", [])) + [str(f["evidence"].get("detail", ""))])
    detail = f"{f['evidence'].get('detail', '')} {f['evidence'].get('measured', '')}"
    for r in rules:
        want = r["assertion"] if isinstance(r["assertion"], list) else [r["assertion"]]
        if r["kind"] not in ("*", kind) or not ("*" in want or f["assertion"] in want):
            continue
        if "log" in r and not re.search(r["log"], text, re.I):
            continue
        if "detail" in r and not re.search(r["detail"], detail, re.I):
            continue
        if "status" in r and r["status"] not in f.get("statuses", []):
            continue
        if "expected_zero" in r and bool(f.get("expected_zero")) != bool(r["expected_zero"]):
            continue
        if "ratio" in r:
            ratio = f.get("ratio")
            if ratio is None:
                continue
            lo, hi = r["ratio"].get("min", float("-inf")), r["ratio"].get("max", float("inf"))
            if not lo - 1e-12 <= ratio <= hi + 1e-12:
                continue
        return r
    return rules[-1]


def failures_of(check: dict, agg: dict, rules: list[dict], cap: int) -> tuple[list[dict], int]:
    """Failed assertions across the failing runs, deduplicated by (assertion, subject), ranked by how many runs they failed in."""
    seen: dict[tuple, dict] = {}
    for run in agg["runs"]:
        if run["verdict"] != "fail":
            continue
        for a in run["assertions"]:
            if a["ok"] is not False:
                continue
            key = (a["id"], a.get("subject"))
            f = seen.get(key)
            if f is None:
                ev = {"measured": a.get("measured"), "expected": a.get("expected"), "detail": a.get("detail", ""),
                      "log_lines": list(a.get("log_lines") or [])[:SHOWN_LOG_LINES], "screenshots": []}
                f = {"assertion": a["id"], "subject": a.get("subject"), "source": a["source"], "runs": [], "evidence": ev}
                for k in ("statuses", "ratio", "expected_zero"):
                    if k in a:
                        f[k] = a[k]
                seen[key] = f
            f["runs"].append(run["repeat"])
            for sp in run.get("screenshots", []):
                if sp not in f["evidence"]["screenshots"]:
                    f["evidence"]["screenshots"].append(sp)
    ranked = sorted(seen.values(), key=lambda f: -len(f["runs"]))
    out = []
    for f in ranked[:cap]:
        r = match_cause(rules, check["kind"], f)
        f["most_likely_cause"] = r["cause"]
        f["cause_rule"] = r["id"]
        f["next_step"] = r["next"]
        out.append(f)
    return out, max(0, len(ranked) - cap)


def _short(s: str, n: int = 110) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def summary_line(check: dict, agg: dict, failures: list[dict]) -> str:
    v, st, cid = agg["verdict"], agg["repeats"], check["id"]
    runs = f" ({st['failed']} of {st['conclusive']} runs failed)" if st["flaky_check"] and st["conclusive"] else ""
    if v == "pass":
        first = next((r for r in agg["runs"] if r["verdict"] == "pass"), None)
        bits = "; ".join(_short(c, 70) for c in (first["checked"][:2] if first else []))
        return f"PASS {cid}: {len(first['assertions']) if first else 0} assertion(s) held. {bits}"[:300]
    if v in ("fail", "flaky"):
        n = len(failures)
        f = failures[0]
        who = f"{f['assertion']}{'[' + f['subject'] + ']' if f.get('subject') else ''}"
        head = "FAIL" if v == "fail" else "FLAKY"
        return f"{head} {cid}{runs}: {n} assertion(s) failed; first {who}: {_short(f['evidence']['detail'], 90)}. Likely cause: {_short(f['most_likely_cause'], 80)}"[:300]
    if v == "refused":
        r = next((x for x in agg["runs"] if x["verdict"] == "refused"), None)
        return f"REFUSED {cid}: {_short((r or {}).get('refused') or 'the script declined to run', 200)}"
    note = agg["notes"][0] if agg["notes"] else ((agg["runs"][0].get("script_error") or "no usable result") if agg["runs"] else "no results were supplied")
    return f"INCONCLUSIVE {cid}: {_short(str(note), 220)}"


def build_report(check: dict, agg: dict, style: dict, rules: list[dict], *, detail: bool = False) -> dict:
    cap = int(setting(style, "console_max_findings"))
    failures, more = failures_of(check, agg, rules, cap if not detail else 10_000)
    base = next((r for r in agg["runs"] if r["verdict"] == (("pass") if agg["verdict"] == "pass" else "fail")), agg["runs"][0] if agg["runs"] else None)
    checked, not_eval = [], []
    for r in agg["runs"]:
        for c in r["checked"]:
            if c not in checked:
                checked.append(c)
        for c in r["not_evaluated"]:
            if c not in not_eval:
                not_eval.append(c)
    notes = list(agg["notes"])
    for r in agg["runs"]:
        notes += [n for n in r["notes"] if n not in notes]
        if r.get("script_error"):
            notes.append("script/answer problem in run %d: %s" % (r["repeat"], _short(str(r["script_error"]), 240)))
    rep: dict[str, Any] = {
        "summary": summary_line(check, agg, failures), "verdict": agg["verdict"],
        "check": {"id": check["id"], "kind": check["kind"], "title": check["title"]}, "repeats": agg["repeats"],
        "checked": checked[: (None if detail else 5)], "not_checked": list(check["not_checked"]) + not_eval,
    }
    if len(checked) > 5 and not detail:
        rep["more_checked"] = len(checked) - 5
    if agg["verdict"] in ("fail", "flaky"):
        rep["failures"] = failures
        rep["cause_note"] = "most_likely_cause is a heuristic reading of the evidence (rule id in cause_rule), not a verified diagnosis"
        if any(not f["evidence"]["screenshots"] for f in failures):
            rep["screenshots_note"] = "no screenshot path was supplied for some failures: the plan captures one with screen_capture on failure; pass its saved path as screenshots"
        if more:
            rep["more_failures"] = more
        if agg["verdict"] == "flaky":
            rep["flaky_hint"] = "intermittent: the failing evidence is real for the runs listed, but the check also passed; re-run, and look at timing assumptions before changing the game"
    if notes:
        rep["notes"] = notes[:8]
    rep["input_status"] = "schema_unverified"
    if detail:
        rep["runs"] = [{"repeat": r["repeat"], "verdict": r["verdict"], "assertions": r["assertions"]} for r in agg["runs"]]
    return rep
