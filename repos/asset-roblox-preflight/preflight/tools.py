"""MCP tools for the Blender-to-Roblox asset preflight.

Every tool takes a project_id (a place_id is optional and only recorded for the upload hand-off); a tool without a resolved project refuses. Read-only tools check an
export against a profile and answer briefly: a one-line summary, ranked findings, details through explain_finding. Writing tools default to dry_run=true. Nothing here
connects to Blender, Studio or Roblox, edits an export, or uploads. All numeric limits are DEFAULTS to verify.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import projects as P
from .domain import engine, fixes, loader, overrides as OV, report as R, summary as SM
from .domain.rules import Overrides, render
from .guide_adapter import Plan, Project, ToolSpec, Versioner, common_tools, feedback as fb, resolve_inside, safe_name
from .guide_adapter import scope as _scope

READ_SUFFIXES = {".glb", ".gltf", ".obj", ".fbx", ".json"}
RARE = ("apply_safe_fix", "save_report", "compare_versions", "suggest_profile_promotions", "record_override", "promote_run", "search_library")
PROFILES = ("prop", "tool", "character_accessory", "terrain_piece")


def make_tools(project: Project) -> list[ToolSpec]:
    def entry(project_id: str | None, place_id=None) -> dict:
        return P.resolve(project.root, project_id, place_id)

    def cfg(project_id: str):
        entry(project_id)
        return engine.load_config(project.root, project_id)

    def sp(project_id: str, is_global: bool = False) -> P.ScopedProject:
        entry(project_id)
        return P.scoped(project, P.GLOBAL if is_global else project_id)

    def read_roots() -> list[Path]:
        roots = [*project.allowed_roots, project.root.resolve()]
        if project.asset_root:
            roots.append(project.asset_root.resolve())
        return roots

    def export_path(p: str) -> Path:
        path = _scope.PathPolicy(tuple(read_roots())).resolve(p)
        if not path.is_file() or path.suffix.lower() not in READ_SUFFIXES:
            raise ValueError(f"'{p}' is not an existing export file ({sorted(READ_SUFFIXES)})")
        return path

    def load(path: str, texture_dir: str | None, summary: dict | None, summary_path: str | None) -> dict:
        p = export_path(path)
        tdir = resolve_inside(texture_dir, read_roots()) if texture_dir else None
        if tdir is not None and not tdir.is_dir():
            raise ValueError(f"texture_dir '{texture_dir}' is not a folder")
        if summary is not None and summary_path:
            raise ValueError("pass either summary or summary_path, not both")
        doc = summary
        if summary_path:
            sfile = export_path(summary_path)
            if sfile.suffix.lower() != ".json":
                raise ValueError("summary_path must be a .json file (schema preflight-summary/1, see the README)")
            doc = SM.read_summary_file(sfile)
        return loader.load_export(p, texture_dir=tdir, summary_doc=doc)

    def saved_rows(project_id: str, profile: str | None) -> list[dict]:
        return OV.load_saved(sp(project_id), profile) + OV.load_saved(sp(project_id, True), profile)

    def call_overrides(project_id: str, limit_overrides: dict | None) -> dict:
        c = cfg(project_id)
        out = {}
        for rid, lims in (limit_overrides or {}).items():
            if rid not in c.rules:
                raise ValueError(f"limit_overrides names unknown rule '{rid}'")
            if not isinstance(lims, dict):
                raise ValueError(f"limit_overrides['{rid}'] must be an object like {{'max_triangles': 30000}}")
            for k in lims:
                if k not in c.rules[rid]["limits"]:
                    raise ValueError(f"rule {rid} has no limit '{k}'. Limits: {sorted(c.rules[rid]['limits'])}")
            out[rid] = lims
        return out

    def build(tool: str, project_id: str, place_id, path: str, profile: str, texture_dir=None, summary=None, summary_path=None, collision_intent="auto", limit_overrides=None,
              apply_saved_overrides=True) -> tuple[dict, dict]:
        e = entry(project_id, place_id)
        c = cfg(project_id)
        if profile not in c.profiles:
            raise ValueError(f"unknown profile '{profile}'. Choose one of {sorted(c.profiles)}")
        if collision_intent not in ("auto", "present", "absent"):
            raise ValueError("collision_intent must be auto, present or absent")
        asset = load(path, texture_dir, summary, summary_path)
        saved = saved_rows(project_id, profile) if apply_saved_overrides else []
        ov = Overrides(call=call_overrides(project_id, limit_overrides), saved=saved, asset_name=asset.get("name"))
        rep = R.build_report(asset, c, profile, overrides=ov, collision_intent=collision_intent, tool=tool, project=e, place_id=place_id)
        return rep, asset

    # ------------------------------------------------------------------------------------------ read-only
    def list_projects() -> dict[str, Any]:
        """List registered projects (id, place, universe). Every other tool needs one of these ids."""
        reg = P.load_registry(project.root)
        return {"projects": [{"id": k, "alias": v["alias"], "place_id": v["place_id"], "universe": v["universe"], "synthetic": bool(v.get("synthetic")),
                              "profile_file": v["profile"]} for k, v in reg.items()], "note": "Register your own games in projects.yaml. The shipped ones are synthetic examples."}

    def list_profiles(project_id: str, profile: str | None = None, include_rules: bool = False) -> dict[str, Any]:
        """Profiles (prop, tool, character_accessory, terrain_piece) with this project's DEFAULT limits. include_rules adds the rule table."""
        c = cfg(project_id)
        if profile is not None and profile not in c.profiles:
            raise ValueError(f"unknown profile '{profile}'. Choose one of {sorted(c.profiles)}")
        out = []
        for name, p in c.profiles.items():
            if profile and name != profile:
                continue
            eff = {}
            for rid, r in c.rules.items():
                if r.get("applies_to") and name not in r["applies_to"]:
                    continue
                lims = {k: (p.get("limits") or {}).get(rid, {}).get(k, spec["default"]) for k, spec in r["limits"].items()}
                if lims:
                    eff[rid] = lims
            out.append({"name": name, "rig_checks": bool(p.get("rig_checks")), "limits": eff if profile or include_rules else {k: v for k, v in eff.items() if k in (p.get("limits") or {})}})
        res: dict[str, Any] = {"project": project_id, "profiles": out, "note": "All numbers are DEFAULTS chosen by this tool, not official Roblox limits. Verify them."}
        if include_rules:
            res["rules"] = [{"id": rid, "category": r["category"], "severity": r["severity"], "safe": r["safe"], "title": r["title"]} for rid, r in c.rules.items()]
        return res

    def inspect_export(project_id: str, path: str, texture_dir: str | None = None, summary: dict | None = None, summary_path: str | None = None) -> dict[str, Any]:
        """Describe an export (GLB, glTF, OBJ, FBX header, hub summary): objects, sizes, materials, images, rig, and what cannot be measured. No rules."""
        from .domain.measure import measure_asset

        entry(project_id)
        a = load(path, texture_dir, summary, summary_path)
        mets = measure_asset(a)
        objs = []
        for o in a["objects"][:60]:
            m = mets.get(o["id"], {})
            objs.append({"name": o["name"], "kind": o["kind"], "tris": m.get("triangles"), "size": [round(x, 3) for x in m["dims_world"]] if m.get("dims_world") else None})
        return {"project": project_id, "asset": a["name"], "format": a["format"], "reader": a["reader"], "file_size_bytes": a.get("file_size"), "geometry_measured": a.get("geometry_measured", True),
                "fbx": a.get("fbx"), "objects": objs, "objects_total": len(a["objects"]),
                "materials": [{"name": m.get("name"), "textures": {s: (a["images"][i].get("uri") or a["images"][i].get("name")) for s, i in m["textures"].items()}} for m in a["materials"]],
                "images": [{"name": i.get("uri") or i.get("name"), "format": i.get("format"), "size": f"{i['width']}x{i['height']}" if i.get("width") else None, "exists": bool(i.get("exists") or i.get("embedded"))}
                           for i in a["images"]],
                "bones": [{"count": len(s["joint_names"]), "roots": s["root_joints"]} for s in a.get("skins", [])],
                "animations": [{"name": x.get("name"), "fps": x.get("frame_rate")} for x in a.get("animations", [])], "not_measurable": [n["what"] for n in a.get("not_measurable", [])],
                "warnings": a.get("warnings", [])}

    def _check(tool: str, doc: str):
        def fn(project_id: str, path: str, profile: str, place_id: int | None = None, texture_dir: str | None = None, summary: dict | None = None, summary_path: str | None = None,
               collision_intent: str = "auto", limit_overrides: dict[str, dict[str, Any]] | None = None, max_findings: int = 10, verbose: bool = False) -> dict[str, Any]:
            rep, _ = build(tool, project_id, place_id, path, profile, texture_dir, summary, summary_path, collision_intent, limit_overrides)
            return R.compact(rep, max_findings, verbose)

        fn.__name__, fn.__doc__ = tool, doc
        return fn

    check_mesh = _check("check_mesh", "Mesh, transform and collision checks (triangles, loose geometry, manifold, normals, n-gons, scale, bbox, origin, units, proxy). Partial: never ready_to_upload.")
    check_uvs = _check("check_uvs", "UV checks (present, 0-1, overlap, texel density, wasted space). Partial: never ready_to_upload.")
    check_textures = _check("check_textures", "Material and texture checks (count, missing files, format, power of two, size, colour space, channels). Partial: never ready_to_upload.")
    check_rig = _check("check_rig", "Rig checks for tool and character_accessory only (root, names, bone count, one-bone spin setup, clips, fps). Partial: never ready_to_upload.")
    check_names = _check("check_names", "Name checks (duplicates, spaces, odd characters, .001 suffixes, naming scheme). Partial: never ready_to_upload.")

    def preflight_report(project_id: str, path: str, profile: str, place_id: int | None = None, texture_dir: str | None = None, summary: dict | None = None,
                         summary_path: str | None = None, collision_intent: str = "auto", limit_overrides: dict[str, dict[str, Any]] | None = None, apply_saved_overrides: bool = True,
                         max_findings: int = 10, verbose: bool = False, include_text: bool = False) -> dict[str, Any]:
        """Full preflight: one-line summary, ranked findings (rule, object, measured, limit, fix), ready_to_upload only when no errors remain. Hands off to roblox_upload_plan; never uploads."""
        rep, _ = build("preflight_report", project_id, place_id, path, profile, texture_dir, summary, summary_path, collision_intent, limit_overrides, apply_saved_overrides)
        out = R.compact(rep, max_findings, verbose, include_text)
        if rep["overrides_applied"]:
            offers = [s for s in OV.suggest(saved_rows(project_id, profile), cfg(project_id)) if s["offer"]]
            if offers:
                out["promotion_offers"] = [o["offer"] for o in offers[:2]]
        return out

    def explain_finding(project_id: str, rule: str, profile: str | None = None, path: str | None = None, object: str | None = None, texture_dir: str | None = None,
                        summary_path: str | None = None) -> dict[str, Any]:
        """Details for one rule: explanation, fix template, safe flag, effective limits (with verify flags). With path and profile, also that finding's full record."""
        c = cfg(project_id)
        if rule not in c.rules:
            raise ValueError(f"unknown rule '{rule}'. Use list_profiles(include_rules=true) for ids.")
        r = c.rules[rule]
        prof = c.profiles.get(profile) if profile else None
        if profile and prof is None:
            raise ValueError(f"unknown profile '{profile}'")
        limits = {k: {"value": ((prof or {}).get("limits") or {}).get(rule, {}).get(k, v["default"]), "default": v["default"], "verify_against_current_docs": v["verify_against_current_docs"], "note": v.get("note")}
                  for k, v in r["limits"].items()}
        out: dict[str, Any] = {"project": project_id, "rule": rule, "title": r["title"], "category": r["category"], "severity": (prof or {}).get("severity", {}).get(rule, r["severity"]),
                               "explanation": r["explanation"], "fix_template": r["fix"], "safe": r["safe"], "applies_to": r.get("applies_to"), "limits": limits}
        if path:
            if not profile:
                raise ValueError("pass profile together with path to get the finding itself")
            rep, _ = build("preflight_report", project_id, None, path, profile, texture_dir, None, summary_path)
            hits = [f for f in rep["findings"] if f["rule"] == rule and (object is None or f["object"] == object)]
            out["findings"] = [{k: f.get(k) for k in ("id", "object", "message", "detail", "measured", "limit", "fix", "safe_fix", "fix_op", "source", "overridden", "verify_against_current_docs")} for f in hits[:5]]
            if not hits:
                out["findings_note"] = f"{rule} is not reported for this file under profile {profile}"
        return out

    def compare_versions(project_id: str, before: str, after: str, profile: str, texture_dir: str | None = None) -> dict[str, Any]:
        """[rare] Compare two exports (paths) or saved reports ('report:<name>@<n>'): fixed, new, changed findings, size and triangle changes, verdict."""
        cfg(project_id)

        def get(ref: str) -> dict:
            if ref.startswith("report:"):
                name, _, ver = ref[len("report:"):].partition("@")
                try:
                    return json.loads(Versioner(sp(project_id)).load(f"report_{safe_name(name)}", int(ver) if ver else None))
                except FileNotFoundError as exc:
                    raise ValueError(str(exc)) from exc
            return build("preflight_report", project_id, None, ref, profile, texture_dir)[0]

        res = R.compare_reports(get(before), get(after))
        res["project"], res["profile"] = project_id, profile
        return res

    def suggest_profile_promotions(project_id: str, min_count: int = OV.MIN_REPEATS, asset_type: str | None = None) -> dict[str, Any]:
        """[rare] Offer to promote repeated overrides into this project's profile (exact patch). Read-only; never edits a profile."""
        c = cfg(project_id)
        if min_count < 1:
            raise ValueError("min_count must be at least 1")
        if asset_type is not None and asset_type not in c.profiles:
            raise ValueError(f"unknown asset_type '{asset_type}'. Choose one of {sorted(c.profiles)}")
        rows = saved_rows(project_id, asset_type)
        return {"project": project_id, "min_count": min_count, "saved_overrides": len(rows), "suggestions": OV.suggest(rows, c, min_count, asset_type),
                "note": "Ask the user first, then edit the named file and re-run the evals."}

    # ------------------------------------------------------------------------------------------ writing
    def apply_safe_fix(project_id: str, path: str, profile: str, rules: list[str] | None = None, texture_dir: str | None = None, summary_path: str | None = None,
                       dry_run: bool = True) -> dict[str, Any]:
        """[rare] Plan (default) or write a Blender Python script applying ONLY safe:true fixes (renames, apply scale or rotation). Never edits a file; run on a COPY via the hub, then re-export."""
        c = cfg(project_id)
        for rid in rules or []:
            if rid not in c.rules:
                raise ValueError(f"unknown rule '{rid}'")
            if not c.rules[rid]["safe"]:
                raise ValueError(f"rule {rid} is not marked safe: true, so apply_safe_fix will not emit a script for it. Safe rules: {sorted(r for r, v in c.rules.items() if v['safe'])}. Fix it by hand using the finding's one-line fix.")
        rep, asset = build("preflight_report", project_id, None, path, profile, texture_dir, None, summary_path, apply_saved_overrides=False)
        ops, skipped = fixes.plan_operations(rep["findings"], {o["name"] for o in asset["objects"]}, rules)
        manual = sorted({f["rule"] for f in rep["findings"] if f["severity"] != "info" and not f["safe_fix"]})
        script = fixes.render_script(ops) if ops else None
        problems = fixes.lint_script(script) if script else []
        if problems:
            raise ValueError("generated script failed its own safety lint: " + "; ".join(problems))
        plan = Plan(f"{len(ops)} safe operation(s) for {asset['name']} ({profile}, project {project_id})")
        for op in ops:
            plan.add(op["op"], op.get("object") or op.get("old") or op.get("bone"), json.dumps({k: v for k, v in op.items() if k not in ("op", "rule")}))
        plan.warnings += ["Run on a COPY of the .blend, then re-export and run preflight_report again. Nothing has been run or changed."]
        out_path = resolve_inside(sp(project_id).output_dir / "fixes" / f"{safe_name(asset['name'])}_{fixes.script_id(script)}.py", project.allowed_roots) if script else None
        result: dict[str, Any] = {**plan.to_dict(dry_run), "project": project_id, "operations": ops, "skipped": skipped, "not_fixed_automatically": manual, "script": script,
                                  "would_write": str(out_path) if out_path else None, "handoff": "Give the script to the hub (hub__*) to run in Blender; this server never connects to Blender."}
        if not ops:
            result["summary"] = "no safe fixes apply to this export"
        if not dry_run and script:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(script, encoding="utf-8")
            result["written"] = str(out_path)
        return result

    def save_report(project_id: str, path: str, profile: str, name: str | None = None, place_id: int | None = None, texture_dir: str | None = None, summary_path: str | None = None,
                    dry_run: bool = True) -> dict[str, Any]:
        """[rare] Save the full report as a numbered version in this project's workspace (dry_run=true shows the plan). Compare later with compare_versions."""
        rep, asset = build("preflight_report", project_id, place_id, path, profile, texture_dir, None, summary_path)
        nm = safe_name(name or asset["name"])
        v = Versioner(sp(project_id))
        plan = Plan(f"save report '{nm}' as version {len(v.history(f'report_{nm}')) + 1} for project {project_id}").add("save_report", nm, rep["summary"]["result"])
        if dry_run:
            return {**plan.to_dict(True), "summary_line": R.summary_line(rep)}
        saved = v.save(f"report_{nm}", json.dumps(rep, indent=2, ensure_ascii=False, default=str), label=f"{rep['summary']['result']} {profile}")
        return {**plan.to_dict(False), "saved": saved, "summary_line": R.summary_line(rep)}

    def record_override(project_id: str, rule: str, asset_type: str, reason: str, value: Any = None, limit_key: str | None = None, waive: bool = False, asset_glob: str | None = None,
                        run_id: str | None = None, asset: str | None = None, global_override: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """[rare] Save a user override (e.g. rule MESH_TRI_BUDGET, value 30000) scoped to this project and asset type; global_override=true applies it to every project. reason = the user's words."""
        c = cfg(project_id)
        OV.validate_override(c, rule=rule, asset_type=asset_type, reason=reason, value=value, limit_key=limit_key, waive=waive)
        lk = OV.normalise(c, rule, limit_key, waive)
        store = sp(project_id, global_override)
        if run_id is not None and fb.get_run(store, run_id) is None:
            raise ValueError(f"unknown run_id '{run_id}' in the {'global' if global_override else 'project'} store (record_run first, or omit it)")
        row = OV.make_row(rule=rule, asset_type=asset_type, reason=reason, value=value, limit_key=lk, waive=waive, asset_glob=asset_glob, run_id=run_id, asset=asset, project_id=project_id, is_global=global_override)
        scope_txt = "every project" if global_override else f"project {project_id}"
        plan = Plan(f"save override {rule}{'.' + lk if lk else ''} {'waived' if waive else '= ' + json.dumps(value)} for {asset_type} assets in {scope_txt}").add("append", str(OV.overrides_file(store)), row["override_id"])
        if dry_run:
            return {**plan.to_dict(True), "row": row}
        OV.append(store, row)
        res = {**plan.to_dict(False), "row": row, "saved": True}
        hits = [s for s in OV.suggest(saved_rows(project_id, asset_type), c, OV.MIN_REPEATS, asset_type) if s["rule"] == rule and s["limit_key"] == lk and s["offer"]]
        if hits:
            res["promotion_offer"], res["suggested_patch"] = hits[0]["offer"], hits[0]["suggested_patch"]
        return res

    # ------------------------------------------------------------------------------------ project-scoped shared tools
    dims = (engine.load_config(project.root).style or {}).get("correction_dimensions")

    def core(scope: P.ScopedProject, name: str):
        return next(t.fn for t in common_tools(scope, dims) if t.name == name)

    def get_style_brief(project_id: str, focus: list[str] | None = None, max_chars: int = 1200) -> dict[str, Any]:
        """Compact style spec (traits, must/should rules) plus this project's naming scheme."""
        base = core(sp(project_id), "get_style_brief")(focus=focus, max_chars=max_chars)
        n = cfg(project_id).style.get("naming") or {}
        base["naming"] = {"mesh_patterns": n.get("mesh_patterns"), "collision_pattern": n.get("collision_pattern")}
        base["project"] = project_id
        base.pop("ranges", None)
        return base

    def find_past_corrections(project_id: str, request: str, k: int = 5, include_global: bool = True) -> dict[str, Any]:
        """Past corrections for this project (and global ones) relevant to a request. Read before planning."""
        rows = [{**c, "scope": project_id} for c in fb.corrections_for(sp(project_id), request, k)]
        if include_global:
            rows += [{**c, "scope": "global"} for c in fb.corrections_for(sp(project_id, True), request, k)]
        rows.sort(key=lambda c: -c["score"])
        return {"project": project_id, "corrections": rows[:k]}

    def record_run(project_id: str, request: str, constraints: dict | None = None, outputs: list[str] | None = None, place_id: int | None = None, global_scope: bool = False) -> dict[str, Any]:
        """Save what was asked and produced (with the project and place). Returns run_id. global_scope=true stores it for every project."""
        e = entry(project_id, place_id)
        con = {**(constraints or {}), "project_id": project_id, "place_id": place_id if place_id is not None else e["place_id"]}
        res = core(sp(project_id, global_scope), "record_run")(request=request, constraints=con, outputs=outputs)
        return {**res, "project": project_id, "scope": "global" if global_scope else project_id}

    def record_decision(project_id: str, run_id: str, decision: str, reason: str = "", corrections: list[dict] | None = None, rating: int | None = None, global_scope: bool = False) -> dict[str, Any]:
        """Save the user's verdict (accept, reject, revise) with corrections [{dimension, note}], in their words. global_scope must match the run's."""
        res = core(sp(project_id, global_scope), "record_decision")(run_id=run_id, decision=decision, reason=reason, corrections=corrections, rating=rating)
        return {**res, "project": project_id, "scope": "global" if global_scope else project_id}

    def promote_run(project_id: str, run_id: str, kind: str, title: str, description: str, tags: list[str], path: str, polarity: str = "positive", confirm: bool = False,
                    global_scope: bool = False) -> dict[str, Any]:
        """[rare] Add a reviewed run to this project's library (candidate unless confirm=true; ask the user first)."""
        res = core(sp(project_id, global_scope), "promote_run")(run_id=run_id, kind=kind, title=title, description=description, tags=tags, path=path, polarity=polarity, confirm=confirm)
        return {**res, "project": project_id, "scope": "global" if global_scope else project_id}

    def search_library(project_id: str, query: str = "", kind: str | None = None, tags: list[str] | None = None, k: int = 5, include_candidates: bool = False) -> dict[str, Any]:
        """[rare] Search the example exports (good and bad) plus this project's own library."""
        return {"project": project_id, **core(sp(project_id), "search_library")(query=query, kind=kind, tags=tags, k=k, include_candidates=include_candidates)}

    R_ = ToolSpec
    specs = [
        R_("list_projects", list_projects, list_projects.__doc__),
        R_("get_style_brief", get_style_brief, get_style_brief.__doc__),
        R_("find_past_corrections", find_past_corrections, find_past_corrections.__doc__),
        R_("list_profiles", list_profiles, list_profiles.__doc__),
        R_("inspect_export", inspect_export, inspect_export.__doc__),
        R_("check_mesh", check_mesh, check_mesh.__doc__),
        R_("check_uvs", check_uvs, check_uvs.__doc__),
        R_("check_textures", check_textures, check_textures.__doc__),
        R_("check_rig", check_rig, check_rig.__doc__),
        R_("check_names", check_names, check_names.__doc__),
        R_("preflight_report", preflight_report, preflight_report.__doc__),
        R_("explain_finding", explain_finding, explain_finding.__doc__),
        R_("compare_versions", compare_versions, compare_versions.__doc__),
        R_("suggest_profile_promotions", suggest_profile_promotions, suggest_profile_promotions.__doc__),
        R_("search_library", search_library, search_library.__doc__),
        R_("apply_safe_fix", apply_safe_fix, apply_safe_fix.__doc__, read_only=False, idempotent=True),
        R_("save_report", save_report, save_report.__doc__, read_only=False, idempotent=False),
        R_("record_run", record_run, record_run.__doc__, read_only=False, idempotent=False),
        R_("record_decision", record_decision, record_decision.__doc__, read_only=False, idempotent=False),
        R_("record_override", record_override, record_override.__doc__, read_only=False, idempotent=False),
        R_("promote_run", promote_run, promote_run.__doc__, read_only=False, idempotent=True),
    ]
    for s in specs:
        if s.name in RARE and not s.description.startswith("[rare]"):
            s.description = "[rare] " + s.description
    if str(project.env("DISABLE_RARE") or "").lower() in ("1", "true", "yes"):
        specs = [s for s in specs if s.name not in RARE]
    return specs
