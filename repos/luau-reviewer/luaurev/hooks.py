"""Wires the Luau reviewer into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from . import tools as tools_mod
from .guide_adapter import Project, config, evals
from .domain import backends as B
from .domain import projects as PJ
from .domain import detectors as D
from .domain import learning, ruleset as RS
from .domain.lexer import mask, tokenize
from .domain.patch import suggest
from .domain.review import analyse_source, get_rules, review_target
from .domain.structure import Analysis

INSTRUCTIONS = """\
Luau script reviewer. Workflow: get_style_brief -> find_past_corrections (read them: they include false positives the user already rejected) ->
review_file / review_folder (own pattern rules + whichever of luau-analyze, selene and stylua are installed, in ONE report; every finding names the tool that produced it)
-> read the defects first, then the QUESTIONS (low confidence: ask the user, do not present them as bugs) -> explain_finding for the why -> suggest_patch (a unified diff as text;
this server never edits your files) -> suppress_finding (with a reason) or mark_false_positive (the user's verdict, so later reviews are quieter) -> record_run / record_decision.
find_remote_handlers and find_datastore_calls list the security- and data-sensitive spots; compare_reviews shows what a change fixed or broke.
Say exactly what was and was not checked: the report lists the backends that ran, the ones that are missing (never claim a missing tool ran) and the rule categories covered.
The checks are heuristics over a tokenizer, not a Luau parser. Never publish, never connect to Studio from here: get script source with the hub's script_read, save it to disk, review it."""

EXTENSIONS = {"script_case": [".lua", ".luau"], "good_script": [".lua", ".luau"], "bad_script": [".lua", ".luau"]}


def correction_dimensions(project: Project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    rules, settings = get_rules(project)
    be = {n: (B.available(n) or "not installed") for n in B.BACKENDS}
    return {
        "rules": {"count": len(rules), "categories": sorted({r.category for r in rules.values()}), "strict_only": sorted(r.id for r in rules.values() if r.strict_only),
                  "low_confidence_reported_as_questions": sorted(r.id for r in rules.values() if r.confidence == "low")},
        "backends": be,
        "backend_note": "a missing backend is reported as missing in every review; the own rules still run",
        "works_without_backends": ["all own pattern rules", "tokenizer", "module cycle check (folder)", "patch suggestions (diff text)", "ruleset suppressions", "false-positive learning", "evals"],
        "needs_external_tools": ["type and syntax errors (luau-analyze)", "selene lints", "format check (stylua)"],
        "never": ["edits your files", "connects to Studio", "publishes"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("LUAUREV_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["LUAUREV_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("LUAUREV_WORKSPACE", None)
            else:
                os.environ["LUAUREV_WORKSPACE"] = old


@contextlib.contextmanager
def _path_env(path_dirs: list[str] | None):
    """Temporarily replace PATH (None leaves it alone). Used to simulate 'backend missing' and to put stub executables first."""
    if path_dirs is None:
        yield
        return
    old = os.environ.get("PATH")
    os.environ["PATH"] = os.pathsep.join(path_dirs)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old


def make_stub(directory: Path, name: str, stdout: str = "", stderr: str = "", exit_code: int = 0) -> Path:
    """A tiny shell script standing in for an external tool: prints canned output, exits with a chosen code."""
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"{name}.stdout"
    err = directory / f"{name}.stderr"
    out.write_text(stdout, encoding="utf-8")
    err.write_text(stderr, encoding="utf-8")
    script = directory / name
    script.write_text(f"#!/bin/sh\ncat '{out}'\ncat '{err}' >&2\nexit {exit_code}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def summarise(report: dict) -> dict:
    defects, questions = report["findings"], report["questions"]
    own = lambda fs: [f for f in fs if f["source"] == "own"]
    return {"defects": defects, "questions": questions, "defect_rules": sorted({f["rule_id"] for f in own(defects)}), "question_rules": sorted({f["rule_id"] for f in own(questions)}),
            "all_defect_rules": sorted({f["rule_id"] for f in defects}), "errors": [f for f in defects if f["severity"] == "error"], "verdict": report["verdict"], "clean": report["clean"],
            "statement": report["statement"], "backends": {n: b["status"] for n, b in report["backends"].items()}, "suppressed": report["suppressed"], "learned": report["learned"],
            "past_corrections": report["past_corrections"], "summary": report["summary"], "table": report["table"], "not_checked": report["not_checked"],
            "checked": report["checked"], "overrides": report["overrides"], "project": report["project"], "summary_line": report["summary_line"],
            "places": sorted({f["place"] for f in defects + questions if f.get("place")}), "files": report["files"]}


DEFAULT_PROJECT = "example-sandbox"  # synthetic project with an empty ruleset: fixture expectations are not affected by overrides


def eval_solver(project: Project):
    def run_review(inp: dict, root: Path, workdir: Path) -> dict:
        target = inp.get("file") or inp.get("folder")
        if inp.get("code") is not None:
            f = workdir / inp.get("name", "snippet.server.luau")
            f.write_text(inp["code"], encoding="utf-8")
            target = str(f)
        else:
            target = str(root / target)
        ruleset_path = None
        if inp.get("ruleset"):
            ruleset_path = str(workdir / "ruleset.yaml")
            Path(ruleset_path).write_text(RS.dump({**RS.empty(), **inp["ruleset"]}), encoding="utf-8")
        return review_target(project, target, project_id=inp.get("project_id", DEFAULT_PROJECT), place_id=inp.get("place_id"), strict=inp.get("strict", False),
                             backends=inp.get("backends", []), ruleset_path=ruleset_path, context=inp.get("context"))

    def solve(task: dict) -> dict:
        if task["input"].get("catch"):
            try:
                return {"error": None, **solve_op(task)}
            except ValueError as exc:
                return {"error": str(exc)}
        return solve_op(task)

    def solve_op(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        root = project.root
        with _temp_workspace() as ws:
            sc = PJ.resolve_scope(project, inp.get("project_id", DEFAULT_PROJECT), inp.get("place_id"))
            store = learning.FalsePositiveStore(PJ.fp_store_path(project, sc, bool(inp.get("global"))))
            if op in ("review", "review_folder"):
                return summarise(run_review(inp, root, ws))
            if op == "learning_effect":
                first = summarise(run_review(inp, root, ws))
                for m in inp["mark"]:
                    code = m["code"] if m.get("code") else Path(root / inp["file"]).read_text(encoding="utf-8").split("\n")[m["line"] - 1]
                    st = learning.FalsePositiveStore(PJ.fp_store_path(project, PJ.resolve_scope(project, m.get("project_id", sc.project_id), m.get("place_id")), bool(m.get("global"))))
                    st.add(st.make_row(m["rule"], code, m["reason"], project_id=m.get("project_id", sc.project_id), place_id=m.get("place_id"), global_=bool(m.get("global"))))
                second = summarise(run_review(inp, root, ws))
                pick = lambda s, rule: next((f for f in s["defects"] + s["questions"] if f["rule_id"] == rule), None)
                rule = inp["rule"]
                a, b = pick(first, rule), pick(second, rule)
                return {"before": a and {"confidence": a["confidence"], "kind": a["kind"]}, "after": b and {"confidence": b["confidence"], "kind": b["kind"], "learned": b.get("learned")},
                        "defect_rules": second["defect_rules"], "question_rules": second["question_rules"], "past_corrections": second["past_corrections"]}
            if op == "tokens":
                src = inp["code"]
                toks = tokenize(src)
                return {"masked": mask(src, toks), "kinds": [t.kind for t in toks], "texts": [t.text for t in toks], "lines": [t.line for t in toks]}
            if op == "structure":
                a = Analysis.of(inp["code"])
                return {"balanced": a.balanced, "blocks": [b.kind for b in a.blocks], "functions": [f.label for f in a.functions], "calls": [c.callee for c in a.calls],
                        "params": {f.label: [p.name for p in f.params] for f in a.functions}}
            if op == "similarity":
                a, b = learning.normalise(inp["a"]), learning.normalise(inp["b"])
                return {"a": a, "b": b, "similarity": round(learning.similarity(a, b, inp.get("ngram", 3)), 4)}
            if op == "backend_parse":
                tool = inp["tool"]
                if tool == "luau-analyze":
                    found, ignored = B.parse_luau_analyze(inp["text"])
                elif tool == "selene":
                    found, ignored = B.parse_selene(inp["text"])
                else:
                    found, ignored = B.parse_stylua_check(inp["text"], inp.get("exit_code"), inp.get("files", []))
                return {"findings": [{"file": f.file, "line": f.line, "col": f.col, "rule_id": f.rule_id, "severity": f.severity, "problem": f.problem} for f in found], "ignored": ignored}
            if op == "backend_missing":
                empty = ws / "emptybin"
                empty.mkdir()
                with _path_env([str(empty)]):
                    rep = run_review({**inp, "backends": inp.get("backends", list(B.BACKENDS))}, root, ws)
                return summarise(rep)
            if op == "backend_stub":
                bindir = ws / "stubbin"
                here = str((root / inp["file"]).resolve())
                for name, spec in inp["stubs"].items():
                    make_stub(bindir, name, spec.get("stdout", "").replace("{FILE}", here), spec.get("stderr", "").replace("{FILE}", here), spec.get("exit", 0))
                with _path_env([str(bindir), "/usr/bin", "/bin"]):
                    rep = run_review({**inp, "backends": inp.get("backends", list(inp["stubs"]))}, root, ws)
                return summarise(rep)
            if op == "suppress":
                return summarise(run_review(inp, root, ws))
            if op == "patch":
                rules = get_rules(project)[0]
                src = (root / inp["file"]).read_text(encoding="utf-8")
                try:
                    res = suggest(src, Path(inp["file"]).name, inp["rule"], rules, line=inp.get("line"), strict=inp.get("strict", False))
                except ValueError as exc:
                    return {"error": str(exc)}
                return {"error": None, **res}
            if op == "finder":
                tools = {t.name: t for t in tools_mod.make_tools(project)}
                return {"error": None, **tools[inp["tool"]].fn(path=str(root / inp["path"]))}
            if op == "compare":
                tools = {t.name: t for t in tools_mod.make_tools(project)}
                return tools["compare_reviews"].fn(base=str(root / inp["base"]), new=str(root / inp["new"]), project_id=inp.get("project_id", DEFAULT_PROJECT), strict=inp.get("strict", False))
            if op == "compare_code":
                for side in ("a", "b"):
                    (ws / side).mkdir()
                    (ws / side / inp.get("name", "x.server.luau")).write_text(inp["base_code" if side == "a" else "new_code"], encoding="utf-8")
                tools = {t.name: t for t in tools_mod.make_tools(project)}
                return tools["compare_reviews"].fn(base=str(ws / "a" / inp.get("name", "x.server.luau")), new=str(ws / "b" / inp.get("name", "x.server.luau")),
                                                   project_id=inp.get("project_id", DEFAULT_PROJECT), strict=inp.get("strict", False))
            if op == "suppress_write":
                tools = {t.name: t for t in tools_mod.make_tools(project)}
                args = {k: inp[k] for k in ("rule_id", "reason", "project_id", "place_id", "file", "line", "apply_globally") if k in inp}
                args.setdefault("rule_id", inp.get("rule"))
                if args.get("file"):
                    args["file"] = str(root / args["file"])
                res = tools["suppress_finding"].fn(**args)
                return {"dry_run": res["dry_run"], "written": res.get("written", False), "layer": res["layer"], "applies_to": res["applies_to"], "entry": res["entry"],
                        "ruleset_path": res["ruleset_path"], "tracked_ruleset_changed": False}
            if op == "tool_size":
                tools = {t.name: t for t in tools_mod.make_tools(project)}
                args = {k: (str(root / v) if k in ("path", "file", "base", "new") and isinstance(v, str) else v) for k, v in inp["args"].items()}
                if inp["tool"] not in ("list_rules",) and "project_id" not in args:
                    args["project_id"] = DEFAULT_PROJECT
                res = tools[inp["tool"]].fn(**args)
                return {"chars": len(json.dumps(res, default=str)), "tool": inp["tool"], "description_chars": len(tools[inp["tool"]].description)}
            if op == "rules":
                rules, settings = get_rules(project)
                return {"ids": sorted(rules), "count": len(rules), "by_category": {c: sorted(r.id for r in rules.values() if r.category == c) for c in sorted({r.category for r in rules.values()})},
                        "low_confidence": sorted(r.id for r in rules.values() if r.confidence == "low"), "strict_only": sorted(r.id for r in rules.values() if r.strict_only)}
            raise ValueError(f"unknown eval op '{op}'")

    return solve


# ----------------------------------------------------------------------------------------------------------------------------------
# precision and recall
# ----------------------------------------------------------------------------------------------------------------------------------
def pr_counts(task: dict, result: dict) -> tuple[int, int, int]:
    exp_d, exp_q = set(task.get("expect_rules", [])), set(task.get("expect_questions", []))
    got_d, got_q = set(result.get("defect_rules", [])), set(result.get("question_rules", []))
    tp = len(exp_d & got_d) + len(exp_q & got_q)
    fp = len(got_d - exp_d) + len(got_q - exp_q)
    fn = len(exp_d - got_d) + len(exp_q - got_q)
    return tp, fp, fn


def pr_from_report(report: dict, include_gaps: bool = True, gap_ids: set[str] | None = None) -> dict:
    """Aggregate precision and recall from a saved eval report (the precision_recall check messages carry tp/fp/fn)."""
    tp = fp = fn = 0
    gap_tp = gap_fn = 0
    tasks = 0
    for t in report["tasks"]:
        for c in t["checks"]:
            if c["check"] != "precision_recall":
                continue
            m = re.match(r"tp=(\d+) fp=(\d+) fn=(\d+)", c["message"])
            if not m:
                continue
            a, b, d = map(int, m.groups())
            tasks += 1
            if gap_ids and t["id"] in gap_ids:
                gap_tp, gap_fn = gap_tp + a, gap_fn + d
                if not include_gaps:
                    fp += b
                    continue
            tp, fp, fn = tp + a, fp + b, fn + d
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"tasks": tasks, "tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4), "known_gap_planted_bugs": gap_tp + gap_fn, "known_gap_caught": gap_tp}


def eval_checks(project: Project) -> dict:
    dig = evals.dig

    def has_rule(result, task, rule):
        return rule in result.get("all_defect_rules", []), f"defects {result.get('all_defect_rules')}; expected {rule}"

    def has_question(result, task, rule):
        return rule in result.get("question_rules", []), f"questions {result.get('question_rules')}; expected {rule}"

    def no_rule(result, task, rule):
        got = set(result.get("all_defect_rules", [])) | set(result.get("question_rules", []))
        return rule not in got, f"{rule} must not be reported (got {sorted(got)})"

    def no_findings(result, task):
        n = len(result.get("defects", [])) + len(result.get("questions", []))
        return n == 0, f"expected zero findings, got {n}: " + ", ".join(f"{f['rule_id']}@{f['line']}" for f in result.get("defects", []) + result.get("questions", []))[:300]

    def no_errors(result, task):
        return not result.get("errors"), f"{len(result.get('errors', []))} error finding(s)"

    def rule_at_line(result, task, rule, line):
        lines = [f["line"] for f in result.get("defects", []) + result.get("questions", []) if f["rule_id"] == rule]
        return line in lines, f"{rule} on lines {lines}; expected {line}"

    def rule_confidence(result, task, rule, confidence):
        got = [f["confidence"] for f in result.get("defects", []) + result.get("questions", []) if f["rule_id"] == rule]
        return confidence in got, f"{rule} confidences {got}; expected {confidence}"

    def rule_severity(result, task, rule, severity):
        got = [f["severity"] for f in result.get("defects", []) + result.get("questions", []) if f["rule_id"] == rule]
        return severity in got, f"{rule} severities {got}; expected {severity}"

    def precision_recall(result, task, allow_miss=False):
        tp, fp, fn = pr_counts(task, result)
        ok = fp == 0 and (fn == 0 or allow_miss)
        note = " (documented known gap: miss allowed)" if allow_miss and fn else ""
        return ok, (f"tp={tp} fp={fp} fn={fn} expected={sorted(task.get('expect_rules', []))}+q{sorted(task.get('expect_questions', []))} "
                    f"got={result.get('defect_rules')}+q{result.get('question_rules')}{note}")

    def contains(result, task, path, text):
        got = dig(result, path)
        blob = json.dumps(got) if not isinstance(got, str) else got
        return text in blob, f"{path} should contain {text!r} (got {blob[:200]!r})"

    def excludes(result, task, path, text):
        got = dig(result, path)
        blob = json.dumps(got) if not isinstance(got, str) else got
        return text not in blob, f"{path} must not contain {text!r}"

    def approx(result, task, path, value, tol=0.001):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention {text!r} (got {err[:160]!r})"

    def count_is(result, task, path, value):
        got = dig(result, path)
        n = len(got) if isinstance(got, (list, dict, str)) else got
        return n == value, f"len({path}) == {value} (got {n})"

    return {"has_rule": has_rule, "has_question": has_question, "no_rule": no_rule, "no_findings": no_findings, "no_errors": no_errors, "rule_at_line": rule_at_line,
            "rule_confidence": rule_confidence, "rule_severity": rule_severity, "precision_recall": precision_recall, "contains": contains, "excludes": excludes, "approx": approx,
            "list_contains": list_contains, "list_excludes": list_excludes, "error_contains": error_contains, "count_is": count_is}


# ----------------------------------------------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------------------------------------------
def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("review", help="review a Luau file or folder (own rules + installed backends); exit 1 if any error-severity defect")
    p.add_argument("path")
    p.add_argument("--project", help="project id from projects.yaml (required: nothing is guessed)")
    p.add_argument("--place", help="place id (optional; otherwise files are matched to places by folder)")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--backends", default=None, help="comma list of luau-analyze,selene,stylua; 'none' for own rules only (default: all installed)")
    p.add_argument("--ruleset", help="an extra ruleset layer applied after global, project and place")
    p.add_argument("--context", choices=["server", "client", "shared"])
    p.add_argument("--json", action="store_true", help="print the full JSON report instead of the table")
    p.add_argument("--out", help="also save the JSON report here (use with compare-reviews)")
    p.set_defaults(handler=_review)

    p = sub.add_parser("rules", help="list the own rules")
    p.add_argument("--category")
    p.set_defaults(handler=lambda a, pr: config.emit({t.name: t for t in tools_mod.make_tools(pr)}["list_rules"].fn(category=a.category)))

    p = sub.add_parser("compare-reviews", help="what is new / fixed between two saved reports, files or folders")
    p.add_argument("base")
    p.add_argument("new")
    p.add_argument("--project")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(handler=_compare)

    p = sub.add_parser("eval-pr", help="precision and recall of the eval suite (runs it); --compare BASE_LABEL adds deltas against a saved report")
    p.add_argument("--label", default="pr")
    p.add_argument("--compare", metavar="BASE_LABEL")
    p.add_argument("--report", metavar="LABEL", help="do not run: compute from the saved report evals/reports/LABEL.json")
    p.set_defaults(handler=_eval_pr)


def _review(a, pr) -> int:
    rep = review_target(pr, a.path, project_id=a.project, place_id=a.place, strict=a.strict, backends=a.backends, ruleset_path=a.ruleset, context=a.context)
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    if a.json:
        config.emit(rep)
    else:
        print(rep["table"])
    return 1 if rep["summary"]["by_severity"]["error"] else 0


def _compare(a, pr) -> int:
    res = {t.name: t for t in tools_mod.make_tools(pr)}["compare_reviews"].fn(base=a.base, new=a.new, project_id=a.project, strict=a.strict)
    config.emit(res)
    return 1 if res["regression"] else 0


def _gap_ids(project: Project) -> set[str]:
    return {t["id"] for t in evals.load_tasks(project.root / "evals" / "tasks") if t.get("known_gap")}


def _eval_pr(a, pr) -> int:
    reports = pr.root / "evals" / "reports"
    gaps = _gap_ids(pr)
    if a.report:
        rep = json.loads((reports / f"{a.report}.json").read_text(encoding="utf-8"))
    else:
        rep = evals.run_suite(evals.load_tasks(pr.root / "evals" / "tasks"), eval_solver(pr), eval_checks(pr), label=a.label, root=pr.root)
        evals.save_report(rep, reports)
    full = pr_from_report(rep, True, gaps)
    out = {"label": rep["label"], "passed": rep["passed"], "total": rep["total"], "precision": full["precision"], "recall": full["recall"],
           "tp": full["tp"], "fp": full["fp"], "fn": full["fn"], "known_gap_planted_bugs": full["known_gap_planted_bugs"], "known_gap_caught": full["known_gap_caught"],
           "note": "recall counts the planted bugs in known_gaps.yaml as misses; precision counts every unexpected defect or question as a false positive"}
    if a.compare:
        base = json.loads((reports / f"{a.compare}.json").read_text(encoding="utf-8"))
        b = pr_from_report(base, True, gaps)
        cmp = evals.compare_reports(base, rep)
        out["compare"] = {"base": a.compare, "base_precision": b["precision"], "base_recall": b["recall"], "precision_delta": round(full["precision"] - b["precision"], 4),
                          "recall_delta": round(full["recall"] - b["recall"], 4), "regressions": cmp["regressions"], "improvements": cmp["improvements"], "new_tasks": cmp["new_tasks"]}
    config.emit(out)
    return 0 if rep["passed"] == rep["total"] else 1


HOOKS = config.DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver,
    eval_checks=eval_checks,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
