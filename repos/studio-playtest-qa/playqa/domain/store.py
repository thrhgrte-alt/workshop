"""Local stores in the per-place workspace: stored check results (append-only JSONL) and baselines (copy-on-write versions).

Everything lives under ``workspace/projects/<project>/<place>/`` (git-ignored, never uploaded). A stored result keeps the verdict, the repeat statistics, the failed assertion ids
and the summary, not the raw hub answers.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path

from ..guide_adapter import Project, dryrun, scope as S
from .baseline import validate_baseline

RESULT_SCHEMA = 1


def place_project(project: Project, scope: S.Scope) -> Project:
    return S.project_for_scope(project, scope)


def results_file(project: Project, scope: S.Scope) -> Path:
    return place_project(project, scope).workspace / "results" / "results.jsonl"


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def new_result_id() -> str:
    return f"res-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def result_row(scope: S.Scope, report: dict, result_id: str) -> dict:
    fails = report.get("failures") or []
    return {"schema": RESULT_SCHEMA, "result_id": result_id, "at": now(), "scope": scope.to_dict(), "check_id": report["check"]["id"], "verdict": report["verdict"],
            "repeats": report["repeats"], "failed_assertions": sorted({f"{f['assertion']}" + (f"[{f['subject']}]" if f.get("subject") else "") for f in fails}), "summary": report["summary"]}


def append_result(project: Project, scope: S.Scope, row: dict) -> Path:
    f = results_file(project, scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return f


def read_results(project: Project, scope: S.Scope, check_id: str | None = None) -> list[dict]:
    f = results_file(project, scope)
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if check_id is None or r.get("check_id") == check_id:
            out.append(r)
    return out


def flaky_hint(rows: list[dict], check_id: str, new_verdict: str, flaky: bool, last: int = 6) -> str | None:
    """A stable (non-flaky) check whose stored results show BOTH passes and failures is a candidate to mark flaky (a suggestion, never applied)."""
    if flaky:
        return None
    verdicts = [r["verdict"] for r in rows if r.get("check_id") == check_id][-last:] + [new_verdict]
    if "pass" in verdicts and "fail" in verdicts:
        return f"'{check_id}' is not marked flaky but its last results mix pass and fail ({', '.join(verdicts)}): consider adding it to `flaky:` in playtest.yaml"
    return None


def baseline_name(name: str) -> str:
    return f"baseline-{name}"


def versioner(project: Project, scope: S.Scope) -> dryrun.Versioner:
    return dryrun.Versioner(place_project(project, scope))


def load_baseline(project: Project, scope: S.Scope, name: str) -> dict | None:
    v = versioner(project, scope)
    try:
        doc = json.loads(v.load(baseline_name(name)))
    except FileNotFoundError:
        return None
    problems = validate_baseline(doc)
    if problems:
        raise ValueError(f"stored baseline '{name}' is unreadable: " + "; ".join(problems))
    return doc
