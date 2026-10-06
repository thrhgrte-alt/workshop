"""Wires the level-design domain into the shared CLI, MCP server and eval runner."""

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
from .core.style import load_style
from .domain import api, build, evaluate, graphs, mapview, sight, spec as S, walkcheck

INSTRUCTIONS = """\
Roblox level-design assistant. Workflow: get_style_brief -> find_past_corrections -> search_level_library ->
list_level_templates -> create_level_spec (template + parameters, or a spec you write; dry_run first, then false to save
revision 1 as the baseline for locks) -> evaluate_level (fix every error; explain warnings) -> capture_review_views ->
build_blockout(dry_run=true, then false) -> simulate_blockout -> forward the Luau to Roblox Studio's own MCP server
(execute_luau, Edit datamodel) -> parse_studio_report -> inspect_blockout -> screen_capture at the player-height views
-> show the user -> record_run / record_decision with their actual words.
This server never talks to Studio and never publishes or overwrites live content. Locked rooms, connections, bounds and
items must not change between revisions (check_locked_constraints). Judge a layout from player height along its routes,
not from the plan view alone. Report every assumption and open question. Subjective rubric criteria stay unscored until a
person has walked the level."""

EXTENSIONS = {"level": [".json", ".yaml", ".yml"], "reference_image": [".png", ".jpg", ".jpeg", ".webp"],
              "reference_map": [".png", ".jpg", ".jpeg", ".pdf"]}


def correction_dimensions(project: Project) -> list[str]:
    return load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    return {
        "api_snapshot": {"generated_at": api.load()["generated_at"], "classes": sorted(api.load()["classes"])},
        "roblox_studio": "this server never connects to Studio; enable Studio's own MCP server (Assistant > Manage MCP Servers) "
                         "and connect your agent to it as well",
        "luau_simulation": "available" if importlib.util.find_spec("lupa") else "unavailable (pip install '.[simulate]')",
        "templates": [t["id"] for t in S.list_templates(project.root / "recipes")],
        "works_without_studio": ["spec validation", "route/loop/choke-point analysis", "sightline + landmark analysis", "pacing + fairness",
                                 "blockout generation", "walkability of generated geometry", "mock simulation", "plan maps", "review cameras",
                                 "locks + revisions + diffs", "feedback", "evals", "MCP server"],
        "needs_studio": ["running the Luau", "reading back the built blockout", "viewport captures at player height", "playtesting"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("RBXLEVEL_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["RBXLEVEL_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("RBXLEVEL_WORKSPACE", None)
            else:
                os.environ["RBXLEVEL_WORKSPACE"] = old


def apply_mutations(spec: dict, mutations: list[dict]) -> dict:
    """Deep-copied spec with deliberate defects applied (used by evals and tests)."""
    s = copy.deepcopy(spec)
    for m in mutations or []:
        op = m["op"]
        coll = {"room": "rooms", "connection": "connections", "spawn": "spawns", "objective": "objectives",
                "landmark": "landmarks", "encounter": "encounters"}.get(m.get("target", ""), None)
        if op == "set":  # {op: set, target: room, id: x, field: rect, value: [...]}
            next(i for i in s[coll] if i["id"] == m["id"])[m["field"]] = m["value"]
        elif op == "unset":
            next(i for i in s[coll] if i["id"] == m["id"]).pop(m["field"], None)
        elif op == "remove":
            s[coll] = [i for i in s[coll] if i["id"] != m["id"]]
        elif op == "add":
            s[coll].append(m["value"])
        elif op == "set_top":
            s[m["field"]] = m["value"]
        elif op == "set_player":
            s.setdefault("player", {})[m["field"]] = m["value"]
        else:
            raise ValueError(f"unknown mutation op '{op}'")
    return s


def eval_solver(project: Project):
    style = load_style(project)
    store = LibraryStore(project)
    rubric = project.root / "evals" / "rubric.yaml"
    snap = api.load()

    def raw(inp: dict) -> dict:
        t = tools_mod._template(project, inp["template"])
        base = S.instantiate(t, inp.get("params"))
        return apply_mutations(base, inp.get("mutations", []))

    def prep(inp: dict):
        norm, f = S.prepare(raw(inp), style)
        f += build.check_ids(norm)
        return norm, f

    def codes(findings, severities=("error", "warning")):
        return sorted({f["code"] for f in findings if f["severity"] in severities})

    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "search":
            filters = {"kind": "level"}
            if inp.get("level_type"):
                filters["domain.level_type"] = inp["level_type"]
            hits = retrieval.search(store, inp.get("query", ""), filters=filters, tags=inp.get("tags", ()), k=inp.get("k", 3))
            return {"retrieved": [h["id"] for h in hits["positive"]], "avoid": [h["id"] for h in hits["negative"]]}
        if op == "spec":
            norm, f = prep(inp)
            return {"codes": codes(f), "errors": codes(f, ("error",)), "info_codes": codes(f, ("info",)), "valid": not S.has_errors(f), "rooms": len(norm["rooms"]),
                    "findings": f}
        if op == "template_params":
            try:
                S.instantiate(tools_mod._template(project, inp["template"]), inp.get("params"))
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "expr":
            try:
                return {"value": S.eval_expr(inp["expr"], inp.get("names", {})), "error": None}
            except ValueError as exc:
                return {"value": None, "error": str(exc)}
        if op == "evaluate":
            res = evaluate.evaluate(raw(inp), style, rubric, manual=inp.get("manual"))
            failed = [c["id"] for c in res["rubric"]["criteria"] if c["passed"] is False]
            return {"codes": codes(res["findings"]), "failed_criteria": failed, "metrics": res["metrics"],
                    "complete": res["rubric"]["complete"], "passed": res["rubric"]["passed"], "unscored": res["rubric"]["unscored"]}
        if op == "analysis":
            norm, f = prep(inp)
            g, p, s = graphs.analyze(norm, style), graphs.pacing(norm, style), sight.analyze(norm, style)
            return {"graph": g["metrics"], "pacing": p["metrics"], "sight": s["metrics"], "unreachable": g["unreachable"], "dead_ends": g["dead_ends"],
                    "codes": codes(g["findings"] + s["findings"] + p["findings"]), "routes": [{"spawn": r["spawn"], "length": r["length"], "chokepoints": r["chokepoints"],
                                                                                            "independent": r["independent_routes"]} for r in g["routes"]]}
        if op == "build":
            norm, f = prep(inp)
            st = copy.deepcopy(style)
            for k, v in inp.get("build_style", {}).items():
                st["build"][k] = v
            parts = build.build_parts(norm, st)
            walk = walkcheck.check(norm, parts)
            return {"parts": len(parts), "walkable": walk["ok"], "unreachable_rooms": walk["unreachable_rooms"], "codes": codes(walk["findings"], ("error", "warning"))}
        if op == "sabotage":  # a builder bug: wall off a door in the built geometry
            norm, f = prep(inp)
            parts = build.build_parts(norm, style)
            c = next(c for c in norm["connections"] if c["id"] == inp["connection"])
            px, pz = (c["line"], c["at"]) if c["axis"] == "z" else (c["at"], c["line"])
            parts.append(build.part("Room_x", "wall_sabotage", (c["width"] + 2, 12, c["width"] + 2), (px, 6, pz), (1, 0, 0)))
            walk = walkcheck.check(norm, parts)
            return {"walkable": walk["ok"], "codes": codes(walk["findings"])}
        if op == "luau":
            norm, f = prep(inp)
            parts = build.build_parts(norm, style)
            code = build.build_luau(norm, parts, inp.get("target_path", "workspace"), style)
            return {"lint": build.lint(code), "deterministic": code == build.build_luau(norm, build.build_parts(norm, style), inp.get("target_path", "workspace"), style),
                    "parts": len(parts), "has_guard": 'GetAttribute("AIGeneratedBy")' in code}
        if op == "lint_text":
            return {"lint": build.lint(inp["code"])}
        if op == "bad_target":
            norm, f = prep(inp)
            try:
                build.build_luau(norm, build.build_parts(norm, style), inp["target_path"], style)
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "simulate":
            from .domain.mock_roblox import MockRoblox, simulate

            norm, f = prep(inp)
            code = build.build_luau(norm, build.build_parts(norm, style), inp.get("target_path", "workspace"), style)
            folder = inp.get("target_path", "workspace").split(".", 1)[1] if "." in inp.get("target_path", "workspace") else None
            if inp.get("no_target"):
                folder = None
            if inp.get("rerun"):
                mock = MockRoblox(snap)
                if folder:
                    mock.make_folder(folder)
                mock.run(code)
                again = json.loads(mock.run(code))
                tree = mock.tree()
                roots = [c for c in _walk(tree) if c["name"].startswith("AI_Blockout_")]
                return {"ok": True, "status": again["status"], "roots": len(roots), "parts": again["parts"]}
            res = simulate(code, snap, foreign=inp.get("foreign_name"), target=folder)
            report = res.get("report") or {}
            return {"ok": res["ok"], "error": res.get("error", ""), "status": report.get("status"), "parts": report.get("parts"),
                    "expected_parts": len(build.build_parts(norm, style))}
        if op == "inspect":
            from .domain.mock_roblox import MockRoblox

            norm, f = prep(inp)
            parts = build.build_parts(norm, style)
            mock = MockRoblox(snap)
            mock.run(build.build_luau(norm, parts, "workspace", style))
            if inp.get("tamper"):
                mock.run(inp["tamper"])
            report = json.loads(mock.run(build.build_inspect_luau(norm["id"], "workspace")))
            cmp = build.compare_built(parts, report)
            return {"in_sync": cmp["in_sync"], "missing": cmp["missing"], "extra": cmp["extra"], "moved": [m["part"] for m in cmp["moved"]],
                    "found": cmp["found"], "expected": cmp["expected"]}
        if op == "locks":
            base_raw = apply_mutations(S.instantiate(tools_mod._template(project, inp["template"]), inp.get("params")),
                                       [{"op": "set_top", "field": "locked", "value": inp["lock"]}])
            base, _ = S.prepare(base_raw, style)
            snapshot = build.lock_snapshot(base)
            changed, _ = S.prepare(apply_mutations(base_raw, inp.get("mutations", [])), style)
            diffs = build.check_locks(changed, snapshot)
            return {"locked": {k: len(v) for k, v in snapshot.items() if isinstance(v, dict)}, "differences": [d["message"] for d in diffs],
                    "ok": not diffs, "codes": sorted({d["code"] for d in diffs})}
        if op == "diff":
            base, _ = prep({**inp, "mutations": []})
            new, _ = prep(inp)
            d = build.diff_specs(base, new)
            return {"unchanged": d["unchanged"], "rooms_changed": [c["id"] for c in d["rooms"]["changed"]], "rooms_added": d["rooms"]["added"],
                    "rooms_removed": d["rooms"]["removed"], "connections_removed": d["connections"]["removed"], "bounds_changed": d["bounds_changed"]}
        if op == "views":
            norm, f = prep(inp)
            views = sight.review_views(norm, inp.get("step", 24.0), inp.get("max_views", 24))
            eye = norm["player"]["eye"]
            route = [v for v in views if v["kind"] == "route"]
            return {"count": len(views), "kinds": sorted({v["kind"] for v in views}), "route_eye_heights_ok": all(abs(v["position"][1] - (eye + 0)) < 1e-6 or v["position"][1] > eye for v in route),
                    "has_overview": any(v["kind"] == "overview" for v in views), "first": views[0]["name"]}
        if op == "map":
            norm, f = prep(inp)
            with tempfile.TemporaryDirectory() as d:
                path = mapview.render(norm, Path(d) / "m.png")
                from PIL import Image

                w, h = Image.open(path).size
            return {"width": w, "height": h}
        if op == "feedback":
            with _temp_workspace():
                rid = fb.record_run(project, request=inp["request"], retrieved=[])
                fb.record_decision(project, rid, "revise", reason=inp["reason"], corrections=inp["corrections"], allowed_dimensions=correction_dimensions(project))
                found = fb.corrections_for(project, inp["later_request"])
            return {"found_run": bool(found and found[0]["run_id"] == rid)}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def _walk(tree):
    yield tree
    for c in tree.get("children", []):
        yield from _walk(c)


def eval_checks(project: Project) -> dict:
    from .core.evals import dig

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:140]!r})"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def lint_clean(result, task):
        return not result.get("lint"), f"lint problems: {result.get('lint')}"

    def lint_mentions(result, task, text):
        return any(text in p for p in result.get("lint", [])), f"lint {result.get('lint')} should mention '{text}'"

    def codes_empty(result, task, path="codes"):
        got = dig(result, path) or []
        return not got, f"{path} should be empty (got {got})"

    return {"error_contains": error_contains, "list_contains": list_contains, "list_excludes": list_excludes, "lint_clean": lint_clean,
            "lint_mentions": lint_mentions, "codes_empty": codes_empty}


def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("templates", help="list level templates")
    p.set_defaults(handler=lambda a, pr: emit(S.list_templates(pr.root / "recipes")))

    p = sub.add_parser("evaluate", help="evaluate a template (or a spec file) and print findings, metrics and the rubric")
    p.add_argument("source", help="template id or path to a .json/.yaml spec")
    p.add_argument("--param", action="append", default=[])
    p.set_defaults(handler=_evaluate)

    p = sub.add_parser("map", help="write a plan-view PNG for a template or spec file")
    p.add_argument("source")
    p.add_argument("--out", default=None)
    p.add_argument("--param", action="append", default=[])
    p.set_defaults(handler=_map)

    p = sub.add_parser("build", help="print the blockout Luau for a template or spec file")
    p.add_argument("source")
    p.add_argument("--target", default="workspace")
    p.add_argument("--param", action="append", default=[])
    p.set_defaults(handler=_build)

    p = sub.add_parser("snapshot-info", help="show which Roblox API snapshot the generator trusts")
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


def _load_source(pr: Project, source: str, param_items: list[str]) -> dict:
    path = Path(source)
    if path.suffix.lower() in (".json", ".yaml", ".yml") and path.exists():
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return S.instantiate(tools_mod._template(pr, source), _params(param_items))


def _evaluate(a, pr) -> int:
    style = load_style(pr)
    res = evaluate.evaluate(_load_source(pr, a.source, a.param), style, pr.root / "evals" / "rubric.yaml")
    spec = res.pop("spec")
    for k in ("sight", "routes", "geometry"):
        res.pop(k, None)
    res["assumptions"] = evaluate.assumptions({**spec, "player_given": {}}, style)
    emit(res)
    return 0 if not any(f["severity"] == "error" for f in res["findings"]) else 1


def _map(a, pr) -> int:
    style = load_style(pr)
    norm, f = S.prepare(_load_source(pr, a.source, a.param), style)
    if S.has_errors(f):
        raise ValueError("spec is invalid: " + "; ".join(x["message"] for x in f if x["severity"] == "error"))
    out = a.out or str(pr.output_dir / "maps" / f"{norm['id']}.png")
    print(mapview.render(norm, out))
    return 0


def _build(a, pr) -> int:
    style = load_style(pr)
    norm, f = S.prepare(_load_source(pr, a.source, a.param), style)
    if S.has_errors(f):
        raise ValueError("spec is invalid: " + "; ".join(x["message"] for x in f if x["severity"] == "error"))
    print(build.build_luau(norm, build.build_parts(norm, style), a.target, style))
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
