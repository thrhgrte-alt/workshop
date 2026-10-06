"""Wires the playtest-QA domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import copy
import inspect
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import learning_params
from . import tools as tools_mod
from .domain import checks as K
from .domain import console as CON
from .domain import gen, harness, judge, places
from .domain import report as RP
from .guide_adapter import DomainHooks, all_tools, config, evals as evals_mod, mcpkit, params as P, scope as S, skillgen

INSTRUCTIONS = """\
Roblox Studio playtest QA. Workflow: list_checks -> get the open place from the hub's list_roblox_studios -> plan_playtest(studios) -> for each execute_luau step call
generate_check_script(dry_run=false) and run its luau in the OPEN Studio session (never a live server) -> save each raw answer, the console text (get_console_output) and any screenshot path
-> explain_failure(check_id, results=[one raw answer per run], console_log, screenshots): ONE-LINE VERDICT FIRST, then failures each with the check, the evidence (log lines, measured value,
screenshot path) and the most likely cause (a heuristic reading, not a diagnosis); a pass lists exactly what was checked -> record_result / record_run / record_decision (dry run first).
RULES: every tool needs project_id AND place_id; the open Studio place must be the named one or the tool refuses. A data check runs only in TEST MODE and is refused without a
test-mode switch in the place's playtest.yaml; no script touches a DataStore. A flaky check is re-run N times (plan_playtest does it) before a failure is called; a failure in fewer runs
than N is INCONCLUSIVE, a mixed result is FLAKY. Every threshold is a PLACEHOLDER (the user has not given pacing or budgets) and every parser of hub output is schema_unverified (no real
capture exists yet): say so. This server never connects to Studio and never publishes; the hub does the running. Never claim a check ran without the hub's answers."""

EXTENSIONS = {"check_result": [".json"], "baseline": [".json"], "failure_case": [".json", ".txt"]}


def correction_dimensions(project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project) -> dict:
    out: dict[str, Any] = {}
    try:
        reg = places.load_registry(project)
        rows = []
        for pe in reg.projects:
            for pl in pe.places:
                try:
                    ctx = places.resolve(project, pe.project_id, pl.place_id)
                    rows.append({"place": ctx.label, "synthetic": pe.synthetic, "sections": sorted(s for s in ("boot", "spawn", "reachability", "remotes", "economy", "data", "performance") if s in ctx.cfg)})
                except Exception as exc:
                    rows.append({"place": f"{pe.project_id}/{pl.place_id}", "error": f"{type(exc).__name__}: {exc}"})
        out["projects"] = {"registry": str(places.registry_path(project)), "places": rows}
    except Exception as exc:
        out["projects"] = {"registry": str(places.registry_path(project)), "error": f"{type(exc).__name__}: {exc}"}
    out["luau_mock"] = "lupa available: generated scripts run on the mock world in tests" if learning_mock() else "lupa missing (pip install '.[dev]'): scripts are linted but not run on the mock"
    out["roblox_studio"] = "this server never connects to Studio: it emits Luau and a run plan; the hub (roblox_studio_start_stop_play, execute_luau, get_console_output, screen_capture) runs them"
    out["input_formats"] = "schema_unverified: no real capture of execute_luau, get_console_output, screen_capture or list_roblox_studios exists here (see samples/README.md)"
    out["thresholds"] = "every number in style/style.yaml is a PLACEHOLDER"
    return out


def learning_mock() -> bool:
    from .guide_adapter import mock

    return mock.available()


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("PLAYQA_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["PLAYQA_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("PLAYQA_WORKSPACE", None)
            else:
                os.environ["PLAYQA_WORKSPACE"] = old


STUDIOS = {
    "demo_mine/main": [{"name": "Demo Mine Main (SYNTHETIC)", "place_id": 0, "studio_id": "studio-mine"}],
    "demo_tycoon/main": [{"name": "Demo Tycoon (SYNTHETIC)", "place_id": 9000000001, "studio_id": "studio-tycoon"}],
    "demo_obby/main": [{"name": "Demo Obby (SYNTHETIC)", "place_id": 0, "studio_id": "studio-obby"}],
}


def dummy_args(tool, scope: tuple[str, str] | None = None) -> dict:
    """Minimal valid arguments for any tool (used by the scope evals), optionally scoped to one place."""
    given = {"request": "x", "run_id": "run-x", "decision": "accept", "check_id": "boot", "kind": "check_result", "title": "t", "description": "d", "tags": [], "path": "examples/results/x.json",
             "results": [], "console_log": "10:00:00.000 hello", "studios": STUDIOS["demo_mine/main"]}
    sig = inspect.signature(tool.fn).parameters
    args = {n: given[n] for n, prm in sig.items() if n in given and (prm.default is inspect.Parameter.empty or n in ("studios", "check_id", "results", "console_log"))}
    if scope:
        if "project_id" in sig:
            args["project_id"] = scope[0]
        if "place_id" in sig:
            args["place_id"] = scope[1]
    return args


def _flat(rep: dict) -> dict:
    return {"verdict": rep["verdict"], "summary": rep["summary"], "check_id": rep["check"]["id"], "repeats": rep["repeats"], "failures": rep.get("failures", []),
            "failed": sorted({f"{f['assertion']}" + (f"[{f['subject']}]" if f.get("subject") else "") for f in rep.get("failures", [])}), "checked": rep.get("checked", []), "not_checked": rep.get("not_checked", []),
            "notes": rep.get("notes", []), "report": rep}


def eval_solver(project, trial: list[tuple[str, Any, S.Scope]] | None = None):
    """``trial`` = parameter values to put in force inside each task's private workspace (what the improvement gate uses to test a proposal before anyone approves it)."""
    lib = K.load_library(project.root)
    patterns, causes = CON.load_patterns(project.root), RP.load_causes(project.root)
    dig = evals_mod.dig

    def pctx_of(key: str = "demo_mine/main"):
        pid, plid = key.split("/")
        return places.resolve(project, pid, plid)

    def solve(task: dict) -> dict:
        inp = task["input"]
        op = inp["op"]
        with _temp_workspace() as ws:
            if trial:
                ps_trial = learning_params.store(project)
                for name, value, sc in trial:
                    ps_trial.update(name, value, sc, approved_by="gate-trial", reason="trial of a proposal inside a private workspace; nothing is kept")
            if op == "world_check":
                args = {k: v for k, v in inp.items() if k not in ("op", "check")}
                out = harness.run_world_check(project, inp["check"], **args)
                return {**_flat(out["report"]), **out}
            if op == "judge_docs":
                pc = pctx_of(inp.get("place", "demo_mine/main"))
                cfg = harness.patch_cfg(pc.cfg, inp.get("cfg_patch"))
                style = copy.deepcopy(pc.style)
                for k, v in (inp.get("setting_patch") or {}).items():
                    style["settings"][k]["value"] = v
                for k, v in (inp.get("range_patch") or {}).items():
                    style["ranges"][k].update(v)
                agg, rep = judge.judge(lib[inp["check"]], cfg, style, inp["results"], console=inp.get("console"), screenshots=inp.get("screenshots"), baseline=inp.get("baseline"),
                                       patterns=patterns, causes=causes, planned=inp.get("planned"), detail=inp.get("detail", False))
                return _flat(rep)
            if op == "parse_console":
                pc = pctx_of(inp.get("place", "demo_mine/main"))
                marker = tuple(inp["marker"]) if inp.get("marker") else None
                res = CON.parse(inp["console"], patterns, window_seconds=inp.get("window_seconds"), marker=marker, ignore=inp.get("ignore"), max_findings=inp.get("max_findings", 10), detail=inp.get("detail", False))
                res.pop("_entries", None)
                return res
            if op == "gen_script":
                pc = pctx_of(inp.get("place", "demo_mine/main"))
                cfg = harness.patch_cfg(pc.cfg, inp.get("cfg_patch"))
                try:
                    s = gen.generate(lib[inp["check"]], cfg, pc.style, pc.entry, pc.label, repeat_no=inp.get("repeat_no", 1), phase=inp.get("phase"), probes=gen.load_probes(project.root))
                except ValueError as exc:
                    return {"error": str(exc)}
                return {"lines": s.lines, "lint": s.lint, "sha256": s.sha256, "luau": s.luau, "context": s.context}
            if op == "tools_seq":
                specs = all_tools(project, HOOKS)
                results: list[dict] = []

                def subst(v):
                    if isinstance(v, dict):
                        return {k: subst(x) for k, x in v.items()}
                    if isinstance(v, list):
                        return [subst(x) for x in v]
                    if isinstance(v, str) and v.startswith("$ref:"):
                        return dig({"steps": results}, v[5:])
                    if isinstance(v, str) and v.startswith("$world:"):  # $world:<check>:<fault,fault>[:<context>] -> the raw script answer on the mock world
                        parts = v[7:].split(":")
                        cid, faults = parts[0], [f for f in parts[1].split(",") if f] if len(parts) > 1 else []
                        out = harness.run_world_check(project, cid, faults=faults, repeats=1, context=parts[2] if len(parts) > 2 else None)
                        return out["raws"][0]
                    if v == "$big:":
                        return "x" * 5_000_001
                    if isinstance(v, str) and v.startswith("$perf:"):  # $perf:<faults> -> the raw answers of a performance run (start, end)
                        return harness.run_world_check(project, "perf_snapshot", faults=[f for f in v[6:].split(",") if f], repeats=1)["raws"]
                    if isinstance(v, str) and v.startswith("$console:"):  # $console:<check>:<faults> -> the console text of that run
                        parts = v[9:].split(":")
                        out = harness.run_world_check(project, parts[0], faults=[f for f in parts[1].split(",") if f] if len(parts) > 1 else [], repeats=1)
                        return out["console_last"]
                    return v

                for st in inp["steps"]:
                    try:
                        r = mcpkit.call_local(specs, st["tool"], subst(st.get("args", {})))
                        results.append({"ok": True, "result": r, "chars": len(json.dumps(r, default=str))})
                    except (ValueError, PermissionError, FileNotFoundError) as exc:
                        results.append({"ok": False, "error": str(exc), "result": {}})
                wrote = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
                return {"steps": results, "wrote": wrote, "n_errors": sum(1 for r in results if not r["ok"])}
            if op in ("refuse_all", "scope_stated"):
                specs = all_tools(project, HOOKS)
                if op == "refuse_all":
                    not_refused, checked = [], 0
                    for t in specs:
                        args = dummy_args(t)
                        try:
                            mcpkit.call_local(specs, t.name, args)
                            not_refused.append(t.name)
                        except ValueError as exc:
                            msg = str(exc)
                            if "project_id" not in msg or "place_id" not in msg:
                                not_refused.append(f"{t.name}: {msg[:80]}")
                        checked += 1
                    return {"not_refused": not_refused, "checked": checked}
                missing = []
                for t in specs:
                    if not t.read_only or t.name in ("find_past_corrections",):
                        pass
                    args = dummy_args(t, scope=("demo_mine", "main"))
                    try:
                        res = mcpkit.call_local(specs, t.name, args)
                    except ValueError:
                        continue
                    ok = (res.get("project_id"), res.get("place_id")) == ("demo_mine", "main") and next(iter(res)) == "summary" and bool(res.get("summary"))
                    if not ok:
                        missing.append(t.name)
                return {"missing": missing}
            if op == "learning":
                ps = learning_params.store(project)
                errors: list[dict] = []
                for st in inp.get("steps", []):
                    pid, plid = st["scope"].split("/")
                    sc = S.Scope(pid, plid)
                    try:
                        if st["do"] == "update":
                            ps.update(st["name"], st["value"], sc, approved_by=st.get("by", "amy"), reason=st.get("reason", "r"))
                        elif st["do"] == "rollback":
                            ps.rollback(st["name"], sc, approved_by="amy", reason="back")
                        else:
                            raise ValueError(st)
                    except P.ParamError as exc:
                        errors.append({"name": st["name"], "type": type(exc).__name__, "message": str(exc)})
                res: dict[str, Any] = {"errors": errors}
                if "world" in inp:
                    out = harness.run_world_check(project, inp["world"]["check"], **{k: v for k, v in inp["world"].items() if k != "check"})
                    res.update(out)
                pc = pctx_of(inp.get("place", "demo_mine/main"))
                res["values"] = {n.replace(".", "__"): pc.style["settings"][n[8:]]["value"] if n.startswith("setting.") else pc.style["ranges"][n.split(".")[1]][n.split(".")[2]] for n in inp.get("show", [])}
                return res
            raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project) -> dict:
    dig = evals_mod.dig

    def approx(result, task, path, value, tol=1e-6):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def list_equals(result, task, path, items):
        got = dig(result, path) or []
        return sorted(got) == sorted(items), f"{path}={sorted(got)}; expected exactly {sorted(items)}"

    def list_empty(result, task, path):
        got = dig(result, path)
        return got == [] or got is None, f"{path}={got!r} should be empty"

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:160]!r})"

    def greater(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got > than, f"{path}={got!r} should be > {than}"

    def less(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got < than, f"{path}={got!r} should be < {than}"

    def text_contains(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() in got.lower(), f"{path} should mention '{text}' (got {got[:160]!r})"

    def text_excludes(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() not in got.lower(), f"{path} must not mention '{text}' (got {got[:160]!r})"

    def step_error_contains(result, task, step, text):
        err = dig(result, f"steps.{step}.error") or ""
        return text.lower() in err.lower(), f"step {step} should be refused mentioning '{text}' (got {err[:200]!r})"

    def step_ok(result, task, step):
        ok = dig(result, f"steps.{step}.ok")
        return ok is True, f"step {step} should succeed (got {dig(result, f'steps.{step}.error')!r})"

    def same(result, task, a, b):
        x, y = dig(result, a), dig(result, b)
        return x is not None and x == y, f"{a}={x!r} vs {b}={y!r}"

    def every_failure_has_evidence(result, task, path="failures"):
        fails = dig(result, path) or []
        bad = []
        for f in fails:
            ev = f.get("evidence") or {}
            if not (f.get("assertion") and f.get("most_likely_cause") and f.get("cause_rule") and {"measured", "log_lines", "screenshots"} <= set(ev) and ev.get("detail")):
                bad.append(f.get("assertion"))
        return bool(fails) and not bad, f"{len(fails)} failure(s); incomplete evidence/cause on {bad}"

    return {"step_error_contains": step_error_contains, "step_ok": step_ok, "approx": approx, "list_contains": list_contains, "list_excludes": list_excludes, "list_equals": list_equals,
            "list_empty": list_empty, "error_contains": error_contains, "greater": greater, "less": less, "text_contains": text_contains, "text_excludes": text_excludes, "same": same,
            "every_failure_has_evidence": every_failure_has_evidence}


def register_cli(sub, project) -> None:
    skillgen.add_export_skill_command(sub, project, learning_params.skill_kwargs, resolve=learning_params.resolve_scope)
    p = sub.add_parser("eval-report", help="run self-written and real (evals/real) evals and print a report that keeps the two apart")
    p.add_argument("--label", default="report")
    p.add_argument("--write", action="store_true", help="save evals/reports/<label>.json and .md")
    p.set_defaults(handler=_eval_report)
    p = sub.add_parser("places", help="list the projects and places in projects.yaml")
    p.set_defaults(handler=lambda a, pr: config.emit({"registry": str(places.registry_path(pr)), "places": places.known_places(places.load_registry(pr))}))
    p = sub.add_parser("checks", help="which checks can run for a place")
    p.add_argument("--project-id", required=True)
    p.add_argument("--place-id", required=True)
    p.set_defaults(handler=lambda a, pr: (config.emit(mcpkit.call_local(all_tools(pr, HOOKS), "list_checks", {"project_id": a.project_id, "place_id": a.place_id})), 0)[1])


def _eval_report(a, pr) -> int:
    split = evals_mod.load_split(pr.root / "evals" / "tasks", pr.root / "evals" / "real")
    rep = evals_mod.run_split(split, eval_solver(pr), eval_checks(pr), label=a.label, root=pr.root)
    md = evals_mod.render_markdown(rep)
    print(md)
    if a.write:
        out = pr.root / "evals" / "reports"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{a.label}.split.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
        (out / f"{a.label}.md").write_text(md, encoding="utf-8")
    return 0 if rep["self_written"]["passed"] == rep["self_written"]["total"] and rep["real"]["passed"] == rep["real"]["total"] else 1


HOOKS = DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver,
    eval_checks=eval_checks,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
