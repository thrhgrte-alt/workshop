"""Wires the Designer domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

from . import tools as tools_mod
from .core import feedback as fb
from .core import retrieval
from .core.cli import DomainHooks, emit
from .core.commontools import default_embedder
from .core.manifest import LibraryStore
from .core.project import Project
from .core.rubric import load_rubric
from .core.style import load_style
from .domain import graphspec, materials, sbsinfo, scriptgen, synthetic
from .domain.catalog import diff_against_probe, load_catalog, load_probe

INSTRUCTIONS = """\
Substance 3D Designer assistant. Workflow: get_style_brief -> search_material_library (+ find_past_corrections)
-> list_recipes -> validate_graph_spec -> create_graph_from_recipe(dry_run=true, then false) -> run the generated
script INSIDE Designer -> read_build_result -> render_preview or export maps -> compare_material_to_rubric
-> show the preview to the user -> record_run / record_decision with their actual words.
Never invent node ids or parameter ids: use only recipes/node_catalog.yaml, and call inspect_environment first
to see whether the catalog has been verified on this machine. Scripts are generated for review; no live
connection to Designer exists. Never report success without a build result or a preview."""

EXTENSIONS = {"material": [".sbs"], "graph_recipe": [".yaml", ".yml"],
              "reference_image": [".png", ".jpg", ".jpeg", ".webp"]}


def correction_dimensions(project: Project) -> list[str]:
    return load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    catalog = load_catalog(project.root / "recipes" / "node_catalog.yaml")
    probe = load_probe(tools_mod.probe_path(project))
    from .domain.render import find_tool

    recipes = graphspec.list_recipes(project.root / "recipes")
    return {
        "designer_installed_and_scriptable": "unknown: this CLI cannot see inside Designer; run the probe script there",
        "sbscooker": find_tool(project, "sbscooker", "SBSCOOKER"),
        "sbsrender": find_tool(project, "sbsrender", "SBSRENDER"),
        "recipes": [r["id"] for r in recipes],
        "catalog_nodes": len(catalog),
        "catalog_verified_on_this_machine": None if probe is None else diff_against_probe(catalog, probe)["clean"],
        "probe_result": str(tools_mod.probe_path(project)) if probe else None,
        "works_without_designer": ["search", "recipe validation + compile", "script generation", "map measurement",
                                   "rubric scoring", "feedback + curation", "evals", "MCP server"],
        "needs_designer": ["running generated scripts", "inspecting the active graph", "saving .sbs files",
                           "verifying node/parameter ids"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("SDAI_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["SDAI_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("SDAI_WORKSPACE", None)
            else:
                os.environ["SDAI_WORKSPACE"] = old


def eval_solver(project: Project):
    """Deterministic baseline pipeline for the eval tasks (no model involved)."""
    catalog = load_catalog(project.root / "recipes" / "node_catalog.yaml")
    style = load_style(project)
    rubric = load_rubric(project.root / "evals" / "rubric.yaml")
    fixtures = project.root / "evals" / "fixtures"
    store = LibraryStore(project, include_examples=True)

    def recipe(rid: str) -> dict:
        return tools_mod._recipe(project, rid)

    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "search":
            filters = {"kind": "material"}
            if inp.get("material_type"):
                filters["domain.material_type"] = inp["material_type"]
            hits = retrieval.search(store, inp.get("query", ""), filters=filters, tags=inp.get("tags", ()),
                                    palette=inp.get("palette", ()), k=inp.get("k", 3))
            return {"retrieved": [h["id"] for h in hits["positive"]], "avoid": [h["id"] for h in hits["negative"]],
                    "first_reason": (hits["positive"][0]["reasons"] if hits["positive"] else [])}
        if op == "validate_file":
            r = graphspec.load_recipe(fixtures / inp["file"])
            findings = graphspec.validate_recipe(r, catalog, required_outputs=tuple(inp.get("required_outputs", ())))
            return {"findings": findings, "valid": not any(f["severity"] == "error" for f in findings)}
        if op == "validate_recipe":
            r = recipe(inp["recipe_id"])
            findings = graphspec.validate_recipe(r, catalog, required_outputs=tuple(style.get("required_maps", ())))
            return {"findings": findings, "valid": not any(f["severity"] == "error" for f in findings)}
        if op == "parameters":
            try:
                graphspec.resolve_parameters(recipe(inp["recipe_id"]), inp.get("params"))
                return {"error": None}
            except ValueError as exc:
                return {"error": str(exc)}
        if op == "compile":
            r = recipe(inp["recipe_id"])
            kwargs = dict(graph_name=inp.get("graph_name", inp["recipe_id"]), required_outputs=tuple(style.get("required_maps", ())))
            a = graphspec.compile_recipe(r, catalog, inp.get("params"), **kwargs)
            b = graphspec.compile_recipe(r, catalog, inp.get("params"), **kwargs)
            script = scriptgen.build_create_script(a, "/tmp/result.json", None)
            scriptgen.check_script_syntax(script)
            return {"plan_hash": scriptgen.plan_hash(a), "deterministic": script == scriptgen.build_create_script(b, "/tmp/result.json", None),
                    "nodes": len(a["nodes"]), "outputs": sorted(o["usage"] for o in a["outputs"]),
                    "script_compiles": True, "unverified_nodes": a["unverified_nodes"],
                    "parameters": a["parameters"]}
        if op == "rubric":
            with tempfile.TemporaryDirectory() as d:
                maps = synthetic.write_material_set(Path(d), inp["variant"], seed=inp.get("seed", 0))
                res = materials.compare_to_rubric(maps, style, rubric)
            return {"findings": res["findings"], "rubric": res["rubric"], "metrics": res["metrics"],
                    "failed_criteria": [c["id"] for c in res["rubric"]["criteria"] if c["passed"] is False]}
        if op == "feedback":
            with _temp_workspace():
                rid = fb.record_run(project, request=inp["request"], retrieved=[])
                fb.record_decision(project, rid, "revise", reason=inp["reason"], corrections=inp["corrections"],
                                   allowed_dimensions=correction_dimensions(project))
                found = fb.corrections_for(project, inp["later_request"])
            return {"corrections": found, "found_run": bool(found and found[0]["run_id"] == rid)}
        if op == "path_escape":
            with _temp_workspace():
                try:
                    tool = {t.name: t for t in tools_mod.make_tools(project)}["create_graph_from_recipe"]
                    tool.fn(recipe_id="stylized_stone_wall", save_sbs_to=inp["target"])
                    return {"error": None}
                except PermissionError as exc:
                    return {"error": str(exc)}
        if op == "sbs_summary":
            s = sbsinfo.summarize_sbs(fixtures / inp["file"])
            g = s["graphs"][0] if s["graphs"] else {}
            return {"ok": s["ok"], "graph": g.get("identifier"), "node_count": g.get("node_count"),
                    "atomic_filters": g.get("atomic_filters", {}), "library_instances": g.get("library_instances", {}),
                    "outputs": g.get("outputs", []), "warnings": s["warnings"]}
        if op == "probe_diff":
            d = diff_against_probe(catalog, synthetic.fake_probe(catalog, inp.get("mutate")))
            return {"clean": d["clean"], "problem_nodes": sorted(p["node"] for p in d["problems"]),
                    "verified_ok": d["verified_ok"]}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project: Project) -> dict:
    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:120]!r})"

    def failed_criteria_include(result, task, ids):
        got = set(result.get("failed_criteria", []))
        return set(ids) <= got, f"failed criteria {sorted(got)}; expected to include {ids}"

    def failed_criteria_exclude(result, task, ids):
        got = set(result.get("failed_criteria", []))
        return not (set(ids) & got), f"failed criteria {sorted(got)}; must not include {ids}"

    def list_contains(result, task, path, items):
        from .core.evals import dig

        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    return {"error_contains": error_contains, "failed_criteria_include": failed_criteria_include,
            "failed_criteria_exclude": failed_criteria_exclude, "list_contains": list_contains}


def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("recipes", help="list recipes")
    p.set_defaults(handler=lambda a, pr: emit(graphspec.list_recipes(pr.root / "recipes")))

    p = sub.add_parser("probe-script", help="print the probe script to run INSIDE Designer")
    p.add_argument("--result", help="where the script writes its JSON result")
    p.set_defaults(handler=_probe_script)

    p = sub.add_parser("catalog-diff", help="compare the draft catalog with a probe result")
    p.add_argument("probe_result", nargs="?")
    p.set_defaults(handler=lambda a, pr: _catalog_diff(a, pr))

    p = sub.add_parser("build", help="compile a recipe and write the Designer build script")
    p.add_argument("recipe")
    p.add_argument("--name")
    p.add_argument("--param", action="append", default=[], help="name=value (JSON values)")
    p.add_argument("--apply", action="store_true", help="write files (default: dry run)")
    p.set_defaults(handler=lambda a, pr: _build(a, pr))

    p = sub.add_parser("measure", help="measure exported maps against the style ranges and rubric")
    p.add_argument("--maps", nargs="+", required=True, help="usage=path, e.g. baseColor=bc.png normal=n.png")
    p.set_defaults(handler=lambda a, pr: _measure(a, pr))

    p = sub.add_parser("summarize-sbs", help="summarise a .sbs file")
    p.add_argument("path")
    p.set_defaults(handler=lambda a, pr: emit(sbsinfo.summarize_sbs(a.path)))


def _probe_script(a, pr) -> int:
    tool = {t.name: t for t in tools_mod.make_tools(pr)}["probe_script"]
    print(tool.fn(a.result)["script"])
    return 0


def _catalog_diff(a, pr) -> int:
    probe = load_probe(Path(a.probe_result) if a.probe_result else tools_mod.probe_path(pr))
    if probe is None:
        raise ValueError("no probe result found; run `probe-script` inside Designer first")
    d = diff_against_probe(load_catalog(pr.root / "recipes" / "node_catalog.yaml"), probe)
    emit(d)
    return 0 if d["clean"] else 1


def _build(a, pr) -> int:
    params = {}
    for item in a.param:
        k, _, v = item.partition("=")
        try:
            params[k] = json.loads(v)
        except json.JSONDecodeError:
            params[k] = v
    tool = {t.name: t for t in tools_mod.make_tools(pr)}["create_graph_from_recipe"]
    emit(tool.fn(recipe_id=a.recipe, params=params, graph_name=a.name, dry_run=not a.apply))
    return 0


def _measure(a, pr) -> int:
    maps = dict(item.split("=", 1) for item in a.maps)
    emit(materials.compare_to_rubric(maps, load_style(pr), load_rubric(pr.root / "evals" / "rubric.yaml")))
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
