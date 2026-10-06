"""MCP tools for the Roblox level-design assistant.

The server never talks to Studio. It turns design briefs into structured level specs, analyses them exactly
(connectivity, routes, loops, choke points, sightlines, pacing, fairness, spawn safety), builds deterministic
blockouts, checks the built geometry is walkable, and generates safe Luau that an agent forwards to Studio's own MCP
server. Write tools default to dry_run=true; nothing here publishes or overwrites live content.
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
from .core.safety import Plan, Versioner, resolve_inside, safe_name
from .core.style import load_style
from .domain import api, build, evaluate, graphs, mapview, sight, spec as S, walkcheck


def _style(project: Project) -> dict:
    return load_style(project)


def _template(project: Project, template_id: str) -> dict:
    for f in sorted((project.root / "recipes").glob("*.yaml")):
        t = S.load_template(f)
        if t["id"] == template_id:
            return t
    raise ValueError(f"unknown template '{template_id}'. Available: {[t['id'] for t in S.list_templates(project.root / 'recipes')]}")


def make_tools(project: Project) -> list[ToolSpec]:
    store = LibraryStore(project)
    versions = Versioner(project)

    def resolve(spec: dict | None, level_name: str | None, version: int | None, template_id: str | None = None,
                params: dict | None = None) -> dict:
        given = [x for x in (spec, level_name, template_id) if x is not None]
        if len(given) != 1:
            raise ValueError("pass exactly one of: spec, level_name (a saved revision), or template_id")
        if spec is not None:
            return spec
        if level_name is not None:
            return json.loads(versions.load(f"level-{level_name}", version))
        return S.instantiate(_template(project, template_id), params)

    def prepared(spec: dict | None, level_name: str | None, version: int | None, template_id: str | None = None,
                 params: dict | None = None) -> tuple[dict, list[dict]]:
        raw = resolve(spec, level_name, version, template_id, params)
        norm, findings = S.prepare(raw, _style(project))
        findings += build.check_ids(norm)
        norm["player_given"] = dict(raw.get("player", {}))
        return norm, findings

    def must_be_valid(norm: dict, findings: list[dict]) -> None:
        errors = [f for f in findings if f["severity"] == "error"]
        if errors:
            raise ValueError("level spec is invalid: " + "; ".join(f"[{e['code']}] {e['message']}" for e in errors[:6]))

    def baseline(norm: dict, baseline_version: int = 1) -> dict | None:
        try:
            old = json.loads(versions.load(f"level-{norm['id']}", baseline_version))
        except FileNotFoundError:
            return None
        n, _ = S.prepare(old, _style(project))
        return build.lock_snapshot(n)

    # --- discovery ------------------------------------------------------------------------------------------------------------
    def list_level_templates() -> dict[str, Any]:
        """Level templates with parameters and ranges. A template plus parameters yields a valid starting spec."""
        return {"templates": S.list_templates(project.root / "recipes")}

    def search_level_library(query: str = "", level_type: str | None = None, tags: list[str] | None = None, k: int = 5) -> dict[str, Any]:
        """Find curated reference levels (and 'avoid' examples) by text, type (arena/hub/linear/...) and tags."""
        filters: dict[str, Any] = {"kind": "level"}
        if level_type:
            filters["domain.level_type"] = level_type
        return retrieval.search(store, query, filters=filters, tags=tags or (), k=max(1, min(k, 20)), embedder=default_embedder(project))

    # --- spec ----------------------------------------------------------------------------------------------------------------------
    def create_level_spec(template_id: str | None = None, params: dict | None = None, spec: dict | None = None,
                          name: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Create a normalised, validated level spec from a template (+parameters) or from a spec you wrote. Returns the
        spec, findings, assumptions and open questions. dry_run=false also saves it as revision 1 (the baseline for locks)."""
        raw = resolve(spec, None, None, template_id, params)
        if name:
            raw = {**raw, "id": name}
        norm, findings = S.prepare(raw, _style(project))
        findings += build.check_ids(norm)
        norm["player_given"] = dict(raw.get("player", {}))
        result: dict[str, Any] = {"valid": not S.has_errors(findings), "findings": [f for f in findings if f["severity"] != "info"],
                                  "notes": [f["message"] for f in findings if f["severity"] == "info"],
                                  "assumptions": evaluate.assumptions(norm, _style(project)), "spec": norm, "dry_run": dry_run}
        if not dry_run:
            must_be_valid(norm, findings)
            saved = versions.save(f"level-{norm['id']}", json.dumps({k: v for k, v in norm.items() if k != "player_given"}, indent=2, sort_keys=True), label="created")
            result["revision"] = saved["version"]
        return result

    def save_level_revision(spec: dict | None = None, level_name: str | None = None, label: str = "", dry_run: bool = True) -> dict[str, Any]:
        """Save a spec as a new revision and show what changed against the previous one (rooms/connections added, removed, moved).
        dry_run=true shows the diff only. Locked constraints are checked against revision 1 before anything is written."""
        norm, findings = prepared(spec, level_name, None)
        must_be_valid(norm, findings)
        name = f"level-{norm['id']}"
        hist = versions.history(name)
        diff = None
        if hist:
            diff = build.diff_specs(json.loads(versions.load(name)), norm)
        lock = baseline(norm)
        lock_findings = build.check_locks(norm, lock) if lock else []
        out = {"dry_run": dry_run, "history": [h["version"] for h in hist], "diff_vs_latest": diff, "lock_findings": lock_findings}
        if lock_findings:
            out["blocked"] = "locked constraints would change; revise the spec or deliberately start a new baseline"
            return out
        if not dry_run:
            saved = versions.save(name, json.dumps({k: v for k, v in norm.items() if k != "player_given"}, indent=2, sort_keys=True), label=label)
            out["saved"] = {"version": saved["version"], "unchanged": saved["unchanged"]}
        return out

    def diff_level_specs(a_name: str, b_name: str, a_version: int | None = None, b_version: int | None = None) -> dict[str, Any]:
        """Diff two saved revisions (rooms, connections, spawns, objectives, landmarks, encounters, bounds)."""
        a = json.loads(versions.load(f"level-{a_name}", a_version))
        b = json.loads(versions.load(f"level-{b_name}", b_version))
        return build.diff_specs(a, b)

    # --- analysis ----------------------------------------------------------------------------------------------------------------------
    def validate_connectivity(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                              template_id: str | None = None, params: dict | None = None) -> dict[str, Any]:
        """Components, unreachable rooms, dead ends, and for each spawn->objective pair the shortest route, independent routes
        and choke-point rooms. Pure graph computation over the spec."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        g = graphs.analyze(norm, _style(project))
        return {"connected": len(g["components"]) == 1, "components": g["components"], "unreachable": g["unreachable"],
                "dead_ends": g["dead_ends"], "findings": g["findings"], "routes": g["routes"]}

    def analyze_routes_and_loops(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                                 template_id: str | None = None, params: dict | None = None, ratio: float = 1.5) -> dict[str, Any]:
        """Route choice: loops (independent cycles), alternates within `ratio` of the shortest route, independent routes,
        choke points, team fairness, and spawn separation."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        g = graphs.analyze(norm, _style(project))
        alts = [graphs.alternates(norm, s["id"], o["id"], ratio) for s in norm["spawns"] for o in norm["objectives"]]
        return {"metrics": g["metrics"], "spawn_separation": graphs.spawn_separation(norm), "routes": g["routes"], "alternates": alts,
                "findings": g["findings"]}

    def check_sightlines(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                         template_id: str | None = None, params: dict | None = None) -> dict[str, Any]:
        """Plan-view line of sight at eye height: longest sightline per room, landmark visibility along each spawn's route,
        stretches with no landmark in view, and enemy-spawn exposure. Floors/ceilings/props do not occlude."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        return sight.analyze(norm, _style(project))

    def evaluate_level(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                       template_id: str | None = None, params: dict | None = None, manual_scores: dict | None = None,
                       baseline_version: int = 1) -> dict[str, Any]:
        """Everything at once: validation, routes/loops, sightlines, pacing, fairness, spawn safety, the built geometry's
        walkability, locked constraints (vs revision `baseline_version`), part budget, and the rubric. Manual criteria
        (fun_and_flow, matches_brief, readable_at_player_height) stay unscored unless you pass manual_scores after actually
        walking the review views. Returns assumptions and open questions too."""
        raw = resolve(spec, level_name, version, template_id, params)
        norm0, _ = S.prepare(raw, _style(project))
        res = evaluate.evaluate(raw, _style(project), project.root / "evals" / "rubric.yaml", baseline_lock=baseline(norm0, baseline_version) if norm0.get("id") else None,
                                manual=manual_scores)
        norm = res.pop("spec")
        res.pop("sight", None)
        res.pop("routes", None)
        res["assumptions"] = evaluate.assumptions({**norm, "player_given": dict(raw.get("player", {}))}, _style(project))
        res["pacing"] = res.get("pacing", {}).get("timeline", [])
        return res

    def check_locked_constraints(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                                 baseline_version: int = 1) -> dict[str, Any]:
        """Compare a spec with the locked geometry of a baseline revision (default: revision 1). Any difference is an error."""
        norm, findings = prepared(spec, level_name, version)
        must_be_valid(norm, findings)
        lock = baseline(norm, baseline_version)
        if lock is None:
            return {"checked": False, "reason": f"no saved revision {baseline_version} of '{norm['id']}'; save one with create_level_spec(dry_run=false) first"}
        diffs = build.check_locks(norm, lock)
        return {"checked": True, "baseline_version": baseline_version, "locked": {k: len(v) for k, v in lock.items() if isinstance(v, dict)},
                "ok": not diffs, "differences": diffs}

    # --- views ---------------------------------------------------------------------------------------------------------------------------------
    def capture_review_views(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                             template_id: str | None = None, params: dict | None = None, step: float = 24.0, max_views: int = 24) -> dict[str, Any]:
        """Camera positions/look-at points at PLAYER EYE HEIGHT along the main route, landmark checks, room corners and a top-down
        overview, for Studio's screen_capture. Review the route views first: a plan view alone is not a review."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        views = sight.review_views(norm, max(8.0, step), max(4, min(max_views, 60)))
        return {"views": views, "how_to_capture": "Build the blockout, then call Studio's screen_capture once per view (custom camera position + look-at). "
                                                   "Walk the route_* views in order, then landmark_*, then rooms; use overview last.",
                "count": len(views)}

    def render_level_map(spec: dict | None = None, level_name: str | None = None, version: int | None = None, template_id: str | None = None,
                         params: dict | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Top-down plan PNG with rooms, doors, stairs/ramps, spawns, objectives, landmarks, routes and landmark line-of-sight dots.
        A diagnostic, not a review. dry_run=false writes the PNG."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        path = resolve_inside(project.output_dir / "maps" / f"{safe_name(norm['id'])}.png", project.allowed_roots)
        if dry_run:
            return {"dry_run": True, "would_write": str(path)}
        return {"dry_run": False, "png": mapview.render(norm, path)}

    # --- build ---------------------------------------------------------------------------------------------------------------------------
    def build_blockout(spec: dict | None = None, level_name: str | None = None, version: int | None = None, template_id: str | None = None,
                       params: dict | None = None, target_path: str = "workspace", dry_run: bool = True) -> dict[str, Any]:
        """Deterministic blockout (floors, walls with door gaps, steps/ramps, spawns, objective/landmark markers, encounter zones).
        Validates the spec, verifies the built geometry is walkable, and checks locks against revision 1. dry_run=true returns the
        plan (part counts, bounding box, assumptions); dry_run=false also returns the Luau to forward to Studio's execute_luau."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        build.validate_target_path(target_path)
        style = _style(project)
        parts = build.build_parts(norm, style)
        walk = walkcheck.check(norm, parts)
        lock = baseline(norm)
        lock_findings = build.check_locks(norm, lock) if lock else []
        if lock_findings:
            raise ValueError("locked constraints changed: " + "; ".join(f["message"] for f in lock_findings[:4]))
        summary = {"id": norm["id"], "root": f"AI_Blockout_{norm['id']}", "spec_hash": build.spec_hash(norm), "parts": len(parts),
                   "parts_by_group": build.part_count_by_group(parts), "bounding_box": build.bounding_box(parts),
                   "walkable": walk["ok"], "geometry_findings": walk["findings"], "assumptions": evaluate.assumptions({**norm, "player_given": {}}, style),
                   "open_questions": norm.get("open_questions", [])}
        steps = Plan(f"Build blockout '{norm['id']}' ({len(parts)} parts) under {target_path}")
        steps.add("studio", f"{target_path}.{summary['root']}", "run the Luau with execute_luau (Edit datamodel)")
        steps.add("file", "workspace/output/luau", f"{safe_name(norm['id'])}-{summary['spec_hash']}.luau")
        if not walk["ok"]:
            steps.warnings.append("the built geometry is NOT walkable from a spawn to an objective; do not send it to Studio")
        if dry_run:
            return {**steps.to_dict(True), "plan": summary}
        if not walk["ok"]:
            raise ValueError("refusing to generate code: built geometry is not walkable: " + "; ".join(f["message"] for f in walk["findings"] if f["severity"] == "error"))
        code = build.build_luau(norm, parts, target_path, style)
        out_dir = resolve_inside(project.output_dir / "luau", project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{safe_name(norm['id'])}-{summary['spec_hash']}.luau"
        path.write_text(code, encoding="utf-8")
        return {**steps.to_dict(False), "plan": summary, "luau": code, "luau_path": str(path),
                "studio": {"forward_to": "Roblox Studio's built-in MCP server", "tool": "execute_luau", "datamodel_type": "Edit",
                           "studio_id": "from list_roblox_studios", "code_argument": "see execute_luau's input schema"}}

    def verify_blockout_geometry(spec: dict | None = None, level_name: str | None = None, version: int | None = None,
                                 template_id: str | None = None, params: dict | None = None) -> dict[str, Any]:
        """Flood-fill the GENERATED geometry with the configured character (height, max step): can each spawn walk to each objective,
        and can every room be entered? Catches builder bugs a graph check cannot (blocked doors, too-tall steps)."""
        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        return walkcheck.check(norm, build.build_parts(norm, _style(project)))

    def simulate_blockout(spec: dict | None = None, level_name: str | None = None, version: int | None = None, template_id: str | None = None,
                          params: dict | None = None, target_path: str = "workspace") -> dict[str, Any]:
        """Run the generated Luau on a mock DataModel (needs the optional 'lupa' package) to catch bad classes, properties and value types before
        Studio, and report how many parts were created. A mock cannot prove Studio accepts the code."""
        from .domain.mock_roblox import simulate

        norm, findings = prepared(spec, level_name, version, template_id, params)
        must_be_valid(norm, findings)
        parts = build.build_parts(norm, _style(project))
        code = build.build_luau(norm, parts, target_path, _style(project))
        folder = target_path.split(".", 1)[1] if "." in target_path else None
        res = simulate(code, api.load(), target=folder)
        return {"ok": res["ok"], "error": res.get("error"), "report": res.get("report"), "expected_parts": len(parts), "mock": True}

    def inspect_blockout(spec_id: str, target_path: str = "workspace", studio_report: str | None = None,
                         spec: dict | None = None, level_name: str | None = None, version: int | None = None) -> dict[str, Any]:
        """Read back a built blockout. Without studio_report returns read-only Luau to run via execute_luau. With studio_report (its JSON
        output) plus the spec, compares what exists with what the spec wants: missing, extra and moved/resized parts (hand edits, drift)."""
        if studio_report is None:
            return {"mode": "script", "luau": build.build_inspect_luau(spec_id, target_path)}
        from .domain.studio import parse_report

        norm, findings = prepared(spec, level_name, version)
        must_be_valid(norm, findings)
        return {"mode": "compare", **build.compare_built(build.build_parts(norm, _style(project)), parse_report(studio_report))}

    def parse_studio_report(text: str) -> dict[str, Any]:
        """Parse what a generated script returned/printed (JSON, possibly with log lines). Errors are reported verbatim."""
        from .domain.studio import parse_report

        rep = parse_report(text)
        return {"status": rep.get("status"), "root": rep.get("root"), "parts": rep.get("parts"), "spec_hash": rep.get("spec_hash"), "raw_keys": sorted(rep)}

    def remove_blockout_script(spec_id: str, target_path: str = "workspace") -> dict[str, Any]:
        """Luau that removes a blockout this tool created (refuses anything it did not create)."""
        return {"luau": build.build_remove_luau(spec_id, target_path)}

    ro = dict(read_only=True)
    return [
        ToolSpec("list_level_templates", list_level_templates, list_level_templates.__doc__, **ro),
        ToolSpec("search_level_library", search_level_library, search_level_library.__doc__, **ro),
        ToolSpec("validate_connectivity", validate_connectivity, validate_connectivity.__doc__, **ro),
        ToolSpec("analyze_routes_and_loops", analyze_routes_and_loops, analyze_routes_and_loops.__doc__, **ro),
        ToolSpec("check_sightlines", check_sightlines, check_sightlines.__doc__, **ro),
        ToolSpec("evaluate_level", evaluate_level, evaluate_level.__doc__, **ro),
        ToolSpec("check_locked_constraints", check_locked_constraints, check_locked_constraints.__doc__, **ro),
        ToolSpec("diff_level_specs", diff_level_specs, diff_level_specs.__doc__, **ro),
        ToolSpec("capture_review_views", capture_review_views, capture_review_views.__doc__, **ro),
        ToolSpec("verify_blockout_geometry", verify_blockout_geometry, verify_blockout_geometry.__doc__, **ro),
        ToolSpec("simulate_blockout", simulate_blockout, simulate_blockout.__doc__, **ro),
        ToolSpec("inspect_blockout", inspect_blockout, inspect_blockout.__doc__, **ro),
        ToolSpec("parse_studio_report", parse_studio_report, parse_studio_report.__doc__, **ro),
        ToolSpec("remove_blockout_script", remove_blockout_script, remove_blockout_script.__doc__, **ro),
        ToolSpec("create_level_spec", create_level_spec, create_level_spec.__doc__, read_only=False, idempotent=True),
        ToolSpec("save_level_revision", save_level_revision, save_level_revision.__doc__, read_only=False, idempotent=True),
        ToolSpec("render_level_map", render_level_map, render_level_map.__doc__, read_only=False, idempotent=True),
        ToolSpec("build_blockout", build_blockout, build_blockout.__doc__, read_only=False, idempotent=True),
    ]
