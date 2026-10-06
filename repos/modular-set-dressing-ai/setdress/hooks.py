"""Wires the set-dressing domain into the shared CLI, MCP server and eval runner."""

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
from .core.style import check_ranges, load_style
from .domain import api, compose, dress, export, geom, kit as K, mapview, scene as SC

INSTRUCTIONS = """\
Modular set-dressing assistant. Workflow: get_style_brief -> find_past_corrections -> search_scene_library -> kit_report + search_kit
(ALWAYS search the approved kit before proposing a replacement asset) -> inspect_scene -> build in this order: locked/hero pieces, socket-snapped
groups (snap_to_socket), then dress_region with rules and a seed -> every change is a plan: review it (dry_run=true), then apply (dry_run=false,
saves a new version; undo_last_change reverts) -> validate_composition (fix errors, explain warnings) -> render_scene_map -> export_scene
(luau) -> simulate_scene -> forward the Luau to Roblox Studio's own MCP server (execute_luau, Edit datamodel) -> parse_studio_report ->
inspect_placed -> look at it in Studio -> record_run / record_decision with the user's actual words.
This server never talks to Studio and never publishes or overwrites live content. Locked instances must never move; functional paths and
eye-height sightlines must stay clear. Report unresolved kit metadata (kit_report) instead of guessing. Subjective rubric criteria stay unscored
until a person has looked at the scene."""

EXTENSIONS = {"module": [".json", ".yaml"], "scene": [".json"], "reference_image": [".png", ".jpg", ".jpeg", ".webp"]}


def correction_dimensions(project: Project) -> list[str]:
    return load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    kit, findings = tools_mod.load_kit(project)
    rep = K.report(kit)
    return {
        "kit": {"path": str(tools_mod.kit_path(project)), "modules": rep["modules"], "findings": [f for f in findings if f["severity"] != "info"],
                "unresolved_metadata": rep["unresolved"], "socket_types": rep["socket_types"]},
        "api_snapshot": {"generated_at": api.load()["generated_at"], "classes": sorted(api.load()["classes"])},
        "roblox_studio": "this server never connects to Studio; enable Studio's own MCP server (Assistant > Manage MCP Servers) and connect your agent to it too",
        "luau_simulation": "available" if importlib.util.find_spec("lupa") else "unavailable (pip install '.[simulate]')",
        "works_without_studio": ["kit validation + search", "collision/clearance/sightline checks", "socket snapping", "seeded dressing plans", "composition metrics",
                                 "undo/versions/diffs/locks", "Luau + JSON export", "mock simulation", "plan maps", "feedback", "evals", "MCP server"],
        "needs_studio": ["running the Luau", "clone mode against your real kit templates", "reading placements back", "visual review in Studio"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("SETDRESS_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["SETDRESS_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("SETDRESS_WORKSPACE", None)
            else:
                os.environ["SETDRESS_WORKSPACE"] = old


def mutate_scene(scene: dict, mutations: list[dict]) -> dict:
    s = copy.deepcopy(scene)
    for m in mutations or []:
        op = m["op"]
        if op == "add":
            s["instances"].append({"y": s["region"].get("floor_y", 0.0), "yaw": 0.0, "scale": 1.0, "locked": False, "why": "", **m["value"]})
        elif op == "remove":
            s["instances"] = [i for i in s["instances"] if i["id"] != m["id"]]
        elif op == "set":
            next(i for i in s["instances"] if i["id"] == m["id"])[m["field"]] = m["value"]
        elif op == "set_top":
            s[m["field"]] = m["value"]
        else:
            raise ValueError(f"unknown scene mutation '{op}'")
    return s


def mutate_kit(kit: dict, mutations: list[dict]) -> dict:
    k = copy.deepcopy(kit)
    for m in mutations or []:
        mod = next((x for x in k["modules"] if x["id"] == m.get("module")), None)
        if m["op"] == "set":
            mod[m["field"]] = m["value"]
        elif m["op"] == "unset":
            mod.pop(m["field"], None)
        elif m["op"] == "add":
            k["modules"].append(m["value"])
        elif m["op"] == "remove":
            k["modules"] = [x for x in k["modules"] if x["id"] != m["module"]]
        else:
            raise ValueError(f"unknown kit mutation '{m['op']}'")
    return k


def eval_solver(project: Project):
    style = load_style(project)
    store = LibraryStore(project)
    raw_kit = K.load_kit(tools_mod.kit_path(project))
    kit, _ = K.normalize_kit(raw_kit)
    snap = api.load()

    def scene_of(inp: dict) -> dict:
        path = project.root / "examples" / "scenes" / f"{inp.get('scene', 'tavern_bare')}.json"
        return SC.normalize(mutate_scene(json.loads(path.read_text(encoding="utf-8")), inp.get("mutations", [])))

    def codes(findings, sev=("error", "warning")):
        return sorted({f["code"] for f in findings if f["severity"] in sev})

    def kit_of(inp: dict) -> dict:
        return K.normalize_kit(mutate_kit(raw_kit, inp["kit_mutations"]))[0] if inp.get("kit_mutations") else kit

    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "search_library":
            hits = retrieval.search(store, inp.get("query", ""), filters={"kind": "scene"}, tags=inp.get("tags", ()), k=inp.get("k", 3))
            return {"retrieved": [h["id"] for h in hits["positive"]], "avoid": [h["id"] for h in hits["negative"]]}
        if op == "kit_search":
            hits = K.search(kit, inp.get("query", ""), tags_all=inp.get("tags", ()), max_footprint=tuple(inp["max_footprint"]) if inp.get("max_footprint") else None,
                            socket_type=inp.get("socket_type"), size_class_=inp.get("size_class"), k=inp.get("k", 8))
            return {"ids": [h["id"] for h in hits]}
        if op == "kit_get":
            try:
                K.get(kit, inp["module"])
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "kit":
            k2, f = K.normalize_kit(mutate_kit(raw_kit, inp.get("mutations", [])))
            return {"codes": codes(f, ("error", "warning")), "errors": codes(f, ("error",)), "unresolved": sorted(K.report(k2)["unresolved"])}
        if op == "geom":
            kind = inp["kind"]
            if kind == "overlap":
                a = geom.footprint_corners(tuple(inp["a"]["at"]), inp["a"].get("yaw", 0), tuple(inp["a"]["size"]))
                b = geom.footprint_corners(tuple(inp["b"]["at"]), inp["b"].get("yaw", 0), tuple(inp["b"]["size"]))
                return {"depth": round(geom.overlap_depth(a, b), 4)}
            if kind == "to_world":
                x, z = geom.to_world(tuple(inp["local"]), tuple(inp["at"]), inp["yaw"], inp.get("scale", 1.0))
                return {"x": round(x, 4), "z": round(z, 4)}
            if kind == "forward":
                x, z = geom.forward(inp["yaw"])
                return {"x": round(x, 4), "z": round(z, 4)}
            if kind == "snap":
                return {"value": geom.snap(inp["value"], inp["step"]), "yaw": geom.snap_yaw(inp["yaw"], inp["yaw_step"])}
        if op == "snap_socket":
            sc = scene_of(inp)
            try:
                inst = dress.snap_to_socket(sc, kit, inp["target"], inp["target_socket"], inp["module"], inp["module_socket"], scale=inp.get("scale", 1.0))
            except ValueError as exc:
                return {"error": str(exc)}
            after = SC.apply_plan(sc, {"adds": [inst], "moves": [], "removes": []})
            f = [x for x in SC.validate(after, kit) if x["severity"] == "error"]
            return {"error": None, "at": [round(v, 3) for v in inst["at"]], "y": round(inst["y"], 3), "yaw": round(inst["yaw"], 3), "errors": codes(f, ("error",))}
        if op == "place":
            sc = scene_of(inp)
            try:
                inst = dress.make_instance(sc, kit, inp["module"], inp["at"], yaw=inp.get("yaw", 0), scale=inp.get("scale", 1.0), y=inp.get("y"), snap=inp.get("snap", True))
            except ValueError as exc:
                return {"error": str(exc), "codes": []}
            after = SC.apply_plan(sc, {"adds": [inst], "moves": [], "removes": []})
            f = [x for x in SC.validate_structure(after, kit) if x["where"] == inst["id"]]
            f += SC.placement_findings(after, kit, inst, after["instances"], spacing=inp.get("spacing", 0.0))
            return {"error": None, "codes": codes(f, ("error", "warning")), "at": inst["at"], "yaw": inst["yaw"]}
        if op == "validate":
            sc = scene_of(inp)
            f = SC.validate(sc, kit, spacing=inp.get("spacing", 0.0))
            return {"codes": codes(f), "errors": codes(f, ("error",))}
        if op == "dress":
            sc = scene_of(inp)
            a = dress.dress_region(sc, kit, inp.get("rules", {}), inp.get("seed", 0))
            b = dress.dress_region(sc, kit, inp.get("rules", {}), inp.get("seed", 0))
            other = dress.dress_region(sc, kit, inp.get("rules", {}), inp.get("seed", 0) + 1)
            after = SC.apply_plan(sc, a["plan"])
            f = SC.validate(after, kit)
            used = sorted({i["module"] for i in a["plan"]["adds"]})
            locks = SC.check_locks(after, SC.lock_snapshot(sc))
            return {"adds": len(a["plan"]["adds"]), "deterministic": a["plan"] == b["plan"], "seed_changes_result": a["plan"] != other["plan"],
                    "errors": codes(f, ("error",)), "used": used, "locks_ok": not locks, "coverage_after": a["coverage_after"],
                    "explained": len(a["explain"]) == len(a["plan"]["adds"]), "rejected": sorted(a["rejected"]),
                    "max_share": max([sum(1 for i in a["plan"]["adds"] if i["module"] == m) / max(len(a["plan"]["adds"]), 1) for m in used] or [0])}
        if op == "dress_error":
            try:
                dress.dress_region(scene_of(inp), kit, inp.get("rules", {}), inp.get("seed", 0))
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "measure":
            sc = scene_of(inp)
            res = compose.measure(sc, kit)
            flagged = sorted({f["metric"] for f in check_ranges(res["metrics"], style["ranges"]) if f["severity"] in ("warning", "error")})
            return {"flagged": flagged, "metrics": res["metrics"]}
        if op == "plan":
            sc = scene_of(inp)
            plan = inp["plan"]
            for k in ("adds", "moves", "removes"):
                plan.setdefault(k, [])
            plan["adds"] = [dress.make_instance(sc, kit, a["module"], a["at"], yaw=a.get("yaw", 0), iid=a["id"]) for a in plan["adds"]]
            try:
                after = SC.apply_plan(sc, plan)
            except ValueError as exc:
                return {"error": str(exc)}
            back = SC.apply_plan(after, SC.invert_plan(sc, plan))
            return {"error": None, "added": SC.diff(sc, after)["added"], "round_trip": SC.diff(sc, back)["unchanged"], "hash_restored": SC.scene_hash(sc) == SC.scene_hash(back)}
        if op == "locks":
            sc = scene_of({**inp, "mutations": []})
            snap_ = SC.lock_snapshot(sc)
            changed = scene_of(inp)
            diffs = SC.check_locks(changed, snap_)
            return {"locked": sorted(snap_), "codes": sorted({d["code"] for d in diffs}), "ok": not diffs}
        if op == "export":
            sc = scene_of(inp)
            kit_ = kit_of(inp)
            try:
                code = export.build_luau(sc, kit_, target_path=inp.get("target_path", "workspace"), mode=inp.get("mode", "primitives"), max_items=inp.get("max_items", 600))
            except ValueError as exc:
                return {"error": str(exc), "lint": []}
            return {"error": None, "lint": export.lint(code), "deterministic": code == export.build_luau(sc, kit_, target_path=inp.get("target_path", "workspace"), mode=inp.get("mode", "primitives")),
                    "has_guard": 'GetAttribute("AIGeneratedBy")' in code, "lines": code.count("\n")}
        if op == "lint_text":
            return {"lint": export.lint(inp["code"])}
        if op == "json_export":
            data = export.to_json(scene_of(inp), kit)
            return {"count": len(data["instances"]), "format": data["format"], "first": data["instances"][0]["id"]}
        if op == "simulate":
            from .domain.mock_roblox import MockRoblox

            sc = scene_of(inp)
            kit_ = kit_of(inp)
            mode = inp.get("mode", "primitives")
            mock = MockRoblox(snap)
            code = export.build_luau(sc, kit_, mode=mode)
            if mode == "clone":
                stub = mock.lua.eval("function(names) local f = Instance.new('Folder'); f.Name = 'Kit'; local rs = Instance.new('Folder'); rs.Name = 'ReplicatedStorage'; rs.Parent = workspace; f.Parent = rs; for _, n in pairs(names) do local m = Instance.new('Model'); m.Name = n; m.Parent = f end end")
                stub(mock.lua.table_from(sorted({m["template"] for m in kit["modules"] if m.get("template") and inp.get("missing_template") != m["template"]})))
                code = code.replace('resolveTarget("ReplicatedStorage.Kit")', 'resolveTarget("workspace.ReplicatedStorage.Kit")')
            try:
                report = json.loads(mock.run(code))
            except Exception as exc:
                return {"ok": False, "error": str(exc).splitlines()[0]}
            root = next(c for c in mock.tree()["children"] if c["name"].startswith("AI_SetDress_"))
            return {"ok": True, "status": report["status"], "instances": report["instances"], "children": len(root["children"]), "expected": len(sc["instances"]),
                    "pivots": sum(1 for c in root["children"] if c.get("pivot"))}
        if op == "inspect":
            from .domain.mock_roblox import MockRoblox

            sc = scene_of(inp)
            mock = MockRoblox(snap)
            mock.run(export.build_luau(sc, kit))
            if inp.get("tamper"):
                mock.run(inp["tamper"])
            cmp = export.compare_built(sc, kit, json.loads(mock.run(export.build_inspect_luau(sc["id"]))))
            return {"in_sync": cmp["in_sync"], "missing": cmp["missing"], "extra": cmp["extra"], "moved": [m["id"] for m in cmp["moved"]], "found": cmp["found"]}
        if op == "map":
            sc = scene_of(inp)
            with tempfile.TemporaryDirectory() as d:
                from PIL import Image

                w, h = Image.open(mapview.render(sc, kit, Path(d) / "m.png")).size
            return {"width": w, "height": h}
        if op == "feedback":
            with _temp_workspace():
                rid = fb.record_run(project, request=inp["request"], retrieved=[])
                fb.record_decision(project, rid, "revise", reason=inp["reason"], corrections=inp["corrections"], allowed_dimensions=correction_dimensions(project))
                found = fb.corrections_for(project, inp["later_request"])
            return {"found_run": bool(found and found[0]["run_id"] == rid)}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project: Project) -> dict:
    from .core.evals import dig

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:160]!r})"

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

    def approx(result, task, path, value, tol=0.01):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    return {"error_contains": error_contains, "list_contains": list_contains, "list_excludes": list_excludes, "lint_clean": lint_clean, "lint_mentions": lint_mentions,
            "approx": approx}


def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("kit", help="kit report (validation, unresolved metadata, socket types)")
    p.set_defaults(handler=lambda a, pr: emit({"findings": tools_mod.load_kit(pr)[1], **K.report(tools_mod.load_kit(pr)[0])}))

    p = sub.add_parser("validate", help="validate a scene file against the kit")
    p.add_argument("scene")
    p.set_defaults(handler=_validate)

    p = sub.add_parser("measure", help="composition metrics for a scene file")
    p.add_argument("scene")
    p.set_defaults(handler=_measure)

    p = sub.add_parser("dress", help="print a dressing plan for a scene file (never writes)")
    p.add_argument("scene")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--rules", default="{}", help="JSON rules")
    p.set_defaults(handler=_dress)

    p = sub.add_parser("map", help="write a plan-view PNG for a scene file")
    p.add_argument("scene")
    p.add_argument("--out")
    p.set_defaults(handler=_map)

    p = sub.add_parser("export", help="print Luau for a scene file")
    p.add_argument("scene")
    p.add_argument("--mode", default="primitives", choices=["primitives", "clone"])
    p.add_argument("--target", default="workspace")
    p.set_defaults(handler=_export)


def _load(pr: Project, path: str) -> tuple[dict, dict]:
    kit, _ = tools_mod.load_kit(pr)
    return SC.normalize(json.loads(Path(path).read_text(encoding="utf-8"))), kit


def _validate(a, pr) -> int:
    sc, kit = _load(pr, a.scene)
    f = SC.validate(sc, kit)
    emit({"errors": sum(x["severity"] == "error" for x in f), "findings": f})
    return 1 if any(x["severity"] == "error" for x in f) else 0


def _measure(a, pr) -> int:
    sc, kit = _load(pr, a.scene)
    res = compose.measure(sc, kit)
    emit({"metrics": res["metrics"], "flagged": check_ranges(res["metrics"], load_style(pr)["ranges"]), "module_counts": res["module_counts"]})
    return 0


def _dress(a, pr) -> int:
    sc, kit = _load(pr, a.scene)
    res = dress.dress_region(sc, kit, json.loads(a.rules), a.seed)
    emit({"adds": len(res["plan"]["adds"]), "coverage_after": res["coverage_after"], "rejected": res["rejected"], "explain": res["explain"][:12], "plan": res["plan"]})
    return 0


def _map(a, pr) -> int:
    sc, kit = _load(pr, a.scene)
    print(mapview.render(sc, kit, a.out or str(pr.output_dir / "maps" / f"{sc['id']}.png")))
    return 0


def _export(a, pr) -> int:
    sc, kit = _load(pr, a.scene)
    print(export.build_luau(sc, kit, target_path=a.target, mode=a.mode))
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
