"""Wires the VFX domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import tempfile
from pathlib import Path

from . import tools as tools_mod
from .core import feedback as fb
from .core import retrieval
from .core.cli import DomainHooks, emit
from .core.manifest import LibraryStore
from .core.project import Project
from .core.rubric import load_rubric
from .core.style import load_style
from .domain import analysis, api, effects, luau, preview, studio

INSTRUCTIONS = """\
Roblox Studio VFX assistant. Workflow: get_style_brief -> find_past_corrections -> search_effect_library ->
list_effect_recipes -> create_effect_from_recipe(dry_run=true) -> check_performance_budget + evaluate_effect ->
simulate_effect (mock, optional) -> create_effect_from_recipe(dry_run=false) -> forward the returned Luau to Roblox
Studio's own MCP server (execute_luau, Edit datamodel) -> parse_studio_report -> preview_effect -> screen_capture
at each returned camera -> show the user -> record_run / record_decision with their actual words.
This server does not talk to Studio and never publishes or overwrites live content; generated code replaces only the
root it created itself. Never invent Roblox class or property names: the validator rejects anything not in the API
snapshot. Budgets and readability numbers are heuristic estimates, not measurements: say so."""

EXTENSIONS = {"effect": [".json"], "reference_image": [".png", ".jpg", ".jpeg", ".webp"],
              "reference_clip": [".mp4", ".webm", ".gif", ".mov"]}


def correction_dimensions(project: Project) -> list[str]:
    return load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    snap = api.load()
    return {
        "api_snapshot": {"generated_at": snap["generated_at"], "classes": sorted(snap["classes"]),
                         "source": snap["source"]},
        "roblox_studio": "not detected from here: this server never connects to Studio; enable Studio's own MCP server "
                         "(Assistant > Manage MCP Servers) and connect your agent to it as well",
        "luau_simulation": "available" if importlib.util.find_spec("lupa") else "unavailable (pip install '.[simulate]')",
        "recipes": [r["id"] for r in effects.list_recipes(project.root / "recipes")],
        "works_without_studio": ["search", "recipe validation", "Luau generation + lint", "mock simulation",
                                 "budget/readability estimates", "timeline previews", "feedback", "evals", "MCP server"],
        "needs_studio": ["running the generated Luau", "inspecting a live effect", "viewport screenshots", "playtesting"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("RBXVFX_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["RBXVFX_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("RBXVFX_WORKSPACE", None)
            else:
                os.environ["RBXVFX_WORKSPACE"] = old


def apply_mutations(recipe: dict, mutations: list[dict]) -> dict:
    """Return a modified deep copy of a recipe. Used by evals to make deliberately broken variants."""
    r = copy.deepcopy(recipe)
    inst = {i["id"]: i for i in r["instances"]}
    for m in mutations:
        op = m["op"]
        if op == "set_property":
            inst[m["instance"]].setdefault("properties", {})[m["property"]] = m["value"]
        elif op == "remove_property":
            inst[m["instance"]]["properties"].pop(m["property"], None)
        elif op == "set_field":
            inst[m["instance"]][m["field"]] = m["value"]
        elif op == "add_instance":
            r["instances"].append(m["instance"])
            inst[m["instance"]["id"]] = m["instance"]
        elif op == "remove_instance":
            r["instances"] = [i for i in r["instances"] if i["id"] != m["instance"]]
            r.get("emit", {}).pop(m["instance"], None)
        elif op == "set_emit":
            r.setdefault("emit", {})[m["instance"]] = m["value"]
        elif op == "set_top":
            r[m["field"]] = m["value"]
        else:
            raise ValueError(f"unknown mutation op '{op}'")
    return r


def eval_solver(project: Project):
    style = load_style(project)
    store = LibraryStore(project)
    snap = api.load()

    def recipe(rid: str) -> dict:
        return tools_mod._recipe(project, rid)

    def build(inp: dict):
        r = recipe(inp["recipe_id"])
        if inp.get("mutations"):
            r = apply_mutations(r, inp["mutations"])
        return effects.build_plan(r, inp.get("params"), name=inp.get("name"))

    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "search":
            filters = {"kind": "effect"}
            if inp.get("effect_kind"):
                filters["domain.effect_kind"] = inp["effect_kind"]
            hits = retrieval.search(store, inp.get("query", ""), filters=filters, tags=inp.get("tags", ()),
                                    palette=inp.get("palette", ()), k=inp.get("k", 3))
            return {"retrieved": [h["id"] for h in hits["positive"]], "avoid": [h["id"] for h in hits["negative"]]}
        if op == "build":
            try:
                plan = build(inp)
            except ValueError as exc:
                return {"error": str(exc), "built": False}
            return {"error": None, "built": True, "instances": [i["id"] for i in plan["instances"]], "emit": plan["emit"],
                    "warnings": plan["warnings"], "plan_hash": effects.plan_hash(plan), "kind": plan["kind"]}
        if op == "analyze":
            plan = build(inp)
            res = analysis.analyze(plan, style, inp.get("platform", "mobile"), inp.get("distance"))
            return {"metrics": res["metrics"], "flagged": sorted({f["metric"] for f in res["findings"]}),
                    "assumptions": res["assumptions"]}
        if op == "luau":
            plan = build(inp)
            code = luau.build_create_script(plan, inp.get("target_path", "workspace"), (0, 5, 0), emit_now=True)
            again = luau.build_create_script(build(inp), inp.get("target_path", "workspace"), (0, 5, 0), emit_now=True)
            return {"lint": luau.lint(code), "deterministic": code == again, "has_guard": 'GetAttribute("AIGeneratedBy")' in code,
                    "lines": code.count("\n")}
        if op == "lint_text":
            return {"lint": luau.lint(inp["code"])}
        if op == "bad_target":
            plan = build(inp)
            try:
                luau.build_create_script(plan, inp["target_path"], (0, 5, 0))
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "simulate":
            from .domain.mock_roblox import simulate

            plan = build(inp)
            code = luau.build_create_script(plan, inp.get("target_path", "workspace"), (0, 5, 0), emit_now=True)
            folder = inp.get("target_path", "workspace").split(".", 1)[1] if "." in inp.get("target_path", "workspace") else None
            if inp.get("no_target"):
                folder = None
            if inp.get("rerun"):
                from .domain.mock_roblox import MockRoblox

                mock = MockRoblox(snap)
                if folder:
                    mock.make_folder(folder)
                mock.run(code)
                second = json.loads(mock.run(code))
                return {"ok": True, "status": second["status"], "created": [c["name"] for c in second["created"]],
                        "emits": mock.emits}
            res = simulate(code, snap, preexisting_foreign=inp.get("foreign_name"), target=folder)
            report = res.get("report") or {}
            return {"ok": res["ok"], "error": res.get("error", ""), "status": report.get("status"),
                    "created": [c["name"] for c in report.get("created", [])], "emits": res.get("emits"),
                    "tags": res.get("tags")}
        if op == "roundtrip":
            from .domain.mock_roblox import MockRoblox

            plan = build(inp)
            mock = MockRoblox(snap)
            report = json.loads(mock.run(luau.build_create_script(plan, "workspace", (0, 5, 0), emit_now=False)))
            seen = json.loads(mock.run(luau.build_inspect_script(report["root"])))
            back = luau.parse_inspection(seen)
            findings = effects.validate_plan(back)
            return {"errors": [f for f in findings if f["severity"] == "error"],
                    "instances": sorted(i["id"] for i in back["instances"])}
        if op == "feedback":
            with _temp_workspace():
                rid = fb.record_run(project, request=inp["request"], retrieved=[])
                fb.record_decision(project, rid, "revise", reason=inp["reason"], corrections=inp["corrections"],
                                   allowed_dimensions=correction_dimensions(project))
                found = fb.corrections_for(project, inp["later_request"])
            return {"found_run": bool(found and found[0]["run_id"] == rid)}
        if op == "timeline":
            plan = build(inp)
            with tempfile.TemporaryDirectory() as d:
                path = preview.render_timeline(plan, Path(d) / "t.png")
                from PIL import Image

                w, h = Image.open(path).size
            return {"width": w, "height": h}
        if op == "report_parse":
            try:
                rep = studio.parse_report(inp["text"])
                return {"error": None, "status": rep.get("status")}
            except ValueError as exc:
                return {"error": str(exc)}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project: Project) -> dict:
    from .core.evals import dig

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:140]!r})"

    def flagged_include(result, task, metrics):
        got = set(result.get("flagged", []))
        return set(metrics) <= got, f"flagged {sorted(got)}; expected to include {metrics}"

    def flagged_exclude(result, task, metrics):
        got = set(result.get("flagged", []))
        return not (set(metrics) & got), f"flagged {sorted(got)}; must not include {metrics}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_not_empty(result, task, path):
        got = dig(result, path) or []
        return bool(got), f"{path} should not be empty (got {got})"

    def lint_clean(result, task):
        return not result.get("lint"), f"lint problems: {result.get('lint')}"

    def lint_mentions(result, task, text):
        return any(text in p for p in result.get("lint", [])), f"lint {result.get('lint')} should mention '{text}'"

    return {"error_contains": error_contains, "flagged_include": flagged_include, "flagged_exclude": flagged_exclude,
            "list_contains": list_contains, "list_not_empty": list_not_empty, "lint_clean": lint_clean, "lint_mentions": lint_mentions}


def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("recipes", help="list effect recipes")
    p.set_defaults(handler=lambda a, pr: emit(effects.list_recipes(pr.root / "recipes")))

    p = sub.add_parser("build", help="build an effect plan from a recipe and print the Luau")
    p.add_argument("recipe")
    p.add_argument("--name")
    p.add_argument("--param", action="append", default=[], help="name=value (JSON values)")
    p.add_argument("--target", default="workspace")
    p.add_argument("--emit-now", action="store_true")
    p.set_defaults(handler=_build)

    p = sub.add_parser("analyze", help="budget + readability estimates for a recipe")
    p.add_argument("recipe")
    p.add_argument("--platform", default="mobile")
    p.add_argument("--param", action="append", default=[])
    p.set_defaults(handler=_analyze)

    p = sub.add_parser("snapshot-info", help="show which Roblox API snapshot the validator trusts")
    p.set_defaults(handler=lambda a, pr: emit({k: v for k, v in api.load().items() if k in ("generated_at", "source")}
                                              | {"classes": {c: len(v["properties"]) for c, v in api.load()["classes"].items()}}))


def _params(items: list[str]) -> dict:
    out = {}
    for item in items:
        k, _, v = item.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _build(a, pr) -> int:
    plan = effects.build_plan(tools_mod._recipe(pr, a.recipe), _params(a.param), name=a.name)
    print(luau.build_create_script(plan, a.target, (0, 5, 0), emit_now=a.emit_now))
    return 0


def _analyze(a, pr) -> int:
    plan = effects.build_plan(tools_mod._recipe(pr, a.recipe), _params(a.param))
    emit(analysis.analyze(plan, load_style(pr), a.platform))
    return 0


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
