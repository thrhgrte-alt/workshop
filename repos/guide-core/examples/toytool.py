"""A toy tool built on guide-core. It does almost nothing useful on purpose: it exists to exercise every public module.

What it does: checks a Luau snippet (the shared safety lint plus two toy rules), renames a part in a per-place parts list (dry run by
default), generates an injection-safe ``SetAttribute`` script, and records feedback per project/place. Everything goes through
guide-core; there is no private re-implementation of dry-run, scope, feedback, params, lint or evals here.

``Toy(root, broken=...)`` can deliberately break one behaviour so the test-suite can prove the evals notice (a suite that cannot fail is
not worth reporting). ``solve`` is the eval solver used by ``evals/run_evals.py`` and the tests.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from guide_core import dryrun, evals, feedback, gate, luau_safety, mock, observe, promote, propose, scope as scope_mod, skillgen, telemetry
from guide_core.mcpkit import ToolSpec
from guide_core.params import ParamError, ParamSpec, ParamStore
from guide_core.project import Project
from guide_core.scope import ProjectRegistry, Scope, ScopeError

BROKEN_MODES = ("lint_off", "dryrun_writes", "scope_off", "feedback_leaks", "gate_blind", "redact_off")

PARAMS = [
    ParamSpec("lint.max_lines", 40, 10, 200, 10, description="scripts longer than this get a LEN warning"),
    ParamSpec("lint.todo_limit", 2, 0, 10, 1, description="more TODO comments than this get an info finding"),
    ParamSpec("lint.confidence", "high", choices=("low", "medium", "high"), description="confidence shown on toy findings"),
    ParamSpec("lint.safety_weight", 1.0, 0.0, 2.0, 0.25, locked=True, description="locked: the safety lint's weight never changes"),
]

REGISTRY = {"version": 1, "projects": [
    {"project_id": "demo-a", "alias": "Demo A (synthetic)", "universe_id": 1, "places": [{"place_id": "main", "studio_name": "Demo A Main", "roblox_place_id": 0},
                                                                                    {"place_id": "alt", "studio_name": "Demo A Alt", "roblox_place_id": 555}]},
    {"project_id": "demo-b", "alias": "Demo B (synthetic)", "places": [{"place_id": "main", "studio_name": "Demo B Main"}]},
    {"project_id": "demo-c", "alias": "Demo C (synthetic)", "places": [{"place_id": "main", "studio_name": "Demo C Main"}]},
]}


class Toy:
    def __init__(self, root: Path, broken: str | None = None):
        if broken is not None and broken not in BROKEN_MODES:
            raise ValueError(f"broken must be one of {BROKEN_MODES}")
        self.root, self.broken = Path(root), broken
        self.project = Project(name="toytool", package="toytool", env_prefix="TOYTOOL", root=self.root, domain="toy")
        self.registry = ProjectRegistry.from_dict(REGISTRY)
        self.params = ParamStore(self.project.workspace / "params.json", PARAMS)
        self.telemetry = telemetry.Telemetry(self.project.workspace / "telemetry.jsonl")
        self.log = observe.RunLog(self.project.workspace / "observations.jsonl")
        self.proposals = propose.ProposalStore(self.project.workspace / "proposals.jsonl")
        self.knowledge = promote.KnowledgeStore(self.project.workspace / "knowledge")

    # scope ------------------------------------------------------------------------------------------------------
    def scope(self, project_id: str | None, place_id: str | None = None, need_place: bool = False) -> Scope:
        if self.broken == "scope_off":  # BROKEN: accepts anything, even nothing
            return Scope(project_id or "anon", place_id)
        return scope_mod.require_scope(project_id, place_id, need_place=need_place, registry=self.registry)

    # check -------------------------------------------------------------------------------------------------------
    def check_script(self, code: str, project_id: str | None = None, place_id: str | None = None, values: Any = None) -> dict[str, Any]:
        sc = self.scope(project_id, place_id)
        pv = values or self.params
        findings: list[dict] = []
        issues = [] if self.broken == "lint_off" else luau_safety.lint(code)
        for msg in issues:
            findings.append({"rule": "LINT", "severity": "error", "message": msg, "confidence": "high"})
        n_lines = len(code.splitlines())
        if n_lines > pv.value("lint.max_lines"):
            findings.append({"rule": "LEN", "severity": "warn", "message": f"{n_lines} lines", "confidence": pv.value("lint.confidence")})
        todos = code.count("TODO")
        if todos > pv.value("lint.todo_limit"):
            findings.append({"rule": "TODO", "severity": "info", "message": f"{todos} TODO comments", "confidence": pv.value("lint.confidence")})
        errors = sum(f["severity"] == "error" for f in findings)
        shown = findings[:10]
        verdict = (f"BLOCKED: {errors} error(s), {len(findings) - errors} other finding(s)" if errors else
                   (f"OK with {len(findings)} note(s)" if findings else "OK: nothing found")) + f" [{sc.label}]"
        return {"verdict": verdict, "blocked": bool(errors), "findings": shown, "total_findings": len(findings), "truncated": len(findings) > len(shown), "scope": sc.to_dict()}

    # rename (dry run / apply) -----------------------------------------------------------------------------------------
    def _parts_file(self, sc: Scope) -> Path:
        return scope_mod.project_for_scope(self.project, sc).workspace / "parts.json"

    def seed_parts(self, sc: Scope, parts: list[str]) -> None:
        f = self._parts_file(sc)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(parts), encoding="utf-8")

    def parts(self, sc: Scope) -> list[str]:
        f = self._parts_file(sc)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def rename_part(self, project_id: str | None, place_id: str | None, old: str, new: str, apply: bool = False, expect: str | None = None) -> dict[str, Any]:
        sc = self.scope(project_id, place_id, need_place=True)
        parts = self.parts(sc)
        plan = dryrun.Plan(f"rename {old} -> {new} in {sc.label}")
        if old not in parts:
            plan.warnings.append(f"'{old}' does not exist")
        elif new in parts:
            plan.warnings.append(f"'{new}' already exists")
        else:
            plan.add("rename", old, f"-> {new}")

        def applier(p: dryrun.Plan) -> list[str]:
            dryrun.Versioner(scope_mod.project_for_scope(self.project, sc)).save("parts", json.dumps(parts), label=f"before {p.fingerprint()}")
            self._parts_file(sc).write_text(json.dumps([new if x == old else x for x in parts]), encoding="utf-8")
            return [old]

        do_apply = apply or self.broken == "dryrun_writes"  # BROKEN: writes although apply was not asked for
        if do_apply and plan.warnings:
            raise ValueError("refusing to apply a plan with warnings: " + "; ".join(plan.warnings))
        journal = self._parts_file(sc).parent / "journal.jsonl"
        return dryrun.run_plan(plan, applier, apply=do_apply, journal=journal, expect=expect)

    # generation --------------------------------------------------------------------------------------------------------
    def gen_set_attribute(self, path: str, attribute: str, value: Any, run_on_mock: bool = False) -> dict[str, Any]:
        luau_safety.validate_path(path, "path")
        luau_safety.validate_name(attribute, "attribute")
        if isinstance(value, bool):
            lit = "true" if value else "false"
        elif isinstance(value, (int, float)):
            lit = repr(value)
        else:
            lit = luau_safety.quote_string(str(value))
        parts = path.split(".")
        code = "\n".join([f'local node = game:GetService("{parts[0]}")'] + [f'node = node:FindFirstChild("{p}")' for p in parts[1:]] +
                         [f'node:SetAttribute("{attribute}", {lit})', 'return "done"'])
        luau_safety.assert_safe(code)
        out: dict[str, Any] = {"script": code, "lint": luau_safety.lint(code)}
        if run_on_mock:
            if not mock.available():
                out["mock_status"] = "skipped"
            else:
                m = mock.MockRoblox()
                m.ensure_path(path)
                m.run(code)
                out["mock_status"] = "ok"
                out["attribute"] = m.info(path)["attributes"].get(attribute)
        return out

    # feedback ----------------------------------------------------------------------------------------------------------
    def feedback_flow(self, steps: list[dict], query: str, project_id: str | None, place_id: str | None = None, strict: bool = True) -> dict[str, Any]:
        runs: dict[str, str] = {}
        for st in steps:
            sc = self.scope(st["project_id"], st.get("place_id")) if not st.get("unscoped") else None
            rid = feedback.record_run(self.project, request=st["request"], scope=sc, strict_scope=strict)
            runs[st["request"]] = rid
            if st.get("decision"):
                d = st["decision"]
                feedback.record_decision(self.project, rid, d["decision"], reason=d.get("reason", ""), corrections=d.get("corrections"), mark_global=d.get("global", False),
                                         tags=d.get("tags"))
        qs = None if self.broken == "feedback_leaks" else self.scope(project_id, place_id)  # BROKEN: asks without a scope, so everything is visible
        hits = feedback.find_past_corrections(self.project, query, scope=qs, strict_scope=strict and self.broken != "feedback_leaks")
        return {"hit_requests": [h["request"] for h in hits], "count": len(hits)}

    # tools (what a repository built on guide-core would expose) ------------------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        def check_script(code: str, project_id: str, place_id: str | None = None) -> dict[str, Any]:
            """Check a Luau snippet: safety lint plus toy rules. One-line verdict first."""
            return self.check_script(code, project_id, place_id)

        def rename_part(project_id: str, place_id: str, old: str, new: str, dry_run: bool = True) -> dict[str, Any]:
            """Rename a part in the place's parts list. Dry run by default."""
            return self.rename_part(project_id, place_id, old, new, apply=not dry_run)

        def explain_rule(rule: str) -> dict[str, Any]:
            """Describe a toy rule in detail."""
            return {"LINT": "shared Luau safety lint", "LEN": "script too long", "TODO": "too many TODO comments"}.get(rule) and {"rule": rule} or {"rule": rule, "unknown": True}

        return self.telemetry.instrument([
            ToolSpec("check_script", check_script, check_script.__doc__ or ""),
            ToolSpec("rename_part", rename_part, rename_part.__doc__ or "", read_only=False, idempotent=False),
            ToolSpec("explain_rule", explain_rule, explain_rule.__doc__ or "", group="rare"),
        ])


# --- eval support ----------------------------------------------------------------------------------------------------------
def _code(inp: dict) -> str:
    if "code_lines" in inp:
        return "\n".join(["local a = 1"] * inp["code_lines"])
    return inp.get("code", "")


def _refused(fn, *a, **kw) -> dict:
    try:
        return fn(*a, **kw)
    except (ScopeError, ValueError, ParamError, promote.PromotionError, feedback.ScopeError) as exc:
        return {"refused": True, "error": str(exc)}


def solve(task: dict, broken: str | None = None) -> dict:
    """Eval solver: run the task's ``input`` on a fresh toy workspace and return the result the checks inspect."""
    inp = task.get("input", {})
    op = inp["op"]
    with tempfile.TemporaryDirectory() as td:
        toy = Toy(Path(td), broken)
        if op == "check":
            return _refused(toy.check_script, _code(inp), inp.get("project_id"), inp.get("place_id"))
        if op == "gen_attr":
            return _refused(toy.gen_set_attribute, inp["path"], inp["attribute"], inp["value"], inp.get("mock", False))
        if op == "studio":
            def go():
                sc = toy.scope(inp["project_id"], inp["place_id"])
                hit = scope_mod.match_open_studio(toy.registry.place(sc), inp["studios"], label=sc.label)
                return {"matched_by": hit["matched_by"], "studio_id": hit.get("studio_id")}
            return _refused(go)
        if op == "rename":
            try:
                sc = toy.scope(inp.get("project_id"), inp.get("place_id"), need_place=True)
            except ScopeError as exc:
                return {"refused": True, "error": str(exc)}
            toy.seed_parts(sc, inp.get("parts", ["Door", "Lamp"]))
            res = _refused(toy.rename_part, inp["project_id"], inp["place_id"], inp["old"], inp["new"], inp.get("apply", False), inp.get("expect"))
            journal = toy._parts_file(sc).parent / "journal.jsonl"
            vers = scope_mod.project_for_scope(toy.project, sc).versions_dir / "parts" / "index.json"
            return {**res, "parts_after": toy.parts(sc), "journal_lines": len(journal.read_text().splitlines()) if journal.exists() else 0,
                    "versions_saved": len(json.loads(vers.read_text())) if vers.exists() else 0}
        if op == "feedback":
            return _refused(toy.feedback_flow, inp["steps"], inp["query"], inp.get("project_id"), inp.get("place_id"), inp.get("strict", True))
        if op == "feedback_index":
            sc = toy.scope("demo-a", "main")
            rid = feedback.record_run(toy.project, request="door bevels", scope=sc)
            feedback.record_decision(toy.project, rid, "revise", reason="too sharp", corrections=[{"dimension": "bevel", "note": "round door bevels"}])
            if inp["what"] == "rebuild":
                (toy.project.feedback_dir / "index.sqlite").unlink(missing_ok=True)
                hits = feedback.find_past_corrections(toy.project, "door bevels", scope=sc)
                return {"count": len(hits)}
            return {"schema_version": feedback.index_info(toy.project)["schema_version"]}
        if op in ("gate", "compare", "gate_correction"):
            return _gate_ops(toy, inp, op)
        if op == "params":
            return _params_ops(toy, inp)
        if op == "knowledge":
            return _knowledge_ops(toy, inp)
        if op == "telemetry":
            tools = {t.name: t for t in toy.tools()}
            for _ in range(3):
                tools["check_script"].fn(code="local x = 1", project_id="demo-a", place_id="main")
            s = toy.telemetry.summary()["check_script"]
            return {"calls": s["calls"], "chars_median": s["chars_median"], "budget_exceeded": telemetry.check_budgets(toy.telemetry.summary(), {"check_script": 400})}
        if op == "skill":
            if inp.get("tune"):
                toy.params.update("lint.max_lines", 50, Scope("demo-a", "main"), approved_by="tester", reason="toy")
            res = skillgen.export_skill(out_dir=Path(td) / "skills", name="toytool", description="Use when checking Luau snippets with the toy tool.", repo="toytool",
                                        scope=Scope("demo-a", "main") if inp.get("tune") else Scope.make_global(), tools=toy.tools(), workflow=["check_script first", "rename with dry_run"],
                                        verified=["lint on fixtures"], unverified=["never run against Studio"], params=toy.params, knowledge=toy.knowledge, project=toy.project)
            return {"problems": res["problems"], "skill_lines": res["skill_lines"], "skill_md": res["files"]["toytool/SKILL.md"], "parameters_md": res["files"]["toytool/references/parameters.md"]}
        if op == "observe":
            sc = toy.scope("demo-a", "main")
            rid = toy.log.log_run(request=inp["request"], scope=sc, extra={"note": inp["request"]}) if toy.broken != "redact_off" else _raw_log(toy, inp["request"], sc)
            return {"stored": json.dumps(toy.log.rows(kind="run")[0]), "run_id": rid}
    raise ValueError(f"unknown op {op}")


def _raw_log(toy: Toy, request: str, sc: Scope) -> str:  # BROKEN: writes without redaction
    toy.log._append({"kind": "run", "run_id": "obs-raw", "request": request, "extra": {"note": request}})
    return "obs-raw"


def _suite_from(inp: dict) -> dict:
    tasks = [{"id": t["id"], "input": {"op": "check", **t["input"], "project_id": "demo-a", "place_id": "main"}, "checks": t["checks"]} for t in inp.get("tasks", [])]
    return {"self_written": tasks}


def _solver_for(toy: Toy):
    def solver_for(prop: dict | None):
        values = toy.params.view(Scope("demo-a", "main"), {} if (prop is None or toy.broken == "gate_blind") else {prop["change"]["param"]: prop["change"]["after"]})
        return lambda t: toy.check_script(_code(t["input"]), t["input"].get("project_id"), t["input"].get("place_id"), values=values)
    return solver_for


def _gate_ops(toy: Toy, inp: dict, op: str) -> dict:
    sc = Scope("demo-a", "main")
    if op == "compare":
        tasks = [{"id": t["id"], "input": {**t["input"], "project_id": "demo-a", "place_id": "main"}, "checks": t["checks"]} for t in inp["tasks"]]
        base = evals.run_suite(tasks, _solver_for(toy)(None), CHECKS, label="base", root=toy.root)
        fake = {"change": {"param": inp["param"], "after": inp["after"]}}
        new = evals.run_suite(tasks, _solver_for(toy)(fake), CHECKS, label="new", root=toy.root)
        cmp = evals.compare_detailed(base, new)
        return {"regressions": cmp["regressions"], "new_score": cmp["new_score"], "base_score": cmp["base_score"]}
    suites = _suite_from(inp)
    if op == "gate_correction":
        rid = feedback.record_run(toy.project, request="35 line scripts are fine", scope=sc)
        feedback.record_decision(toy.project, rid, "accept", corrections=[{"dimension": "length", "note": "35 lines is not too long"}],
                                 regression_case={"input": {"code_lines": 35}, "checks": [{"type": "no_len_finding"}]})
        tasks, not_exec = gate.correction_tasks(toy.project, sc)
        suites = {"corrections": [{**t, "input": {**t["input"], "project_id": "demo-a", "place_id": "main"}} for t in tasks]}
    base_change = {"op": "set", "param": inp["param"], "before": toy.params.value(inp["param"], sc), "after": inp["after"], "step": 10}
    prop = {"id": "prop-test", "kind": "param", "scope": sc.to_dict(), "target": f"param:{inp['param']}", "change": base_change, "rationale": "test",
            "evidence": {"run_ids": [], "count": 0, "of": 0, "rate": 0, "projects": []}, "status": "proposed"}
    res = gate.run_gate(prop, suites, _solver_for(toy), CHECKS, root=toy.root)
    return {"verdict": res.verdict, "regressions": [r["id"] for r in res.regressions], "cases_run": res.cases_run, "notes": res.notes}


def _params_ops(toy: Toy, inp: dict) -> dict:
    sc = Scope("demo-a", "main")

    def go():
        out: dict[str, Any] = {}
        for step in inp["steps"]:
            if step["do"] == "update":
                toy.params.update(step["param"], step["value"], sc, approved_by=step.get("by", "tester"), reason="toy")
            elif step["do"] == "rollback":
                toy.params.rollback(step["param"], sc, approved_by="tester", reason="toy")
        r = toy.params.resolve(inp["param"], sc, inp.get("explicit"))
        out.update(value=r.value, source=r.source, version=r.version, locked=r.locked, explain=r.explain())
        return out

    return _refused(go)


def _knowledge_ops(toy: Toy, inp: dict) -> dict:
    kb, sig = toy.knowledge, inp.get("signature", "has_interaction+purchase_remote")
    try:
        for i, pid in enumerate(inp["learned_in"]):
            kb.learn(sig, text="pattern", project_id=pid, run_ids=[f"r{i}"], approved_by="tester")
        for pid in inp.get("contradicted_in", []):
            kb.contradict(sig, pid, note="did not hold", approved_by="tester")
        if inp.get("non_transferable"):
            kb.mark_non_transferable(sig, reason="held in one game only", approved_by="tester")
        out: dict[str, Any] = {"candidates": [c["signature"] for c in kb.global_candidates()],
                               "blocked_by": next((c["blocked_by"] for c in kb.candidate_report() if c["signature"] == sig), [])}
        if inp.get("promote_by") is not None:
            try:
                kb.promote_global(sig, approved_by=inp["promote_by"], confirm=inp.get("confirm", True))
                out["promoted"] = True
            except promote.PromotionError as exc:
                out.update(promoted=False, error=str(exc))
        return out
    except promote.PromotionError as exc:
        return {"refused": True, "error": str(exc)}


# --- checks ----------------------------------------------------------------------------------------------------------------------
def _dig(r, p):
    return evals.dig(r, p)


def check_contains(result, task, path, text):
    got = _dig(result, path)
    return isinstance(got, str) and text in got, f"{path} contains {text!r} (got {str(got)[:120]!r})"


def check_excludes(result, task, path, text):
    got = _dig(result, path)
    return isinstance(got, str) and text not in got, f"{path} excludes {text!r} (got {str(got)[:120]!r})"


def check_count(result, task, path, value):
    got = _dig(result, path)
    n = len(got) if isinstance(got, (list, dict, str)) else None
    return n == value, f"len({path}) == {value} (got {n})"


def check_one_of(result, task, path, values):
    got = _dig(result, path)
    return got in values, f"{path} in {values} (got {got!r})"


def check_less_than(result, task, path, value):
    got = _dig(result, path)
    return isinstance(got, (int, float)) and got < value, f"{path} < {value} (got {got!r})"


def check_refused(result, task, text=""):
    return bool(result.get("refused")) and text in str(result.get("error", "")), f"refused with {text!r} (got {str(result.get('error'))[:140]!r})"


def check_no_len_finding(result, task):
    bad = [f for f in result.get("findings", []) if f["rule"] == "LEN"]
    return not bad, f"no LEN finding (got {len(bad)})"


def check_not_blocked(result, task):
    return result.get("blocked") is False, f"not blocked (got {result.get('blocked')!r})"


def check_mock_attr(result, task, value):
    if result.get("mock_status") == "skipped":
        return True, "mock skipped (lupa not installed)"
    return result.get("mock_status") == "ok" and result.get("attribute") == value, f"mock attribute == {value!r} (got {result.get('attribute')!r})"


CHECKS = {"contains": check_contains, "excludes": check_excludes, "count": check_count, "one_of": check_one_of, "less_than": check_less_than,
          "refused": check_refused, "no_len_finding": check_no_len_finding, "not_blocked": check_not_blocked, "mock_attr": check_mock_attr}
