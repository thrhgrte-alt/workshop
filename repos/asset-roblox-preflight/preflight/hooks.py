"""Wires the asset-preflight domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from . import learning_params
from . import tools as tools_mod
from . import projects as P
from .domain import bpystub, engine, fixes, imageinfo, loader
from .guide_adapter import DomainHooks, Project, all_tools, call_local, config, emit, evals as _evals, feedback as fb, skillgen

INSTRUCTIONS = """\
Blender-to-Roblox asset preflight. Checks an EXPORT (GLB, glTF, OBJ, an FBX header plus a hub summary) against a profile (prop, tool, character_accessory,
terrain_piece) and reports problems in plain terms. Every tool needs a project_id (list_projects; never guess one; place_id is optional and only recorded). Workflow: get_style_brief -> find_past_corrections -> list_profiles -> inspect_export (what is in the file, what cannot be
measured) -> preflight_report (the full check; or check_mesh / check_uvs / check_textures / check_rig / check_names for what the request names, nothing more) -> explain findings by
severity using the measured value, limit and one-line fix the tool returns (outputs are short: use explain_finding for details) -> apply_safe_fix (dry_run first; emits a Blender Python script for the hub to run on a COPY of the .blend,
never edits a file in place, only fixes marked safe: true) -> re-export and run preflight_report again -> compare_versions -> save_report -> record_run / record_decision, and
record_override when the user says a rule may be broken for an asset type (suggest_profile_promotions offers to make repeated overrides permanent).
Every numeric limit is a DEFAULT chosen by this tool, not an official Roblox limit: say so, and never promise an upload will succeed. ready_to_upload means no errors remain on these
defaults; hand such an asset to roblox_upload_plan through the hub. This server never connects to Blender, Studio or Roblox and never uploads or publishes anything. FBX geometry is NOT
parsed here: without a hub summary an FBX is reported as not measured, never as ready. Say what could not be checked."""

EXTENSIONS = {"export_case": [".glb", ".gltf", ".obj", ".fbx", ".json"], "good_export": [".glb", ".gltf", ".obj"], "bad_export": [".glb", ".gltf", ".obj"]}
ASSETS = "examples/assets"
DEFAULT_PROJECT = "synthetic-sandbox"


def correction_dimensions(project: Project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    cfg = engine.load_config(project.root)
    cov = engine.check_coverage(cfg)
    return {
        "projects": sorted(P.load_registry(project.root)),
        "formats": {"glb": "read (JSON + binary chunks, accessors, materials, images, skins, animations)", "gltf": "read (embedded or external buffers and images)", "obj": "read (+ MTL texture references)",
                    "fbx": "header only (flavour, version, size): geometry is NOT parsed; pass a hub summary", "json": "hub summary, schema preflight-summary/1"},
        "rules": len(cfg.rules), "profiles": sorted(cfg.profiles), "safe_rules": sorted(r for r, v in cfg.rules.items() if v["safe"]),
        "rule_code_coverage": cov, "pillow_for_pixel_checks": "available" if _has_pillow() else "not installed: pixel statistics use a slower pure-Python PNG path (small PNGs only)",
        "limits": "all numeric limits are DEFAULTS to verify against current Roblox documentation",
        "never": ["connects to Blender, Studio or Roblox", "uploads or publishes", "edits an export in place"],
        "needs_user_setup": ["your verified limits (rules/profiles/*.yaml)", "your naming scheme (style/style.yaml)", "the hub (hub__*) to run Blender scripts and roblox_upload_plan"],
    }


def _has_pillow() -> bool:
    import importlib.util

    return importlib.util.find_spec("PIL") is not None


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("PREFLIGHT_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["PREFLIGHT_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("PREFLIGHT_WORKSPACE", None)
            else:
                os.environ["PREFLIGHT_WORKSPACE"] = old


def _asset_file(project: Project, name: str) -> str:
    root = project.root / ASSETS
    direct = root / name
    if direct.is_file():
        return str(direct)
    hits = sorted(p for p in root.glob(name + ".*") if p.suffix != ".mtl")
    if not hits:
        raise ValueError(f"no example asset called '{name}' in {ASSETS}")
    return str(hits[0])


def _resolve(project: Project, value):
    """Replace '$file:<name>' strings (recursively) by the absolute path of an example asset."""
    if isinstance(value, str) and value.startswith("$file:"):
        return _asset_file(project, value[6:])
    if isinstance(value, dict):
        return {k: _resolve(project, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(project, v) for v in value]
    return value


def _digest(rep: dict) -> dict:
    f = rep["findings"]
    rules = sorted({x["rule"] for x in f})
    return {"result": rep["summary"]["result"], "ready": rep["summary"]["ready_to_upload"], "errors": rep["summary"]["errors"], "warnings": rep["summary"]["warnings"], "infos": rep["summary"]["infos"],
            "rules": rules, "error_rules": sorted({x["rule"] for x in f if x["severity"] == "error"}), "warn_rules": sorted({x["rule"] for x in f if x["severity"] == "warn"}),
            "findings": [{"rule": x["rule"], "severity": x["severity"], "object": x["object"], "measured": x["measured"], "limit": x["limit"], "fix": x["fix"], "safe_fix": x["safe_fix"],
                          "source": x["source"], "overridden": bool(x.get("overridden"))} for x in f],
            "facts": rep["facts"], "not_checked": [n["what"] for n in rep["not_checked"]], "handoff": rep["handoff"] is not None, "handoff_tool": (rep["handoff"] or {}).get("next_tool"),
            "overrides_applied": rep["overrides_applied"], "scope": rep["summary"]["scope"], "assumptions": rep["assumptions"], "text": rep.get("text", "")}


def eval_solver(project: Project):
    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        try:
            return _solve(project, inp, op)
        except (ValueError, PermissionError) as exc:
            return {"error": str(exc)}

    return solve


def _solve(project: Project, inp: dict, op: str) -> dict:
    tb = all_tools(project, HOOKS)
    if op == "preflight":
        args = {"project_id": inp.get("project_id", DEFAULT_PROJECT), "path": _asset_file(project, inp["file"]), "profile": inp["profile"], "verbose": True, "max_findings": 100}
        if inp.get("tool", "preflight_report") == "preflight_report":
            args["include_text"] = True
        for k in ("collision_intent", "limit_overrides", "apply_saved_overrides", "place_id"):
            if k in inp:
                args[k] = inp[k]
        if inp.get("texture_dir"):
            args["texture_dir"] = str(project.root / ASSETS / inp["texture_dir"])
        if inp.get("summary_file"):
            args["summary_path"] = _asset_file(project, inp["summary_file"])
        out = call_local(tb, inp.get("tool", "preflight_report"), args)
        d = _digest(out)
        if inp.get("twice"):
            d["deterministic"] = _digest(call_local(tb, inp.get("tool", "preflight_report"), args)) == d
        return d
    if op == "inspect":
        args = {"project_id": inp.get("project_id", DEFAULT_PROJECT), "path": _asset_file(project, inp["file"])}
        if inp.get("summary_file"):
            args["summary_path"] = _asset_file(project, inp["summary_file"])
        return call_local(tb, "inspect_export", args)
    if op == "measure":
        from .domain.measure import measure_asset

        a = loader.load_export(Path(_asset_file(project, inp["file"])))
        m = measure_asset(a)
        obj = next(o for o in a["objects"] if o["kind"] == "mesh" and (not inp.get("object") or o["name"] == inp["object"]))
        r = {k: v for k, v in m[obj["id"]].items() if isinstance(v, (int, float, bool, str)) or v is None}
        r["dims_world"] = list(m[obj["id"]]["dims_world"] or [])
        r["images"] = [{"name": i.get("name"), "w": i.get("width"), "h": i.get("height"), "channels": i.get("channels"), "format": i.get("format")} for i in a["images"]]
        r["bones"] = [s["joint_names"] for s in a["skins"]]
        r["animations"] = [{"name": x["name"], "fps": x["frame_rate"]} for x in a["animations"]]
        r["objects"] = [o["name"] for o in a["objects"]]
        r["format"] = a["format"]
        return r
    if op == "tool_call":
        with _temp_workspace():
            res = call_local(tb, inp["tool"], _resolve(project, inp.get("args", {})))
        return {"result": res, **{k: v for k, v in res.items() if k in ("dry_run", "profiles", "rules", "summary", "operations", "suggestions")}}
    if op == "fix_script":
        with _temp_workspace():
            args = {"project_id": inp.get("project_id", DEFAULT_PROJECT), "path": _asset_file(project, inp["file"]), "profile": inp["profile"]}
            if inp.get("rules"):
                args["rules"] = inp["rules"]
            plan = call_local(tb, "apply_safe_fix", args)
            asset = loader.load_export(Path(args["path"]))
            objects = [(o["name"], "MESH" if o["kind"] == "mesh" else "EMPTY", tuple(o["scale"]), (0.0, 0.0, 0.0)) for o in asset["objects"] if o["kind"] != "bone"]
            armatures = [s["joint_names"] for s in asset["skins"]]
            out: dict = {"dry_run": plan["dry_run"], "ops": [o["op"] for o in plan["operations"]], "operations": plan["operations"], "skipped": [s["why"] for s in plan["skipped"]],
                         "script_lines": plan["script"].count("\n") if plan["script"] else 0, "wrote_anything": "written" in plan, "has_script": plan["script"] is not None,
                         "not_fixed": plan["not_fixed_automatically"]}
            if plan["script"]:
                out["lint"] = fixes.lint_script(plan["script"])
                run = bpystub.run_script(plan["script"], objects, armatures)
                out["stub"] = run
                out["renamed_bones"] = run["result"]["bones_renamed"]
                out["renamed_objects"] = run["result"]["objects_renamed"]
                out["scale_applied"] = run["result"]["scale_applied"]
                out["bone_names_after"] = run["bone_names"]
                out["object_names_after"] = run["object_names"]
            return out
    if op == "compare":
        with _temp_workspace():
            return call_local(tb, "compare_versions", {"project_id": inp.get("project_id", DEFAULT_PROJECT), "before": _asset_file(project, inp["before"]), "after": _asset_file(project, inp["after"]), "profile": inp["profile"]})
    if op == "override_flow":
        return _override_flow(project, inp, tb)
    if op == "rules_check":
        cfg = engine.load_config(project.root)
        cov = engine.check_coverage(cfg)
        flagged = [(rid, k) for rid, r in cfg.rules.items() for k, v in r["limits"].items() if v["verify_against_current_docs"]]
        return {"rules": len(cfg.rules), "profiles": sorted(cfg.profiles), "rules_without_code": cov["rules_without_code"], "code_without_rules": cov["code_without_rules"],
                "categories": sorted({r["category"] for r in cfg.rules.values()}), "all_profiles_say_defaults": all(p.get("limits_are_defaults") for p in cfg.profiles.values()),
                "verify_flagged_limits": len(flagged), "safe_rules": sorted(r for r, v in cfg.rules.items() if v["safe"]),
                "every_rule_has_fix_and_explanation": all(r.get("fix") and r.get("explanation") for r in cfg.rules.values()),
                "severities": sorted({r["severity"] for r in cfg.rules.values()}), "rig_rules_scoped": all(set(r["applies_to"]) <= {"tool", "character_accessory"} for rid, r in cfg.rules.items()
                                                                                                        if rid.startswith("RIG_") and rid != "RIG_UNEXPECTED")}
    if op == "feedback":
        with _temp_workspace():
            pid = inp.get("project_id", DEFAULT_PROJECT)
            rid = call_local(tb, "record_run", {"project_id": pid, "request": inp["request"]})["run_id"]
            call_local(tb, "record_decision", {"project_id": pid, "run_id": rid, "decision": "revise", "reason": inp["reason"], "corrections": inp["corrections"]})
            found = call_local(tb, "find_past_corrections", {"project_id": pid, "request": inp["later_request"]})["corrections"]
        return {"found_run": bool(found and found[0]["run_id"] == rid)}
    if op == "descriptions":
        d = [(t.name, t.description) for t in tb]
        return {"tools": len(d), "longest": max(len(x) for _, x in d), "total": sum(len(x) for _, x in d), "rare": sorted(n for n, x in d if x.startswith("[rare]")),
                "read_only": sorted(t.name for t in tb if t.read_only), "writing": sorted(t.name for t in tb if not t.read_only)}
    if op == "isolation":
        return _isolation(project, inp, tb)
    if op == "output_size":
        with _temp_workspace():
            args = _resolve(project, inp["args"])
            if inp["tool"] != "list_projects":
                args.setdefault("project_id", inp.get("project_id", DEFAULT_PROJECT))
            res = call_local(tb, inp["tool"], args)
        return {"chars": len(json.dumps(res, default=str)), "keys": sorted(res)[:6], "first": next(iter(res))}
    if op == "image_info":
        from .domain import synth

        data = synth.png_bytes(inp["w"], inp["h"], color_type=inp.get("color_type", 2))
        i = imageinfo.image_info(data)
        return {"width": i["width"], "height": i["height"], "channels": i["channels"], "format": i["format"]}
    raise ValueError(f"unknown eval op '{op}'")


def _override_flow(project: Project, inp: dict, tb) -> dict:
    out: dict = {}
    with _temp_workspace() as ws:
        path = _asset_file(project, inp["file"])
        profile = inp["profile"]
        pid = inp.get("project_id", DEFAULT_PROJECT)
        base = {"project_id": pid, "path": path, "profile": profile, "verbose": True}
        before = call_local(tb, "preflight_report", base)
        out["before_errors"] = before["summary"]["errors"]
        ov = {"project_id": pid, "rule": inp["rule"], "asset_type": profile, "reason": inp["reason"], "value": inp.get("value"), "limit_key": inp.get("limit_key"), "waive": inp.get("waive", False)}
        dry = call_local(tb, "record_override", ov)
        out["dry_run_saved_nothing"] = dry["dry_run"] and not (ws / "feedback" / "overrides.jsonl").exists()
        for _ in range(inp.get("times", 1)):
            call_local(tb, "record_override", {**ov, "dry_run": False, "asset": f"asset_{_}"})
        after = call_local(tb, "preflight_report", base)
        out["after_errors"] = after["summary"]["errors"]
        out["after_rules"] = sorted({f["rule"] for f in after["findings"]})
        out["override_listed"] = [u["rule"] for u in after["overrides_applied"]]
        out["opt_out_ignores_override"] = call_local(tb, "preflight_report", {**base, "apply_saved_overrides": False})["summary"]["errors"] == before["summary"]["errors"]
        other = inp.get("other_profile")
        if other:
            out["other_profile_errors"] = call_local(tb, "preflight_report", {"project_id": inp.get("other_project", pid), "path": path, "profile": other})["errors"]
        sug = call_local(tb, "suggest_profile_promotions", {"project_id": pid, "min_count": inp.get("min_count", 3)})
        out["suggestions"] = len(sug["suggestions"])
        out["suggested_patch"] = (sug["suggestions"][0]["suggested_patch"] if sug["suggestions"] else None)
        out["offer_in_report"] = bool(after.get("promotion_offers")) if inp.get("times", 1) >= inp.get("min_count", 3) else None
        try:
            call_local(tb, "record_override", {**ov, "reason": "  ", "dry_run": False})
        except ValueError as exc:
            out["empty_reason_refused"] = "reason" in str(exc)
    return out


def _isolation(project: Project, inp: dict, tb) -> dict:
    """Corrections and overrides never cross projects unless marked global."""
    a, b, what = inp["a"], inp["b"], inp["what"]
    out: dict = {}
    with _temp_workspace():
        if what == "corrections":
            rid = call_local(tb, "record_run", {"project_id": a, "request": inp["request"]})["run_id"]
            call_local(tb, "record_decision", {"project_id": a, "run_id": rid, "decision": "revise", "reason": inp["reason"], "corrections": inp["corrections"]})
            found = lambda pid, **kw: call_local(tb, "find_past_corrections", {"project_id": pid, "request": inp["later_request"], **kw})["corrections"]
            out["a_sees"], out["b_sees"] = len(found(a)), len(found(b))
            rg = call_local(tb, "record_run", {"project_id": a, "request": inp["request"] + " global", "global_scope": True})["run_id"]
            call_local(tb, "record_decision", {"project_id": a, "run_id": rg, "decision": "revise", "reason": inp["reason"] + " everywhere", "corrections": inp["corrections"], "global_scope": True})
            out["b_sees_with_global"] = len(found(b))
            out["b_sees_global_off"] = len([c for c in found(b, include_global=False)])
            out["scopes_seen_by_b"] = sorted({c["scope"] for c in found(b)})
            return out
        path = _asset_file(project, inp["file"])
        base = lambda pid: call_local(tb, "preflight_report", {"project_id": pid, "path": path, "profile": inp["profile"], "verbose": True})
        ov = {"rule": inp["rule"], "asset_type": inp["profile"], "reason": inp["reason"], "value": inp["value"], "limit_key": inp["limit_key"], "dry_run": False}
        out["a_before"], out["b_before"] = base(a)["summary"]["errors"], base(b)["summary"]["errors"]
        call_local(tb, "record_override", {"project_id": a, **ov})
        out["a_after_project_override"], out["b_after_project_override"] = base(a)["summary"]["errors"], base(b)["summary"]["errors"]
        call_local(tb, "record_override", {"project_id": a, **ov, "global_override": True})
        out["b_after_global_override"] = base(b)["summary"]["errors"]
        out["b_override_listed"] = [u["rule"] for u in base(b)["overrides_applied"]]
        return out


def eval_checks(project: Project) -> dict:
    dig = _evals.dig

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:200]!r})"

    def approx(result, task, path, value, tol=0.01):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def at_least(result, task, path, value):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got >= value, f"{path}={got!r}; expected >= {value}"

    def at_most(result, task, path, value):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got <= value, f"{path}={got!r}; expected <= {value}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def text_has(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() in got.lower(), f"{path} should contain {text!r}"

    def text_lacks(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() not in got.lower(), f"{path} should not contain {text!r}"

    def is_none(result, task, path):
        got = dig(result, path, "MISSING")
        return got is None, f"{path}={got!r}; expected null"

    def has_rule(result, task, rule, severity=None, object=None):
        hits = [f for f in result.get("findings", []) if f["rule"] == rule and (severity is None or f["severity"] == severity) and (object is None or f["object"] == object)]
        return bool(hits), f"expected a {severity or ''} {rule} finding" + (f" on {object}" if object else "") + f"; got rules {result.get('rules')}"

    def lacks_rule(result, task, rule):
        return rule not in (result.get("rules") or []), f"{rule} must not be reported (rules: {result.get('rules')})"

    def rules_only(result, task, rules, severities=("error", "warn")):
        got = sorted({f["rule"] for f in result.get("findings", []) if f["severity"] in severities})
        return got == sorted(rules), f"error/warn rules {got}; expected exactly {sorted(rules)}"

    def clean(result, task):
        bad = [f"{f['rule']}({f['object']})" for f in result.get("findings", []) if f["severity"] in ("error", "warn")]
        return result.get("result") == "pass" and not bad, f"a known-good asset must pass with no errors or warnings; got {result.get('result')} {bad}"

    def finding_value(result, task, rule, value, tol=0.01, field="measured"):
        for f in result.get("findings", []):
            if f["rule"] == rule:
                got = f[field]
                return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{rule}.{field}={got!r}; expected {value} +/- {tol}"
        return False, f"no {rule} finding"

    def finding_text(result, task, rule, text, field="fix"):
        for f in result.get("findings", []):
            if f["rule"] == rule:
                return text.lower() in str(f[field]).lower(), f"{rule}.{field}={f[field]!r} should contain {text!r}"
        return False, f"no {rule} finding"

    def one_line_fixes(result, task):
        bad = [f["rule"] for f in result.get("findings", []) if not f["fix"] or "\n" in f["fix"] or "{" in f["fix"]]
        return not bad, f"every finding needs a one-line, fully filled fix; offenders: {bad}"

    return {"error_contains": error_contains, "approx": approx, "at_least": at_least, "at_most": at_most, "list_contains": list_contains, "list_excludes": list_excludes,
            "text_has": text_has, "text_lacks": text_lacks, "is_none": is_none, "has_rule": has_rule, "lacks_rule": lacks_rule, "rules_only": rules_only, "clean": clean,
            "finding_value": finding_value, "finding_text": finding_text, "one_line_fixes": one_line_fixes}


def register_cli(sub, project: Project) -> None:
    skillgen.add_export_skill_command(sub, project, learning_params.skill_kwargs, resolve=learning_params.resolve_scope)

    p = sub.add_parser("report", help="full preflight of an export; exit 1 when it fails")
    p.add_argument("path")
    p.add_argument("--project", required=True, help="project_id from projects.yaml")
    p.add_argument("--place", type=int, help="optional place_id (must match the registry)")
    p.add_argument("--profile", required=True, choices=["prop", "tool", "character_accessory", "terrain_piece"])
    p.add_argument("--texture-dir")
    p.add_argument("--summary", help="hub summary JSON (schema preflight-summary/1)")
    p.add_argument("--collision", default="auto", choices=["auto", "present", "absent"])
    p.add_argument("--json", action="store_true", help="print the full JSON instead of the text report")
    p.set_defaults(handler=_report)

    p = sub.add_parser("inspect", help="describe what is inside an export (no rules)")
    p.add_argument("path")
    p.add_argument("--project", required=True)
    p.add_argument("--texture-dir")
    p.add_argument("--summary")
    p.set_defaults(handler=_inspect)

    p = sub.add_parser("profiles", help="list a project's profiles and their DEFAULT limits")
    p.add_argument("--project", required=True)
    p.add_argument("--rules", action="store_true")
    p.set_defaults(handler=lambda a, pr: emit(call_local(all_tools(pr, HOOKS), "list_profiles", {"project_id": a.project, "include_rules": a.rules})) or 0)

    p = sub.add_parser("projects", help="list registered projects")
    p.set_defaults(handler=lambda a, pr: emit(call_local(all_tools(pr, HOOKS), "list_projects", {})) or 0)

    p = sub.add_parser("fix-script", help="print the Blender script for safe fixes (never run by this tool)")
    p.add_argument("path")
    p.add_argument("--project", required=True)
    p.add_argument("--profile", required=True, choices=["prop", "tool", "character_accessory", "terrain_piece"])
    p.set_defaults(handler=_fix_script)


def _report(a, pr) -> int:
    args = {"project_id": a.project, "path": a.path, "profile": a.profile, "collision_intent": a.collision, "include_text": not a.json, "verbose": True}
    if a.place:
        args["place_id"] = a.place
    if a.texture_dir:
        args["texture_dir"] = a.texture_dir
    if a.summary:
        args["summary_path"] = a.summary
    res = call_local(all_tools(pr, HOOKS), "preflight_report", args)
    if a.json:
        emit(res)
    else:
        print(res["text"])
    return 0 if res["summary"]["result"] == "pass" else 1


def _inspect(a, pr) -> int:
    args = {"project_id": a.project, "path": a.path}
    if a.texture_dir:
        args["texture_dir"] = a.texture_dir
    if a.summary:
        args["summary_path"] = a.summary
    emit(call_local(all_tools(pr, HOOKS), "inspect_export", args))
    return 0


def _fix_script(a, pr) -> int:
    res = call_local(all_tools(pr, HOOKS), "apply_safe_fix", {"project_id": a.project, "path": a.path, "profile": a.profile})
    print(res["script"] or "# no safe fixes apply to this export")
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
