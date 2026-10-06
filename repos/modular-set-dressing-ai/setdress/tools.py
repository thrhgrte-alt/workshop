"""MCP tools for the set-dressing assistant.

Placement is deterministic and reversible: every change is a *plan* (adds, moves, removes) you can review. With dry_run=true
(the default) nothing is saved. Applying saves a new scene version, so any change can be undone. Locked instances can never be
moved or removed, and are checked against revision 1 of the scene. The server never talks to Studio; generated Luau is forwarded
by the agent to Roblox Studio's own MCP server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import retrieval
from .core.commontools import default_embedder
from .core.manifest import LibraryStore
from .core.mcpkit import ToolSpec
from .core.project import Project
from .core.rubric import load_rubric, score_rubric
from .core.safety import Plan, Versioner, resolve_inside, safe_name
from .core.style import check_ranges, load_style
from .domain import api, compose, dress, export, kit as K, mapview, scene as SC


def kit_path(project: Project) -> Path:
    explicit = project.env("KIT")
    if explicit:
        return Path(explicit).expanduser()
    local = project.workspace / "kit.yaml"
    return local if local.exists() else project.root / "examples" / "kit" / "kit.yaml"


def load_kit(project: Project) -> tuple[dict, list[dict]]:
    return K.normalize_kit(K.load_kit(kit_path(project)))


def make_tools(project: Project) -> list[ToolSpec]:
    store = LibraryStore(project)
    versions = Versioner(project)

    def kit_() -> dict:
        kit, findings = load_kit(project)
        errors = [f for f in findings if f["severity"] == "error"]
        if errors:
            raise ValueError("the kit has errors: " + "; ".join(f"[{e['code']}] {e['message']}" for e in errors[:4]))
        return kit

    def resolve(scene: dict | None, scene_name: str | None, version: int | None) -> dict:
        if (scene is None) == (scene_name is None):
            raise ValueError("pass exactly one of: scene (a dict) or scene_name (a saved scene)")
        raw = scene if scene is not None else json.loads(versions.load(f"scene-{scene_name}", version))
        return SC.normalize(raw)

    def baseline_lock(scene: dict, baseline_version: int = 1) -> dict | None:
        try:
            return SC.lock_snapshot(SC.normalize(json.loads(versions.load(f"scene-{scene['id']}", baseline_version))))
        except FileNotFoundError:
            return None

    def must_be_valid(scene: dict, kit: dict) -> None:
        errors = [f for f in SC.validate_structure(scene, kit) if f["severity"] == "error"]
        if errors:
            raise ValueError("scene is invalid: " + "; ".join(f"[{e['code']}] {e['message']}" for e in errors[:5]))

    def save(scene: dict, label: str) -> dict:
        clean = json.dumps(scene, indent=2, sort_keys=True)
        return versions.save(f"scene-{scene['id']}", clean, label=label)

    def apply_and_maybe_save(scene: dict, kit: dict, plan: dict, dry_run: bool, label: str) -> dict[str, Any]:
        after = SC.apply_plan(scene, plan)
        findings = SC.validate(after, kit)
        errs = [f for f in findings if f["severity"] == "error"]
        lock = baseline_lock(scene)
        lock_findings = SC.check_locks(after, lock) if lock else []
        out: dict[str, Any] = {"dry_run": dry_run, "plan": plan, "findings": findings + lock_findings, "diff": SC.diff(scene, after),
                               "ok": not errs and not lock_findings, "scene_hash_after": SC.scene_hash(after)}
        if not dry_run:
            if errs or lock_findings:
                raise ValueError("refusing to save: " + "; ".join(f["message"] for f in (errs + lock_findings)[:4]))
            saved = save(after, label)
            out["saved"] = {"version": saved["version"], "scene": after["id"]}
        return out

    # --- kit ------------------------------------------------------------------------------------------------------------------------
    def search_kit(query: str = "", tags: list[str] | None = None, tags_any: list[str] | None = None, max_width: float | None = None,
                   max_depth: float | None = None, max_height: float | None = None, socket_type: str | None = None,
                   size_class: str | None = None, k: int = 8) -> dict[str, Any]:
        """Search the APPROVED kit by text, tags, size limits, socket type or size class. Always search here before proposing a replacement asset."""
        fp = (max_width if max_width is not None else 1e9, max_depth if max_depth is not None else 1e9) if (max_width or max_depth) else None
        hits = K.search(kit_(), query, tags_all=tags or (), tags_any=tags_any or (), max_footprint=fp, max_height=max_height, socket_type=socket_type,
                        size_class_=size_class, k=max(1, min(k, 30)))
        return {"hits": hits, "kit": str(kit_path(project)), "count": len(hits)}

    def kit_report() -> dict[str, Any]:
        """Kit health: validation findings, modules with defaulted (unresolved) metadata, socket types, tags, size classes."""
        kit, findings = load_kit(project)
        return {"findings": findings, **K.report(kit), "kit": str(kit_path(project))}

    def search_library(query: str = "", kind: str | None = None, tags: list[str] | None = None, k: int = 5) -> dict[str, Any]:
        """Search curated reference scenes/modules (and 'avoid' examples) in the reference library."""
        return retrieval.search(store, query, filters={"kind": kind} if kind else None, tags=tags or (), k=max(1, min(k, 20)), embedder=default_embedder(project))

    # --- inspect / check ------------------------------------------------------------------------------------------------------------------
    def inspect_scene(scene: dict | None = None, scene_name: str | None = None, version: int | None = None) -> dict[str, Any]:
        """Summary of a scene: region, constraints, instances per module, locked items, metrics and findings."""
        kit, sc = kit_(), resolve(scene, scene_name, version)
        res = compose.measure(sc, kit, baseline_lock(sc))
        return {"id": sc["id"], "region": sc["region"], "instances": len(sc["instances"]), "module_counts": res["module_counts"],
                "locked": sorted(SC.lock_snapshot(sc)), "paths": [p["id"] for p in sc["paths"]], "corridors": [c.get("id") for c in sc["corridors"]],
                "focal": [f["id"] for f in sc["focal"]], "metrics": res["metrics"], "findings": res["findings"], "scene_hash": SC.scene_hash(sc),
                "history": [h["version"] for h in versions.history(f"scene-{sc['id']}")], "kit": str(kit_path(project))}

    def check_collisions_and_clearance(scene: dict | None = None, scene_name: str | None = None, version: int | None = None, spacing: float = 0.0) -> dict[str, Any]:
        """Collisions between solid modules (with height overlap), region/exclusion violations, blocked functional paths, blocked eye-height sightlines.
        spacing adds a required gap between solid footprints."""
        kit, sc = kit_(), resolve(scene, scene_name, version)
        findings = SC.validate(sc, kit, spacing=spacing)
        return {"ok": not any(f["severity"] == "error" for f in findings), "findings": findings, "instances": len(sc["instances"])}

    def check_locked_constraints(scene: dict | None = None, scene_name: str | None = None, version: int | None = None, baseline_version: int = 1) -> dict[str, Any]:
        """Compare locked instances with the baseline revision (default 1). Any difference is an error."""
        sc = resolve(scene, scene_name, version)
        lock = baseline_lock(sc, baseline_version)
        if lock is None:
            return {"checked": False, "reason": f"no saved revision {baseline_version} of '{sc['id']}'"}
        diffs = SC.check_locks(sc, lock)
        return {"checked": True, "locked": len(lock), "ok": not diffs, "differences": diffs}

    def validate_composition(scene: dict | None = None, scene_name: str | None = None, version: int | None = None,
                             manual_scores: dict | None = None) -> dict[str, Any]:
        """Density, repetition, colour spread, negative space, focal points, size hierarchy, collisions, clearances, locks and unresolved metadata,
        scored against the rubric. Manual criteria (reads_as_lived_in, matches_style, supports_gameplay) stay unscored unless you pass manual_scores
        after actually looking at the scene."""
        kit, sc = kit_(), resolve(scene, scene_name, version)
        style = load_style(project)
        res = compose.measure(sc, kit, baseline_lock(sc))
        rng = check_ranges(res["metrics"], style["ranges"])
        flagged = {f["metric"] for f in rng if f["severity"] in ("warning", "error")}
        structural = [f for f in res["findings"] if f["code"] in ("unknown_module", "bad_region", "bad_id", "duplicate_id", "scale_out_of_range", "bad_position")]
        has_locks = bool(SC.lock_snapshot(sc))
        auto = {"valid_scene": not structural,
                "no_collisions": not ({"collisions", "outside_region"} & flagged), "paths_clear": "blocked_paths" not in flagged,
                "sightlines_clear": "blocked_sightlines" not in flagged,
                "locks_respected": ("locked_violations" not in flagged) if (baseline_lock(sc) or not has_locks) else None,
                "density_ok": "coverage" not in flagged, "variety_ok": not ({"max_module_share", "adjacent_same_ratio"} & flagged),
                "color_ok": "color_balance" not in flagged, "negative_space_ok": "largest_free_rect_fraction" not in flagged,
                "focal_ok": "focal_satisfied" not in flagged, "hierarchy_ok": "size_classes_present" not in flagged,
                "metadata_resolved": "unresolved_modules" not in flagged}
        return {"metrics": res["metrics"], "range_findings": [f for f in rng if f["severity"] != "info"], "findings": res["findings"],
                "rubric": score_rubric(load_rubric(project.root / "evals" / "rubric.yaml"), auto=auto, manual=manual_scores), "module_counts": res["module_counts"]}

    # --- placement ------------------------------------------------------------------------------------------------------------------------
    def place_module(module: str, at: list[float], scene: dict | None = None, scene_name: str | None = None, yaw: float = 0.0, scale: float = 1.0,
                     y: float | None = None, snap_to_grid: bool = True, instance_id: str | None = None, why: str = "", dry_run: bool = True) -> dict[str, Any]:
        """Place ONE approved module. Snaps to the scene grid and yaw step, then checks region, exclusions, collisions, paths and sightlines.
        Returns a plan with findings; dry_run=false saves a new scene version if (and only if) the result has no errors."""
        kit, sc = kit_(), resolve(scene, scene_name, None)
        if len(at) < 2:
            raise ValueError("at must be [x, z]")
        inst = dress.make_instance(sc, kit, module, at, yaw=yaw, scale=scale, y=y, iid=instance_id, snap=snap_to_grid, why=why)
        return apply_and_maybe_save(sc, kit, {"adds": [inst], "moves": [], "removes": []}, dry_run, f"place {module}")

    def snap_to_socket(target_id: str, target_socket: str, module: str, module_socket: str, scene: dict | None = None, scene_name: str | None = None,
                       scale: float = 1.0, instance_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Place a module so its socket coincides with an existing instance's socket (chair to table, item to a surface, wall end to wall end).
        Socket types must be mutually compatible; orientation follows the socket's `orient` (opposed or same)."""
        kit, sc = kit_(), resolve(scene, scene_name, None)
        inst = dress.snap_to_socket(sc, kit, target_id, target_socket, module, module_socket, scale=scale, iid=instance_id)
        return apply_and_maybe_save(sc, kit, {"adds": [inst], "moves": [], "removes": []}, dry_run, f"snap {module}.{module_socket}")

    def apply_scene_plan(plan: dict, scene: dict | None = None, scene_name: str | None = None, dry_run: bool = True, label: str = "") -> dict[str, Any]:
        """Apply a reviewed plan {adds, moves, removes}. Locked instances cannot be moved or removed. Use undo_last_change to revert a saved plan."""
        kit, sc = kit_(), resolve(scene, scene_name, None)
        for k in ("adds", "moves", "removes"):
            plan.setdefault(k, [])
        return apply_and_maybe_save(sc, kit, plan, dry_run, label or "plan")

    def dress_region(rules: dict | None = None, seed: int = 0, scene: dict | None = None, scene_name: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Seeded, rule-driven dressing of the scene's region. rules: density (target coverage), include_tags/include_modules/exclude_tags, weights {tag|module: w},
        orient (random90|face_wall|face_focal|fixed), spacing, max_items, max_share, cluster {count, radius}, focal_first. Deterministic for a seed; different seeds
        are controlled variations that respect the same constraints. Returns the plan with an explanation per placement and why candidates were rejected."""
        kit, sc = kit_(), resolve(scene, scene_name, None)
        res = dress.dress_region(sc, kit, rules or {}, seed)
        applied = apply_and_maybe_save(sc, kit, res["plan"], dry_run, f"dress seed {seed}")
        return {"dry_run": dry_run, "explain": res["explain"], "rejected": res["rejected"], "coverage_after": res["coverage_after"], "attempts": res["attempts_used"],
                "seed": seed, "findings": applied["findings"], "ok": applied["ok"], "saved": applied.get("saved"), "adds": len(res["plan"]["adds"])}

    def undo_last_change(scene_name: str, dry_run: bool = True) -> dict[str, Any]:
        """Restore the previous saved version of a scene (saved as a new version, so history is never lost). Locked instances are checked against the baseline."""
        hist = versions.history(f"scene-{scene_name}")
        if len(hist) < 2:
            raise ValueError(f"scene '{scene_name}' has no earlier version to restore")
        prev = json.loads(versions.load(f"scene-{scene_name}", hist[-2]["version"]))
        cur = json.loads(versions.load(f"scene-{scene_name}", hist[-1]["version"]))
        out = {"dry_run": dry_run, "restores_version": hist[-2]["version"], "diff": SC.diff(SC.normalize(cur), SC.normalize(prev))}
        if not dry_run:
            out["saved"] = {"version": save(SC.normalize(prev), f"undo to v{hist[-2]['version']}")["version"]}
        return out

    def save_scene_variant(scene: dict | None = None, scene_name: str | None = None, name: str = "", dry_run: bool = True) -> dict[str, Any]:
        """Save a named variant (separate history) of the scene, e.g. 'night' or 'seed_7', so variations can be compared."""
        kit, sc = kit_(), resolve(scene, scene_name, None)
        must_be_valid(sc, kit)
        if not export.ID_RE.match(name or ""):
            raise ValueError("variant name must match [A-Za-z][A-Za-z0-9_]*")
        out = {"dry_run": dry_run, "variant": name, "scene_hash": SC.scene_hash(sc)}
        if not dry_run:
            saved = versions.save(f"variant-{sc['id']}-{name}", json.dumps(sc, indent=2, sort_keys=True), label=name)
            out["version"] = saved["version"]
        return out

    def diff_scenes(a_name: str, b_name: str, a_version: int | None = None, b_version: int | None = None) -> dict[str, Any]:
        """Diff two saved scenes/versions: added, removed and changed instances."""
        a = SC.normalize(json.loads(versions.load(f"scene-{a_name}", a_version)))
        b = SC.normalize(json.loads(versions.load(f"scene-{b_name}", b_version)))
        return SC.diff(a, b)

    # --- export / Studio ---------------------------------------------------------------------------------------------------------------
    def export_scene(scene: dict | None = None, scene_name: str | None = None, version: int | None = None, format: str = "luau", mode: str = "primitives",
                     target_path: str = "workspace", kit_path_: str = "ReplicatedStorage.Kit", dry_run: bool = True) -> dict[str, Any]:
        """Export for an engine. format=json is engine-neutral (world transforms). format=luau generates Roblox code: mode=primitives builds
        boxes sized from kit footprints; mode=clone clones the kit's `template` models from kit_path_. The Luau is forwarded to Studio's execute_luau.
        dry_run=true returns a summary only; false returns the code and writes it under the output directory."""
        kit, sc = kit_(), resolve(scene, scene_name, version)
        must_be_valid(sc, kit)
        errs = [f for f in SC.validate(sc, kit) if f["severity"] == "error"]
        if errs:
            raise ValueError("refusing to export a scene with errors: " + "; ".join(f["message"] for f in errs[:3]))
        if format not in ("json", "luau"):
            raise ValueError("format must be 'json' or 'luau'")
        summary = {"scene": sc["id"], "scene_hash": SC.scene_hash(sc), "instances": len(sc["instances"]), "format": format, "mode": mode if format == "luau" else None}
        steps = Plan(f"Export scene '{sc['id']}' as {format}")
        steps.add("file", "workspace/output/export", f"{safe_name(sc['id'])}.{ 'json' if format == 'json' else 'luau'}")
        if format == "luau":
            export.validate_path(target_path)
            steps.add("studio", f"{target_path}.AI_SetDress_{sc['id']}", "run the Luau with execute_luau (Edit datamodel)")
        if dry_run:
            return {**steps.to_dict(True), "summary": summary}
        out_dir = resolve_inside(project.output_dir / "export", project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        if format == "json":
            data = export.to_json(sc, kit)
            (out_dir / f"{safe_name(sc['id'])}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            return {**steps.to_dict(False), "summary": summary, "data": data, "path": str(out_dir / f"{safe_name(sc['id'])}.json")}
        code = export.build_luau(sc, kit, target_path=target_path, mode=mode, kit_path=kit_path_)
        path = out_dir / f"{safe_name(sc['id'])}.luau"
        path.write_text(code, encoding="utf-8")
        return {**steps.to_dict(False), "summary": summary, "luau": code, "luau_path": str(path),
                "studio": {"forward_to": "Roblox Studio's built-in MCP server", "tool": "execute_luau", "datamodel_type": "Edit", "studio_id": "from list_roblox_studios",
                           "code_argument": "see execute_luau's input schema"}}

    def simulate_scene(scene: dict | None = None, scene_name: str | None = None, version: int | None = None, mode: str = "primitives",
                       target_path: str = "workspace") -> dict[str, Any]:
        """Run the generated Luau on a mock DataModel (needs the optional 'lupa' package). In clone mode a stub kit is created from the module templates.
        Catches bad properties/types/templates before Studio; cannot prove Studio accepts the code."""
        from .domain.mock_roblox import MockRoblox

        kit, sc = kit_(), resolve(scene, scene_name, version)
        code = export.build_luau(sc, kit, target_path=target_path, mode=mode)
        mock = MockRoblox(api.load())
        if "." in target_path:
            mock.make_folder(target_path.split(".", 1)[1])
        if mode == "clone":
            stub = mock.lua.eval("function(ws, names) local rs = Instance.new('Folder'); rs.Name = 'Kit'; local f = Instance.new('Folder'); f.Name = 'ReplicatedStorage'; f.Parent = ws; rs.Parent = f; for _, n in pairs(names) do local m = Instance.new('Model'); m.Name = n; m.Parent = rs end end")
            stub(mock.env.workspace, mock.lua.table_from(sorted({m["template"] for m in kit["modules"] if m.get("template")})))
            code = code.replace('resolveTarget("ReplicatedStorage.Kit")', 'resolveTarget("workspace.ReplicatedStorage.Kit")')
        try:
            report = json.loads(mock.run(code))
        except Exception as exc:
            return {"ok": False, "error": str(exc).splitlines()[0], "mock": True}
        return {"ok": True, "report": report, "expected_instances": len(sc["instances"]), "mock": True}

    def inspect_placed(scene_id: str, target_path: str = "workspace", studio_report: str | None = None, scene: dict | None = None,
                       scene_name: str | None = None, version: int | None = None) -> dict[str, Any]:
        """Read back a primitives-mode scene from Studio. Without studio_report: read-only Luau. With studio_report and the scene: missing, extra and moved items."""
        if studio_report is None:
            return {"mode": "script", "luau": export.build_inspect_luau(scene_id, target_path)}
        from .domain.studio import parse_report

        kit, sc = kit_(), resolve(scene, scene_name, version)
        return {"mode": "compare", **export.compare_built(sc, kit, parse_report(studio_report))}

    def parse_studio_report(text: str) -> dict[str, Any]:
        """Parse what a generated script returned/printed (JSON, possibly with log lines)."""
        from .domain.studio import parse_report

        rep = parse_report(text)
        return {"status": rep.get("status"), "root": rep.get("root"), "instances": rep.get("instances"), "scene_hash": rep.get("scene_hash"), "raw_keys": sorted(rep)}

    def remove_scene_script(scene_id: str, target_path: str = "workspace") -> dict[str, Any]:
        """Luau that removes a scene this tool created (refuses anything it did not create)."""
        return {"luau": export.build_remove_luau(scene_id, target_path)}

    def render_scene_map(scene: dict | None = None, scene_name: str | None = None, version: int | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Plan-view PNG: footprints by module, paths, sightline corridors, focal radii, locked pieces, problems outlined red. dry_run=false writes it."""
        kit, sc = kit_(), resolve(scene, scene_name, version)
        path = resolve_inside(project.output_dir / "maps" / f"{safe_name(sc['id'])}.png", project.allowed_roots)
        if dry_run:
            return {"dry_run": True, "would_write": str(path)}
        return {"dry_run": False, "png": mapview.render(sc, kit, path)}

    ro = dict(read_only=True)
    return [
        ToolSpec("search_kit", search_kit, search_kit.__doc__, **ro),
        ToolSpec("kit_report", kit_report, kit_report.__doc__, **ro),
        ToolSpec("search_scene_library", search_library, search_library.__doc__, **ro),
        ToolSpec("inspect_scene", inspect_scene, inspect_scene.__doc__, **ro),
        ToolSpec("check_collisions_and_clearance", check_collisions_and_clearance, check_collisions_and_clearance.__doc__, **ro),
        ToolSpec("check_locked_constraints", check_locked_constraints, check_locked_constraints.__doc__, **ro),
        ToolSpec("validate_composition", validate_composition, validate_composition.__doc__, **ro),
        ToolSpec("diff_scenes", diff_scenes, diff_scenes.__doc__, **ro),
        ToolSpec("simulate_scene", simulate_scene, simulate_scene.__doc__, **ro),
        ToolSpec("inspect_placed", inspect_placed, inspect_placed.__doc__, **ro),
        ToolSpec("parse_studio_report", parse_studio_report, parse_studio_report.__doc__, **ro),
        ToolSpec("remove_scene_script", remove_scene_script, remove_scene_script.__doc__, **ro),
        ToolSpec("place_module", place_module, place_module.__doc__, read_only=False, idempotent=True),
        ToolSpec("snap_to_socket", snap_to_socket, snap_to_socket.__doc__, read_only=False, idempotent=True),
        ToolSpec("apply_scene_plan", apply_scene_plan, apply_scene_plan.__doc__, read_only=False, idempotent=True),
        ToolSpec("dress_region", dress_region, dress_region.__doc__, read_only=False, idempotent=True),
        ToolSpec("undo_last_change", undo_last_change, undo_last_change.__doc__, read_only=False, idempotent=True),
        ToolSpec("save_scene_variant", save_scene_variant, save_scene_variant.__doc__, read_only=False, idempotent=True),
        ToolSpec("export_scene", export_scene, export_scene.__doc__, read_only=False, idempotent=True),
        ToolSpec("render_scene_map", render_scene_map, render_scene_map.__doc__, read_only=False, idempotent=True),
    ]
