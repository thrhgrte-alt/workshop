"""Versioned evaluation: run a deterministic baseline or check an agent's saved results.

Two modes share the same task files and checks:

* ``run_suite`` runs a *solver* (the repo's deterministic pipeline) against each task.
* ``check_results_dir`` checks results an AI agent produced, saved as
  ``<task_id>.json``. This measures the agent, not just the code.

Reports are plain JSON, stamped with git revision and a label, so two runs
(a new model, prompt, recipe, or library) can be compared with ``compare_reports``.

Rubric format (``evals/rubric.yaml``, see ``rubric.py``): ``criteria`` each with ``id``, ``method`` (``auto``, ``manual`` or ``hybrid``), a positive ``weight``, optional
``pass_at`` and ``required``; ``score_rubric(rubric, auto={...}, manual={...})`` keeps automated and manual scores apart and lists what is still unscored, so a partial score is
never presented as a verdict. Subjective criteria are judgments.

Task format (YAML, one file may hold a list)::

    - id: dry-run-writes-nothing        # unique
      tags: [dryrun]                     # optional
      source: self_written | real        # optional; set from the folder by ``load_split``
      known_gap: false                   # optional: a known miss, reported but kept visible
      input: {...}                       # free-form, handed to the solver
      checks:                            # at least one; {type: <check name>, ...args}
        - {type: equals, path: dry_run, value: true}

Self-written vs real. Evals the builder wrote next to the code only show the tool agrees with itself. ``load_split`` /
``run_split`` keep ``evals/tasks`` (self-written) and ``evals/real`` (examples the user supplied) apart and every report and
Markdown rendering shows the two counts separately; an empty ``real`` folder is reported as ZERO real results, not as a pass.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from .rubric import load_rubric, score_rubric, validate_rubric  # noqa: F401  (the rubric format lives in rubric.py; re-exported so evals is the one import)

CheckFn = Callable[..., tuple[bool, str]]


def load_tasks(directory: Path, *, strict: bool = False) -> list[dict]:
    tasks = []
    for f in sorted(Path(directory).glob("*.y*ml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for t in data if isinstance(data, list) else [data]:
            if "id" not in t or "checks" not in t:
                raise ValueError(f"{f}: every task needs 'id' and 'checks'")
            if strict and validate_task(t):
                raise ValueError(f"{f}: " + "; ".join(validate_task(t)))
            t["_file"] = f.name
            tasks.append(t)
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids")
    return tasks


def load_split(tasks_dir: Path, real_dir: Path | None = None, *, strict: bool = True) -> dict[str, list[dict]]:
    """``{"self_written": [...], "real": [...]}``. The ``real`` folder may be empty or missing (then the list is empty)."""
    own = load_tasks(tasks_dir, strict=strict)
    real = load_tasks(real_dir, strict=strict) if real_dir and Path(real_dir).is_dir() else []
    for t in own:
        t["source"] = "self_written"
    for t in real:
        t["source"] = "real"
    clash = {t["id"] for t in own} & {t["id"] for t in real}
    if clash:
        raise ValueError(f"task ids exist in both self-written and real sets: {sorted(clash)}")
    return {"self_written": own, "real": real}


def validate_task(task: dict) -> list[str]:
    """Problems with one task (empty list = fine). ``load_tasks(..., strict=True)`` raises on any."""
    problems = []
    if not isinstance(task.get("id"), str) or not task["id"].strip():
        problems.append("'id' must be a non-empty string")
    checks = task.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append(f"task {task.get('id')!r}: 'checks' must be a non-empty list (a task with no checks passes vacuously)")
    else:
        for i, c in enumerate(checks):
            if not isinstance(c, dict) or "type" not in c:
                problems.append(f"task {task.get('id')!r}: check #{i} needs a 'type'")
    if "tags" in task and not (isinstance(task["tags"], list) and all(isinstance(t, str) for t in task["tags"])):
        problems.append(f"task {task.get('id')!r}: 'tags' must be a list of strings")
    if task.get("source") not in (None, "self_written", "real"):
        problems.append(f"task {task.get('id')!r}: 'source' must be self_written or real")
    return problems


def dig(obj: Any, dotted: str, default: Any = None) -> Any:
    for part in dotted.split("."):
        if isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)] if int(part) < len(obj) else None
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return default
    return obj


# --- generic checks ------------------------------------------------------------
def check_equals(result, task, path, value):
    got = dig(result, path)
    return got == value, f"{path} == {value!r} (got {got!r})"


def check_in_range(result, task, path, min=None, max=None):
    got = dig(result, path)
    ok = isinstance(got, (int, float)) and (min is None or got >= min) and (max is None or got <= max)
    return ok, f"{path} in [{min}, {max}] (got {got!r})"


def check_has_keys(result, task, keys):
    missing = [k for k in keys if dig(result, k) is None]
    return not missing, f"missing keys: {missing}" if missing else "all keys present"


def check_no_findings(result, task, path="findings", severities=("error",)):
    findings = dig(result, path, []) or []
    bad = [f for f in findings if f.get("severity") in severities]
    return not bad, f"{len(bad)} finding(s) with severity {list(severities)}" + (f": {bad[0].get('message')}" if bad else "")


def check_has_finding(result, task, code_or_text, path="findings"):
    findings = dig(result, path, []) or []
    hit = any(code_or_text in json.dumps(f) for f in findings)
    return hit, f"expected a finding mentioning '{code_or_text}'"


def check_retrieved_any(result, task, ids, path="retrieved"):
    got = set(dig(result, path, []) or [])
    return bool(got & set(ids)), f"retrieved {sorted(got)}; wanted any of {ids}"


def check_retrieved_not(result, task, ids, path="retrieved"):
    got = set(dig(result, path, []) or [])
    return not (got & set(ids)), f"retrieved {sorted(got)}; must not include {ids}"


def check_file_exists(result, task, path):
    p = dig(result, path)
    return bool(p) and Path(p).exists(), f"{path} -> {p!r} exists"


GENERIC_CHECKS: dict[str, CheckFn] = {
    "equals": check_equals,
    "in_range": check_in_range,
    "has_keys": check_has_keys,
    "no_findings": check_no_findings,
    "has_finding": check_has_finding,
    "retrieved_any": check_retrieved_any,
    "retrieved_not": check_retrieved_not,
    "file_exists": check_file_exists,
}


def _git_rev(cwd: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True,
                              text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def evaluate_task(task: dict, result: Any, checks: dict[str, CheckFn]) -> dict:
    rows = []
    for spec in task["checks"]:
        spec = dict(spec)
        name = spec.pop("type")
        fn = checks.get(name)
        if fn is None:
            rows.append({"check": name, "passed": False, "message": f"unknown check '{name}'"})
            continue
        try:
            ok, msg = fn(result, task, **spec)
        except Exception as exc:  # a broken check is a failed check, not a crash
            ok, msg = False, f"check raised {type(exc).__name__}: {exc}"
        rows.append({"check": name, "passed": bool(ok), "message": msg})
    return {"id": task["id"], "passed": all(r["passed"] for r in rows), "checks": rows}


def _report(label: str, mode: str, root: Path, outcomes: list[dict], meta: dict | None) -> dict:
    return {
        "label": label,
        "mode": mode,
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "git_rev": _git_rev(root),
        "python": platform.python_version(),
        "meta": meta or {},
        "passed": sum(o["passed"] for o in outcomes),
        "total": len(outcomes),
        "tasks": outcomes,
    }


def run_suite(tasks: list[dict], solver: Callable[[dict], Any], checks: dict[str, CheckFn], *,
              label: str, root: Path, meta: dict | None = None) -> dict:
    merged = {**GENERIC_CHECKS, **checks}
    outcomes = []
    for t in tasks:
        try:
            result = solver(t)
            outcome = evaluate_task(t, result, merged)
        except Exception as exc:
            outcome = {"id": t["id"], "passed": False,
                       "checks": [{"check": "solver", "passed": False, "message": f"{type(exc).__name__}: {exc}"}]}
        outcomes.append(outcome)
    return _report(label, "solver", root, outcomes, meta)


def check_results_dir(tasks: list[dict], checks: dict[str, CheckFn], results_dir: Path, *,
                      label: str, root: Path, meta: dict | None = None) -> dict:
    merged = {**GENERIC_CHECKS, **checks}
    outcomes = []
    for t in tasks:
        f = Path(results_dir) / f"{t['id']}.json"
        if not f.exists():
            outcomes.append({"id": t["id"], "passed": False,
                             "checks": [{"check": "result_file", "passed": False, "message": f"missing {f.name}"}]})
            continue
        outcomes.append(evaluate_task(t, json.loads(f.read_text(encoding="utf-8")), merged))
    return _report(label, "agent-results", root, outcomes, meta)


def save_report(report: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"{report['label']}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def compare_reports(base: dict, new: dict) -> dict:
    b = {t["id"]: t["passed"] for t in base["tasks"]}
    n = {t["id"]: t["passed"] for t in new["tasks"]}
    return {
        "base": base["label"],
        "new": new["label"],
        "regressions": sorted(i for i in b if b[i] and not n.get(i, False)),
        "improvements": sorted(i for i in n if n[i] and not b.get(i, False)),
        "new_tasks": sorted(set(n) - set(b)),
        "removed_tasks": sorted(set(b) - set(n)),
        "base_score": f"{base['passed']}/{base['total']}",
        "new_score": f"{new['passed']}/{new['total']}",
    }


# --- reports: split, detail, Markdown ------------------------------------------------------------------------------
def run_split(split: dict[str, list[dict]], solver: Callable[[dict], Any], checks: dict[str, CheckFn], *,
              label: str, root: Path, meta: dict | None = None) -> dict:
    """Run both sets and keep them apart: ``{"label", "self_written": report, "real": report, "real_count"}``."""
    out = {"label": label}
    for name in ("self_written", "real"):
        out[name] = run_suite(split.get(name, []), solver, checks, label=f"{label}.{name}", root=root, meta=meta)
    out["real_count"] = len(split.get("real", []))
    return out


def compare_detailed(base: dict, new: dict) -> dict:
    """``compare_reports`` plus the failing check messages of every regression (what a reviewer needs to see)."""
    res = compare_reports(base, new)
    by_new = {t["id"]: t for t in new["tasks"]}
    res["regression_details"] = {i: [c["message"] for c in by_new[i]["checks"] if not c["passed"]] if i in by_new else ["task missing from the new report"]
                                 for i in res["regressions"]}
    res["regressed"] = bool(res["regressions"])
    return res


def render_markdown(report: dict, compare: dict | None = None) -> str:
    """A short Markdown report. Accepts a plain report or a ``run_split`` result."""
    lines: list[str] = []
    if "self_written" in report:
        sw, rl = report["self_written"], report["real"]
        lines += [f"# Eval report: {report['label']}", "",
                  f"- **Self-written** (written by the builder next to the code; shows the tool agrees with itself): {sw['passed']}/{sw['total']} passed",
                  (f"- **Real** (examples supplied by the user): {rl['passed']}/{rl['total']} passed" if rl["total"]
                   else "- **Real** (examples supplied by the user): 0 cases - no real-world result exists yet; drop examples into `evals/real/`"), ""]
        parts = [("Self-written", sw), ("Real", rl)]
    else:
        lines += [f"# Eval report: {report['label']}", "", f"- {report['passed']}/{report['total']} passed ({report['mode']}, git {report['git_rev']})", ""]
        parts = [("Tasks", report)]
    for title, rep in parts:
        failed = [t for t in rep["tasks"] if not t["passed"]]
        if failed:
            lines += [f"## {title}: failing", ""]
            for t in failed:
                msg = "; ".join(c["message"] for c in t["checks"] if not c["passed"])
                lines.append(f"- `{t['id']}`: {msg}")
            lines.append("")
    if compare:
        lines += ["## Compared with " + compare["base"], "",
                  f"- score {compare['base_score']} -> {compare['new_score']}",
                  f"- regressions: {', '.join(compare['regressions']) or 'none'}",
                  f"- improvements: {', '.join(compare['improvements']) or 'none'}",
                  f"- new tasks: {len(compare['new_tasks'])}, removed tasks: {len(compare['removed_tasks'])}", ""]
    return "\n".join(lines).rstrip() + "\n"


def save_markdown(report: dict, directory: Path, compare: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"{report['label']}.md"
    out.write_text(render_markdown(report, compare), encoding="utf-8")
    return out
