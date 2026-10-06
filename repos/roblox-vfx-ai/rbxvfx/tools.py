"""MCP tools for the Roblox VFX assistant.

This server never talks to Roblox Studio. It retrieves references, builds and validates effect plans,
generates safe Luau, estimates budgets and readability, and records feedback. An agent forwards the Luau
to Studio's own MCP server (``execute_luau``). Nothing here publishes or overwrites a live project:
generated code replaces only the root it created itself (see ``domain/luau.py``).
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
from .core.style import load_style
from .domain import analysis, api, effects, luau, preview, studio

BUDGET_METRICS = {"particles_peak", "beam_segments_total", "trails", "lights", "screen_coverage"}


def _recipe(project: Project, recipe_id: str) -> dict:
    for f in sorted((project.root / "recipes").glob("*.yaml")):
        r = effects.load_recipe(f)
        if r["id"] == recipe_id:
            return r
    known = [r["id"] for r in effects.list_recipes(project.root / "recipes")]
    raise ValueError(f"unknown recipe '{recipe_id}'. Available: {known}")


def _plan(project: Project, recipe_id: str, params: dict | None, name: str | None) -> dict:
    return effects.build_plan(_recipe(project, recipe_id), params, name=name)


def make_tools(project: Project) -> list[ToolSpec]:
    store = LibraryStore(project)
    versions = Versioner(project)

    def list_effect_recipes() -> dict[str, Any]:
        """List effect recipes with kind, mount type, parameters, ranges and defaults."""
        return {"recipes": effects.list_recipes(project.root / "recipes"),
                "allowed_classes": list(api.ALLOWED_CLASSES)}

    def search_effect_library(query: str = "", effect_kind: str | None = None, tags: list[str] | None = None,
                              palette: list[str] | None = None, k: int = 5) -> dict[str, Any]:
        """Find curated effects and 'avoid' examples by text, kind (burst/loop/beam/trail/...), tags and palette.
        Hits include the recipe id and measured budget fields, so you can reuse how an effect was built."""
        filters: dict[str, Any] = {"kind": "effect"}
        if effect_kind:
            filters["domain.effect_kind"] = effect_kind
        return retrieval.search(store, query, filters=filters, tags=tags or (), palette=palette or (),
                                k=max(1, min(k, 20)), embedder=default_embedder(project))

    def validate_instance_tree(recipe_id: str | None = None, params: dict | None = None,
                               studio_report: str | None = None) -> dict[str, Any]:
        """Validate an effect against the Roblox API snapshot: classes, property names and types, enum members,
        sequence rules, ranges, attachments. Give recipe_id (+params) for an intended effect, or studio_report (the
        JSON returned by the inspect script) for what actually exists in Studio. Returns findings; never raises
        for invalid effects."""
        if (recipe_id is None) == (studio_report is None):
            raise ValueError("pass exactly one of recipe_id or studio_report")
        if recipe_id:
            try:
                plan = _plan(project, recipe_id, params, None)
            except ValueError as exc:
                return {"valid": False, "source": "recipe", "findings": [effects.finding("error", "build", str(exc))]}
            findings = effects.validate_plan(plan)
            return {"valid": not any(f["severity"] == "error" for f in findings), "source": "recipe", "findings": findings,
                    "instances": [i["id"] for i in plan["instances"]]}
        plan = luau.parse_inspection(studio.parse_report(studio_report))
        findings = effects.validate_plan(plan)
        return {"valid": not any(f["severity"] == "error" for f in findings), "source": "studio", "findings": findings,
                "instances": [i["id"] for i in plan["instances"]]}

    def check_performance_budget(recipe_id: str, params: dict | None = None, platform: str = "mobile",
                                 distance_studs: float | None = None) -> dict[str, Any]:
        """Estimate particle count, beam segments, trails, lights and screen coverage against the platform budget
        in style.yaml (mobile|desktop). These are heuristic estimates from the plan, not profiler measurements."""
        plan = _plan(project, recipe_id, params, None)
        res = analysis.analyze(plan, load_style(project), platform, distance_studs)
        return {"platform": platform, "metrics": {k: v for k, v in res["metrics"].items() if k in BUDGET_METRICS},
                "findings": [f for f in res["findings"] if f["metric"] in BUDGET_METRICS],
                "within_budget": not any(f["metric"] in BUDGET_METRICS for f in res["findings"]),
                "assumptions": res["assumptions"], "disclaimer": res["disclaimer"]}

    def evaluate_effect(recipe_id: str, params: dict | None = None, platform: str = "mobile",
                        distance_studs: float | None = None, manual_scores: dict | None = None) -> dict[str, Any]:
        """Score readability at gameplay distance, timing, colour separation against backgrounds, theme fit,
        fade-out and budget. Automatic criteria come from the plan; manual criteria (looks right in motion, fits the
        game's feel) stay unscored unless you pass manual_scores from actually watching the preview."""
        plan = _plan(project, recipe_id, params, None)
        style = load_style(project)
        res = analysis.analyze(plan, style, platform, distance_studs)
        bad = {f["metric"] for f in res["findings"]}
        auto = {
            "valid_tree": True,
            "within_budget": not (bad & BUDGET_METRICS),
            "readable_at_distance": "apparent_height_fraction" not in bad,
            "separates_from_background": "color_separation_min" not in bad,
            "does_not_obscure_gameplay": "screen_coverage" not in bad,
            "clean_timing": not (bad & {"attack_fraction", "duration_seconds", "abrupt_end"}),
            "on_theme": "theme_distance" not in bad,
        }
        rubric = score_rubric(load_rubric(project.root / "evals" / "rubric.yaml"), auto=auto, manual=manual_scores)
        return {"analysis": res, "rubric": rubric}

    def create_effect_from_recipe(recipe_id: str, params: dict | None = None, name: str | None = None,
                                  target_path: str = "workspace", position: list[float] | None = None,
                                  dry_run: bool = True) -> dict[str, Any]:
        """Build, validate and (when dry_run=false) generate the Luau that creates the effect under target_path in Studio.
        Forward the Luau to Studio's execute_luau (Edit). It replaces only an earlier effect root it created itself
        and refuses to touch anything else. dry_run=true returns the plan only (no code, no files)."""
        plan = _plan(project, recipe_id, params, name)
        luau.validate_target_path(target_path)  # a dry run must reject exactly what the real run would
        pos = tuple(position or (0, 5, 0))
        if len(pos) != 3:
            raise ValueError("position must be [x, y, z]")
        digest = effects.plan_hash(plan)
        summary = {"name": plan["name"], "root": plan["root_name"], "kind": plan["kind"], "plan_hash": digest,
                   "instances": [f"{i['id']} ({i['class']})" for i in plan["instances"]], "emit_counts": plan["emit"],
                   "parameters": plan["parameters"], "warnings": plan["warnings"], "target_path": target_path}
        steps = Plan(f"Create effect '{plan['name']}' under {target_path}")
        steps.add("studio", f"{target_path}.{plan['root_name']}", "run the generated Luau with execute_luau (Edit datamodel)")
        steps.add("file", "workspace/output/luau", f"{safe_name(plan['name'])}-{digest}.luau")
        steps.add("version", f"plan:{plan['name']}", "save the plan so parameters can be changed later")
        if dry_run:
            return {**steps.to_dict(True), "plan": summary}
        code = luau.build_create_script(plan, target_path, pos, emit_now=False)
        out_dir = resolve_inside(project.output_dir / "luau", project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{safe_name(plan['name'])}-{digest}.luau"
        path.write_text(code, encoding="utf-8")
        saved = versions.save(f"plan-{plan['name']}", json.dumps(plan, indent=2, sort_keys=True), label=f"recipe {recipe_id}")
        return {**steps.to_dict(False), "plan": summary, "luau": code, "luau_path": str(path), "plan_version": saved["version"],
                "studio": studio.studio_handoff(code, f"create {plan['root_name']}"),
                "next": "Run it in Studio, then call parse_studio_report with the result; then preview_effect."}

    def preview_effect(recipe_id: str, params: dict | None = None, name: str | None = None,
                       target_path: str = "workspace", position: list[float] | None = None, platform: str = "mobile",
                       dry_run: bool = True) -> dict[str, Any]:
        """Prepare a preview: Luau that builds the effect AND fires burst emitters once, camera setups (gameplay, close,
        top-down) for Studio's screen_capture, and an offline timeline sheet of size/transparency/colour curves.
        dry_run=true returns cameras and the analysis only; dry_run=false also writes the timeline PNG."""
        plan = _plan(project, recipe_id, params, name)
        luau.validate_target_path(target_path)
        pos = list(position or (0, 5, 0))
        if len(pos) != 3:
            raise ValueError("position must be [x, y, z]")
        res = analysis.analyze(plan, load_style(project), platform)
        cams = analysis.camera_setups(res, pos)
        out: dict[str, Any] = {"dry_run": dry_run, "cameras": cams, "findings": res["findings"],
                               "metrics": res["metrics"],
                               "how_to_capture": "Run the Luau, then call Studio's screen_capture once per camera "
                                                 "(custom camera position + look-at). Trails need motion to show.",
                               "note": "The timeline sheet is a plot of the plan's curves, not a render."}
        if not dry_run:
            code = luau.build_create_script(plan, target_path, tuple(pos), emit_now=True)
            png = preview.render_timeline(plan, resolve_inside(project.output_dir / "previews" / f"{safe_name(plan['name'])}.png",
                                                              project.allowed_roots))
            out.update({"luau": code, "timeline_png": png, "studio": studio.studio_handoff(code, "preview (emits bursts)")})
        return out

    def simulate_effect(recipe_id: str, params: dict | None = None, name: str | None = None,
                        target_path: str = "workspace") -> dict[str, Any]:
        """Run the generated Luau on a mock Roblox DataModel (needs the optional 'lupa' package) to catch bad
        property names, wrong value types and unsafe behaviour BEFORE sending code to Studio. A mock cannot prove
        Studio accepts the code; it proves the code is coherent against the documented API."""
        from .domain.mock_roblox import simulate

        plan = _plan(project, recipe_id, params, name)
        luau.validate_target_path(target_path)
        code = luau.build_create_script(plan, target_path, (0, 5, 0), emit_now=True)
        folder = target_path.split(".", 1)[1] if "." in target_path else None
        res = simulate(code, api.load(), target=folder)
        return {"ok": res["ok"], "error": res.get("error"), "report": res.get("report"), "emits": res.get("emits"),
                "printed": res.get("printed", [])[:3], "mock": True}

    def inspect_vfx(path: str, studio_report: str | None = None) -> dict[str, Any]:
        """Inspect an effect that exists in Studio. Without studio_report: returns read-only Luau to run via execute_luau.
        With studio_report (that script's JSON output): summarises the instance tree, validates it and analyses it."""
        if studio_report is None:
            code = luau.build_inspect_script(path)
            return {"mode": "script", "luau": code, "studio": studio.studio_handoff(code, f"inspect {path}", datamodel_type="Edit")}
        plan = luau.parse_inspection(studio.parse_report(studio_report))
        findings = effects.validate_plan(plan)
        res = analysis.analyze(plan, load_style(project), "mobile")
        return {"mode": "report", "instances": [f"{i['id']} ({i['class']}) <- {i['parent']}" for i in plan["instances"]],
                "findings": findings, "metrics": res["metrics"], "analysis_findings": res["findings"],
                "note": "Burst emit counts and effect kind cannot be read from Studio; budget numbers assume steady state."}

    def parse_studio_report(text: str) -> dict[str, Any]:
        """Parse what a generated script returned/printed (JSON, possibly mixed with log lines). Errors are reported verbatim."""
        report = studio.parse_report(text)
        return {"status": report.get("status"), "root": report.get("root"), "plan_hash": report.get("plan_hash"),
                "created": report.get("created", []), "raw_keys": sorted(report)}

    def remove_effect_script(name: str, target_path: str = "workspace") -> dict[str, Any]:
        """Luau that removes an effect root previously created by this tool (refuses anything it did not create)."""
        if not effects.NAME_RE.match(name):
            raise ValueError(f"effect name '{name}' must match {effects.NAME_RE.pattern}")
        code = luau.build_remove_script(f"AIEffect_{name}", target_path)
        return {"luau": code, "studio": studio.studio_handoff(code, f"remove AIEffect_{name}")}

    def save_effect_variant(recipe_id: str, params: dict | None = None, name: str = "", notes: str = "",
                            dry_run: bool = True) -> dict[str, Any]:
        """Save a named parameter set for a recipe as a version (so 'the bigger purple one' can be recalled and
        compared). dry_run=false writes it. To turn a result into a library reference, use record_run + promote_run."""
        if not name:
            raise ValueError("name is required")
        plan = _plan(project, recipe_id, params, name)
        variant = {"recipe": recipe_id, "name": plan["name"], "parameters": plan["parameters"], "notes": notes,
                   "plan_hash": effects.plan_hash(plan)}
        if dry_run:
            return {"dry_run": True, "variant": variant}
        saved = versions.save(f"variant-{plan['name']}", json.dumps(variant, indent=2, sort_keys=True), label=notes[:60])
        return {"dry_run": False, "variant": variant, "version": saved["version"], "unchanged": saved["unchanged"]}

    ro = dict(read_only=True)
    return [
        ToolSpec("list_effect_recipes", list_effect_recipes, list_effect_recipes.__doc__, **ro),
        ToolSpec("search_effect_library", search_effect_library, search_effect_library.__doc__, **ro),
        ToolSpec("validate_instance_tree", validate_instance_tree, validate_instance_tree.__doc__, **ro),
        ToolSpec("check_performance_budget", check_performance_budget, check_performance_budget.__doc__, **ro),
        ToolSpec("evaluate_effect", evaluate_effect, evaluate_effect.__doc__, **ro),
        ToolSpec("inspect_vfx", inspect_vfx, inspect_vfx.__doc__, **ro),
        ToolSpec("parse_studio_report", parse_studio_report, parse_studio_report.__doc__, **ro),
        ToolSpec("remove_effect_script", remove_effect_script, remove_effect_script.__doc__, **ro),
        ToolSpec("simulate_effect", simulate_effect, simulate_effect.__doc__, **ro),
        ToolSpec("create_effect_from_recipe", create_effect_from_recipe, create_effect_from_recipe.__doc__,
                 read_only=False, idempotent=True),
        ToolSpec("preview_effect", preview_effect, preview_effect.__doc__, read_only=False, idempotent=True),
        ToolSpec("save_effect_variant", save_effect_variant, save_effect_variant.__doc__, read_only=False, idempotent=True),
    ]
