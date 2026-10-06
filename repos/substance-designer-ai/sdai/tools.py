"""MCP tools for the Designer assistant.

Read-only tools inspect and validate. Tools that write (scripts, plan versions) default to
``dry_run=true`` and only write inside the allowed directories (``SDAI_ALLOWED_PATHS``,
default ``workspace/``). Nothing here edits a Designer session directly: scripts are generated for
review and run inside Designer by the user or by an agent with application access.
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
from .core.rubric import load_rubric
from .core.safety import Plan, Versioner, resolve_inside, safe_name
from .core.style import load_style
from .domain import graphspec, materials, render, sbsinfo, scriptgen
from .domain.catalog import diff_against_probe, load_catalog, load_probe


def _catalog(project: Project):
    return load_catalog(project.root / "recipes" / "node_catalog.yaml")


def _recipe(project: Project, recipe_id: str) -> dict:
    for f in sorted((project.root / "recipes").glob("*.yaml")):
        if f.name != "node_catalog.yaml":
            r = graphspec.load_recipe(f)
            if r["id"] == recipe_id:
                return r
    known = [r["id"] for r in graphspec.list_recipes(project.root / "recipes")]
    raise ValueError(f"unknown recipe '{recipe_id}'. Available: {known}")


def _required_outputs(project: Project) -> tuple:
    return tuple(load_style(project).get("required_maps", ()))


def probe_path(project: Project) -> Path:
    return Path(project.env("PROBE_RESULT") or project.workspace / "probe" / "probe_result.json")


def make_tools(project: Project) -> list[ToolSpec]:
    store = LibraryStore(project)
    versions = Versioner(project)

    def inspect_environment() -> dict[str, Any]:
        """Report what this machine can actually do: Designer CLI tools found, whether the catalog has been
        verified against a real Designer (probe result), recipes available, and optional dependencies."""
        catalog = _catalog(project)
        probe = load_probe(probe_path(project))
        diff = diff_against_probe(catalog, probe) if probe else None
        return {
            "designer_cli": {"sbscooker": render.find_tool(project, "sbscooker", "SBSCOOKER"),
                             "sbsrender": render.find_tool(project, "sbsrender", "SBSRENDER")},
            "catalog": {"nodes": len(catalog), "verified_flags": sum(n.verified for n in catalog.values()),
                        "probe_result_found": probe is not None,
                        "probe_clean": None if diff is None else diff["clean"],
                        "designer_version": None if probe is None else probe.get("designer_version")},
            "recipes": [r["id"] for r in graphspec.list_recipes(project.root / "recipes")],
            "can_build_graphs_in_designer": "only by running a generated script inside Designer (no live connection)",
            "next_step": ("Run `python -m sdai probe-script`, execute it inside Designer, then `python -m sdai catalog-diff <result>`"
                          if probe is None else "Resolve any catalog-diff problems, then build."),
        }

    def list_recipes() -> dict[str, Any]:
        """List the validated graph recipes with their parameters, ranges and defaults."""
        return {"recipes": graphspec.list_recipes(project.root / "recipes")}

    def search_material_library(query: str = "", material_type: str | None = None, tags: list[str] | None = None,
                                palette: list[str] | None = None, k: int = 5) -> dict[str, Any]:
        """Find curated materials (and 'avoid' examples) by text, material type, tags and palette.
        Returns recipe ids and graph summaries so you can reuse how a material was built, not just how it looks."""
        filters: dict[str, Any] = {"kind": "material"}
        if material_type:
            filters["domain.material_type"] = material_type
        return retrieval.search(store, query, filters=filters, tags=tags or (), palette=palette or (),
                                k=max(1, min(k, 20)), embedder=default_embedder(project))

    def validate_graph_spec(recipe_id: str, params: dict | None = None, graph_name: str | None = None) -> dict[str, Any]:
        """Validate a recipe + parameter values without writing anything: node ids, slots, types, ranges,
        cycles, required outputs. Returns findings with severity error|warning."""
        recipe = _recipe(project, recipe_id)
        findings = graphspec.validate_recipe(recipe, _catalog(project), required_outputs=_required_outputs(project),
                                             graph_name=graph_name)
        try:
            resolved = graphspec.resolve_parameters(recipe, params)
            param_error = None
        except ValueError as exc:
            resolved, param_error = None, str(exc)
        if param_error:
            findings.append(graphspec.finding("error", "parameters", param_error))
        return {"recipe": recipe_id, "valid": not any(f["severity"] == "error" for f in findings),
                "findings": findings, "resolved_parameters": resolved}

    def create_graph_from_recipe(recipe_id: str, params: dict | None = None, graph_name: str | None = None,
                                 dry_run: bool = True, save_sbs_to: str | None = None) -> dict[str, Any]:
        """Compile a recipe into a build script for Substance Designer. With dry_run=true (default) returns the
        plan only. With dry_run=false writes the script and a plan version under the output directory; run the
        script inside Designer, then call read_build_result."""
        recipe = _recipe(project, recipe_id)
        name = graph_name or f"{recipe_id}"
        plan = graphspec.compile_recipe(recipe, _catalog(project), params, graph_name=name,
                                        required_outputs=_required_outputs(project))
        digest = scriptgen.plan_hash(plan)
        summary = {"graph_name": name, "plan_hash": digest, "nodes": len(plan["nodes"]),
                   "connections": len(plan["connections"]), "outputs": [o["usage"] for o in plan["outputs"]],
                   "parameters": plan["parameters"], "warnings": plan["warnings"],
                   "unverified_nodes": plan["unverified_nodes"]}
        steps = Plan(f"Build '{name}' from recipe '{recipe_id}'")
        steps.add("write", "script", f"build_{safe_name(name)}-{digest}.py")
        steps.add("version", f"plan:{name}", "save compiled plan so parameters can be changed later")
        if save_sbs_to:
            save_to = resolve_inside(save_sbs_to, project.allowed_roots)
            steps.add("designer-saves", str(save_to), "the script saves the package here when run in Designer")
        else:
            save_to = None
        if dry_run:
            return {**steps.to_dict(True), "plan": summary}
        out_dir = resolve_inside(project.output_dir / "scripts", project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path = out_dir / f"build_{safe_name(name)}-{digest}.result.json"
        script = scriptgen.build_create_script(plan, str(result_path), str(save_to) if save_to else None)
        scriptgen.check_script_syntax(script)
        script_path = out_dir / f"build_{safe_name(name)}-{digest}.py"
        script_path.write_text(script, encoding="utf-8")
        saved = versions.save(f"plan-{name}", json.dumps(plan, indent=2, sort_keys=True), label=f"recipe {recipe_id}")
        return {**steps.to_dict(False), "plan": summary, "script_path": str(script_path),
                "result_path": str(result_path), "plan_version": saved["version"],
                "run_instructions": "In Designer run this script (Python console or Tools > Scripts), then call "
                                    "read_build_result with result_path. The script is untested against a real Designer "
                                    "until you have run the probe; read errors in the result file."}

    def set_validated_parameters(graph_name: str, changes: dict, dry_run: bool = True) -> dict[str, Any]:
        """Change parameters of a previously created graph plan. Re-validates every value against the recipe's
        ranges, shows a before/after diff, and with dry_run=false saves a new plan version and a rebuild script."""
        plan = json.loads(versions.load(f"plan-{graph_name}"))
        recipe = _recipe(project, plan["recipe"])
        merged = {**plan["parameters"], **changes}
        new_plan = graphspec.compile_recipe(recipe, _catalog(project), merged, graph_name=graph_name,
                                            required_outputs=_required_outputs(project))
        diff = {k: {"from": plan["parameters"][k], "to": merged[k]} for k in changes if plan["parameters"].get(k) != merged[k]}
        digest = scriptgen.plan_hash(new_plan)
        if dry_run:
            return {"dry_run": True, "diff": diff, "new_plan_hash": digest, "old_plan_hash": scriptgen.plan_hash(plan),
                    "note": "Designer graphs are rebuilt, not patched in place: the rebuild is deterministic and versioned."}
        out_dir = resolve_inside(project.output_dir / "scripts", project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path = out_dir / f"build_{safe_name(graph_name)}-{digest}.result.json"
        script = scriptgen.build_create_script(new_plan, str(result_path))
        scriptgen.check_script_syntax(script)
        script_path = out_dir / f"build_{safe_name(graph_name)}-{digest}.py"
        script_path.write_text(script, encoding="utf-8")
        saved = versions.save(f"plan-{graph_name}", json.dumps(new_plan, indent=2, sort_keys=True), label="parameter change")
        return {"dry_run": False, "diff": diff, "script_path": str(script_path), "result_path": str(result_path),
                "plan_version": saved["version"]}

    def save_graph_version(graph_name: str, snapshot_path: str | None = None, label: str = "",
                           dry_run: bool = True) -> dict[str, Any]:
        """Version history for a graph. Plans are saved automatically whenever a script is created. Pass
        snapshot_path (from inspect_active_graph) with dry_run=false to also store the live graph's state as a
        version, e.g. after the user hand-edited it in Designer."""
        history = versions.history(f"plan-{graph_name}")
        out: dict[str, Any] = {"graph_name": graph_name, "plan_versions": history}
        if snapshot_path:
            data = resolve_inside(snapshot_path, project.allowed_roots).read_text(encoding="utf-8")
            json.loads(data)  # must be valid JSON
            if dry_run:
                out["would_save"] = f"snapshot-{graph_name}"
            else:
                out["snapshot_version"] = versions.save(f"snapshot-{graph_name}", data, label=label)
        elif not history:
            raise ValueError(f"no saved plan for '{graph_name}'. Create it first with create_graph_from_recipe(dry_run=false).")
        return out

    def read_build_result(result_path: str) -> dict[str, Any]:
        """Read the JSON a generated Designer script wrote (status, errors, created nodes). Always call this after
        running a script, and report errors verbatim."""
        p = resolve_inside(result_path, project.allowed_roots)
        if not p.exists():
            return {"status": "missing", "note": f"{p.name} does not exist yet; the script has not run (or wrote elsewhere)."}
        data = json.loads(p.read_text(encoding="utf-8"))
        return {"status": data.get("status"), "errors": data.get("errors", []), "warnings": data.get("warnings", []),
                "created_nodes": data.get("created_nodes", []), "saved_to": data.get("saved_to"),
                "plan_hash": data.get("plan_hash")}

    def inspect_active_graph(snapshot_path: str | None = None) -> dict[str, Any]:
        """Inspect the graph currently open in Designer. Without snapshot_path returns an inspect script to run in
        Designer (it writes a snapshot JSON). With snapshot_path summarises that snapshot."""
        if snapshot_path is None:
            target = resolve_inside(project.output_dir / "snapshots" / "active_graph.json", project.allowed_roots)
            return {"mode": "script", "snapshot_path": str(target),
                    "script": scriptgen.build_inspect_script(str(target)),
                    "next": "Run the script inside Designer, then call inspect_active_graph(snapshot_path=...)."}
        data = json.loads(resolve_inside(snapshot_path, project.allowed_roots).read_text(encoding="utf-8"))
        if data.get("status") != "ok":
            return {"mode": "snapshot", "status": data.get("status"), "errors": data.get("errors", [])}
        defs = [n.get("definition") for n in data["nodes"]]
        return {"mode": "snapshot", "graph": data["graph"], "node_count": len(data["nodes"]),
                "connection_count": len(data["connections"]), "definitions": {d: defs.count(d) for d in sorted(set(map(str, defs)))},
                "nodes": data["nodes"][:60], "connections": data["connections"][:120], "warnings": data.get("warnings", [])}

    def summarize_sbs(path: str) -> dict[str, Any]:
        """Summarise a .sbs file's graphs: node counts, atomic filters, instanced library graphs, outputs."""
        p = Path(path).expanduser()
        if p.suffix.lower() != ".sbs" or not p.exists():
            raise ValueError("expected an existing .sbs file")
        summary = sbsinfo.summarize_sbs(p)
        return {**summary, "manifest_fields": sbsinfo.summary_to_manifest_fields(summary)}

    def render_preview(sbs_path: str, resolution: int = 1024, dry_run: bool = True) -> dict[str, Any]:
        """Render preview maps of a saved .sbs using Adobe's sbscooker/sbsrender CLIs. dry_run=true (default) only
        shows the commands. Expensive when executed."""
        return render.render_preview(project, sbs_path, None, resolution, dry_run=dry_run)

    def compare_material_to_rubric(maps: dict[str, str], manual_scores: dict | None = None) -> dict[str, Any]:
        """Measure exported maps (usage -> image path, e.g. {"baseColor": "...png", "normal": ...}) against the style
        ranges and score the rubric. Pass manual_scores (0-1 per criterion) only from an actual look at the
        preview; unscored subjective criteria are reported as unscored, never guessed."""
        for p in maps.values():
            if not Path(p).expanduser().exists():
                raise ValueError(f"map not found: {p}")
        return materials.compare_to_rubric({k: str(Path(v).expanduser()) for k, v in maps.items()}, load_style(project),
                                           load_rubric(project.root / "evals" / "rubric.yaml"), manual_scores)

    def catalog_diff(probe_result_path: str | None = None) -> dict[str, Any]:
        """Compare the draft node catalog with a probe result produced inside Designer; lists every id that differs."""
        probe = load_probe(Path(probe_result_path) if probe_result_path else probe_path(project))
        if probe is None:
            raise ValueError("no probe result found. Generate the script with `python -m sdai probe-script`, run it in "
                             "Designer, and pass the result path (or set SDAI_PROBE_RESULT).")
        return diff_against_probe(_catalog(project), probe)

    def probe_script(result_path: str | None = None) -> dict[str, Any]:
        """Return the probe script to run inside Designer. It reports your version and the real node/parameter ids."""
        target = resolve_inside(result_path or probe_path(project), project.allowed_roots)
        script = scriptgen.build_probe_script(_catalog(project), str(target))
        scriptgen.check_script_syntax(script)
        return {"result_path": str(target), "script": script}

    ro = dict(read_only=True)
    return [
        ToolSpec("inspect_environment", inspect_environment, inspect_environment.__doc__, **ro),
        ToolSpec("list_recipes", list_recipes, list_recipes.__doc__, **ro),
        ToolSpec("search_material_library", search_material_library, search_material_library.__doc__, **ro),
        ToolSpec("validate_graph_spec", validate_graph_spec, validate_graph_spec.__doc__, **ro),
        ToolSpec("inspect_active_graph", inspect_active_graph, inspect_active_graph.__doc__, **ro),
        ToolSpec("summarize_sbs", summarize_sbs, summarize_sbs.__doc__, **ro),
        ToolSpec("read_build_result", read_build_result, read_build_result.__doc__, **ro),
        ToolSpec("compare_material_to_rubric", compare_material_to_rubric, compare_material_to_rubric.__doc__, **ro),
        ToolSpec("catalog_diff", catalog_diff, catalog_diff.__doc__, **ro),
        ToolSpec("probe_script", probe_script, probe_script.__doc__, **ro),
        ToolSpec("create_graph_from_recipe", create_graph_from_recipe, create_graph_from_recipe.__doc__,
                 read_only=False, idempotent=True),
        ToolSpec("set_validated_parameters", set_validated_parameters, set_validated_parameters.__doc__,
                 read_only=False, idempotent=True),
        ToolSpec("save_graph_version", save_graph_version, save_graph_version.__doc__, read_only=False, idempotent=True),
        ToolSpec("render_preview", render_preview, render_preview.__doc__, read_only=False, idempotent=True, expensive=True),
    ]
