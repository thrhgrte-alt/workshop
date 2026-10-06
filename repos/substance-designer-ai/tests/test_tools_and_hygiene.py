"""MCP tools end to end, safety defaults, measurement, summaries, and repository hygiene."""

import asyncio
import json
import stat

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from sdai import hooks as hooks_mod
from sdai.core import agentfiles, evals
from sdai.core.cli import all_tools, run
from sdai.core.mcpkit import build_server, call_local
from sdai.domain import materials, render, sbsinfo, synthetic
from sdai.core.rubric import load_rubric
from sdai.core.style import load_style, validate_style


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, name, **kw):
    return call_local(tools(project), name, kw)


# --- safety defaults -----------------------------------------------------------------------------
def test_create_graph_defaults_to_dry_run_and_writes_nothing(project):
    out = call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall")
    assert out["dry_run"] is True and out["plan"]["nodes"] == 11
    assert not project.workspace.exists() or not list(project.workspace.rglob("*.py"))


def test_create_graph_writes_script_and_version_only_when_asked(project):
    out = call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall", graph_name="castle_wall",
               params={"brick_columns": 8}, dry_run=False)
    assert out["dry_run"] is False and out["plan_version"] == 1
    script = open(out["script_path"]).read()
    compile(script, "s", "exec")
    assert project.workspace in __import__("pathlib").Path(out["script_path"]).parents


def test_save_path_outside_allowlist_is_refused(project):
    with pytest.raises(PermissionError, match="outside the allowed"):
        call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall", save_sbs_to="/etc/wall.sbs")


def test_allowlist_can_be_widened_deliberately(project, tmp_path, monkeypatch):
    monkeypatch.setenv("SDAI_ALLOWED_PATHS", f"{project.workspace}:{tmp_path / 'mine'}")
    out = call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall",
               save_sbs_to=str(tmp_path / "mine" / "wall.sbs"))
    assert any(s["op"] == "designer-saves" for s in out["steps"])


def test_unknown_recipe_lists_alternatives(project):
    with pytest.raises(ValueError, match="stylized_stone_wall"):
        call(project, "validate_graph_spec", recipe_id="nope")


def test_parameter_changes_are_diffed_validated_and_versioned(project):
    call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall", graph_name="castle_wall", dry_run=False)
    dry = call(project, "set_validated_parameters", graph_name="castle_wall", changes={"bevel_softness": 5.0})
    assert dry["dry_run"] and dry["diff"] == {"bevel_softness": {"from": 2.0, "to": 5.0}}
    assert dry["new_plan_hash"] != dry["old_plan_hash"]
    with pytest.raises(ValueError, match="above maximum"):
        call(project, "set_validated_parameters", graph_name="castle_wall", changes={"bevel_softness": 50})
    real = call(project, "set_validated_parameters", graph_name="castle_wall", changes={"bevel_softness": 5.0}, dry_run=False)
    assert real["plan_version"] == 2
    hist = call(project, "save_graph_version", graph_name="castle_wall")
    assert [v["version"] for v in hist["plan_versions"]] == [1, 2]
    with pytest.raises(ValueError, match="no saved plan"):
        call(project, "save_graph_version", graph_name="never_made")


def test_read_build_result_reports_missing_and_errors(project):
    out = call(project, "create_graph_from_recipe", recipe_id="stylized_stone_wall", dry_run=False)
    assert call(project, "read_build_result", result_path=out["result_path"])["status"] == "missing"
    open(out["result_path"], "w").write(json.dumps({"status": "error", "errors": ["boom"], "created_nodes": []}))
    res = call(project, "read_build_result", result_path=out["result_path"])
    assert res["status"] == "error" and res["errors"] == ["boom"]


def test_inspect_active_graph_script_then_snapshot(project):
    first = call(project, "inspect_active_graph")
    assert first["mode"] == "script" and "getCurrentGraph" in first["script"]
    snap = {"status": "ok", "graph": "g", "nodes": [{"id": "a", "definition": "sbs::compositing::blur", "params": {}}],
            "connections": []}
    import pathlib
    p = pathlib.Path(first["snapshot_path"])  # inside the allowed output directory
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(snap))
    out = call(project, "inspect_active_graph", snapshot_path=str(p))
    assert out["node_count"] == 1 and out["definitions"] == {"sbs::compositing::blur": 1}
    v = call(project, "save_graph_version", graph_name="g", snapshot_path=str(p), dry_run=False)
    assert v["snapshot_version"]["version"] == 1


def test_inspect_environment_is_honest_about_verification(project):
    env = call(project, "inspect_environment")
    assert env["catalog"]["probe_result_found"] is False and env["catalog"]["verified_flags"] == 0
    assert "probe" in env["next_step"].lower()


def test_probe_flow_marks_catalog_clean(project, tmp_path, monkeypatch):
    from sdai.domain.catalog import load_catalog

    cat = load_catalog(project.root / "recipes" / "node_catalog.yaml")
    probe = tmp_path / "ws" / "probe" / "probe_result.json"
    probe.parent.mkdir(parents=True)
    probe.write_text(json.dumps(synthetic.fake_probe(cat)))
    env = call(project, "inspect_environment")
    assert env["catalog"]["probe_clean"] is True and env["catalog"]["designer_version"] == "synthetic-test"
    assert call(project, "catalog_diff")["clean"]


def test_catalog_diff_without_probe_explains_how(project):
    with pytest.raises(ValueError, match="probe-script"):
        call(project, "catalog_diff")


# --- search --------------------------------------------------------------------------------------------
def test_search_material_library_filters_and_reasons(project):
    res = call(project, "search_material_library", query="chunky stone", material_type="stone")
    assert res["positive"][0]["id"] == "stone-wall-chunky-001" and res["positive"][0]["domain"]["recipe"]
    assert {h["id"] for h in res["negative"]} >= {"stone-wall-too-noisy"}


# --- measurement ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("variant,failing", [("good", []), ("seam", ["tileable"]), ("wrong_palette", ["palette_match"]),
                                              ("noisy", ["detail_level"]), ("bad_normal", ["normal_valid"]),
                                              ("missing_normal", ["maps_present"])])
def test_rubric_flags_exactly_the_injected_defect(project, tmp_path, variant, failing):
    maps = synthetic.write_material_set(tmp_path / variant, variant)
    res = call(project, "compare_material_to_rubric", maps=maps)
    got = [c["id"] for c in res["rubric"]["criteria"] if c["passed"] is False]
    assert got == failing
    assert res["rubric"]["complete"] is False and not res["rubric"]["passed"]  # nobody has judged the look yet


def test_manual_scores_complete_the_rubric(project, tmp_path):
    maps = synthetic.write_material_set(tmp_path / "m", "good")
    manual = {"reads_as_intended": 0.9, "matches_style": 0.8, "graph_quality": 0.7}
    res = call(project, "compare_material_to_rubric", maps=maps, manual_scores=manual)
    assert res["rubric"]["complete"] and res["rubric"]["passed"]
    assert any(c["subjective"] for c in res["rubric"]["criteria"])
    manual["matches_style"] = 0.2  # a human veto beats the measurements (hybrid takes the lower score)
    assert not call(project, "compare_material_to_rubric", maps=maps, manual_scores=manual)["rubric"]["passed"]


def test_missing_map_file_is_a_clear_error(project):
    with pytest.raises(ValueError, match="map not found"):
        call(project, "compare_material_to_rubric", maps={"baseColor": "/nope.png"})


# --- sbs + render ---------------------------------------------------------------------------------------------
def test_sbs_summary_and_manifest_fields(project):
    s = sbsinfo.summarize_sbs(project.root / "evals" / "fixtures" / "mini.sbs")
    g = s["graphs"][0]
    assert g["identifier"] == "mini_stone" and g["node_count"] == 4
    assert g["atomic_filters"] == {"blend": 2, "blur": 1} and g["library_instances"] == {"tile_generator": 1}
    fields = sbsinfo.summary_to_manifest_fields(s)
    assert "node:blur" in fields["tags"] and "node:tile_generator" in fields["tags"]
    assert fields["domain"]["exposed_parameters"] == ["brick_scale", "bevel"]


def test_sbs_summary_tolerates_garbage(tmp_path):
    bad = tmp_path / "bad.sbs"
    bad.write_text("not xml")
    s = sbsinfo.summarize_sbs(bad)
    assert s["ok"] is False and s["warnings"]
    odd = tmp_path / "odd.sbs"
    odd.write_text("<package><graph><identifier v='x'/></graph></package>")
    s = sbsinfo.summarize_sbs(odd)
    assert s["ok"] and any("no compNode" in w for w in s["warnings"])


def test_render_preview_is_dry_run_by_default(project, tmp_path):
    sbs = tmp_path / "w.sbs"
    sbs.write_text("<package/>")
    out = call(project, "render_preview", sbs_path=str(sbs))
    assert out["status"] == "planned" and out["commands"]["cook"][0].endswith("sbscooker")
    assert "$outputsize@10,10" in out["commands"]["render"]


def test_render_preview_without_tools_is_unavailable_not_a_crash(project, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    sbs = tmp_path / "w.sbs"
    sbs.write_text("<package/>")
    out = call(project, "render_preview", sbs_path=str(sbs), dry_run=False)
    assert out["status"] == "unavailable"


def test_render_pipeline_orchestration_with_stub_tools(project, tmp_path, monkeypatch):
    """Stub executables prove command orchestration, error reporting and output discovery - not Adobe's CLI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("sbscooker", "#!/bin/sh\ntouch \"$4/$(basename \"$2\" .sbs).sbsar\"\n"),
                       ("sbsrender", "#!/bin/sh\ntouch baseColor.png normal.png\n")):
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SDAI_SBSCOOKER", str(bin_dir / "sbscooker"))
    monkeypatch.setenv("SDAI_SBSRENDER", str(bin_dir / "sbsrender"))
    sbs = tmp_path / "w.sbs"
    sbs.write_text("<package/>")
    out = call(project, "render_preview", sbs_path=str(sbs), dry_run=False)
    assert out["status"] == "ok" and [p.rsplit("/", 1)[-1] for p in out["maps"]] == ["baseColor.png", "normal.png"]
    (bin_dir / "sbsrender").write_text("#!/bin/sh\necho bad flag >&2\nexit 3\n")
    out = call(project, "render_preview", sbs_path=str(sbs), dry_run=False)
    assert out["status"] == "failed" and out["failed_step"] == "render" and "bad flag" in out["stderr_tail"]


def test_render_rejects_wrong_extension_and_resolution(project, tmp_path):
    with pytest.raises(ValueError, match=".sbs"):
        call(project, "render_preview", sbs_path=str(tmp_path / "x.png"))
    sbs = tmp_path / "w.sbs"
    sbs.write_text("<package/>")
    with pytest.raises(ValueError, match="resolution"):
        call(project, "render_preview", sbs_path=str(sbs), resolution=1000)


# --- MCP end to end ---------------------------------------------------------------------------------------------
def test_mcp_session_designs_validates_and_records(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        valid = await client.call_tool("validate_graph_spec", {"recipe_id": "stylized_stone_wall"})
        plan = await client.call_tool("create_graph_from_recipe", {"recipe_id": "stylized_stone_wall"})
        bad = await client.call_tool("create_graph_from_recipe",
                                     {"recipe_id": "stylized_stone_wall", "params": {"bevel_softness": 99}})
        rid = (await client.call_tool("record_run", {"request": "castle wall", "recipes": ["stylized_stone_wall"]})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "reason": "edges too sharp",
                                                         "corrections": [{"dimension": "bevel_roundness", "note": "rounder"}]})
        badim = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise",
                                                           "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, valid, plan, bad, dec, badim

    listed, valid, plan, bad, dec, badim = asyncio.run(_with(server, go))
    assert listed["search_material_library"].annotations.readOnlyHint is True
    assert listed["create_graph_from_recipe"].annotations.readOnlyHint is False
    assert listed["render_preview"].description.endswith("[expensive: runs a slow external process]")
    assert valid.structuredContent["valid"] is True
    assert plan.structuredContent["dry_run"] is True
    assert bad.isError and "above maximum" in bad.content[0].text
    assert not dec.isError and badim.isError and "unknown correction dimension" in badim.content[0].text


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_every_tool_is_documented_in_the_readme(project):
    readme = (project.root / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


# --- repository hygiene ------------------------------------------------------------------------------------------
def test_style_and_rubric_are_valid(project):
    assert validate_style(load_style(project)) == []
    assert load_rubric(project.root / "evals" / "rubric.yaml")["criteria"]


def test_style_ranges_cover_every_measured_metric(project, tmp_path):
    maps = synthetic.write_material_set(tmp_path / "m", "good")
    measured = materials.measure_material(maps, load_style(project))["metrics"]
    unchecked = set(measured) - set(load_style(project)["ranges"])
    assert unchecked <= {"roughness_seam_score"} | set(), f"metrics with no range: {unchecked}"


def test_skills_and_agent_files_are_in_sync(project):
    assert agentfiles.validate_all_skills(project.root) == []
    assert agentfiles.sync(project.root, check=True) == []


def test_generated_manifest_schema_is_current(project):
    from sdai import KINDS
    from sdai.core.manifest import base_schema
    from sdai.domain.schema import DOMAIN_SCHEMA

    on_disk = json.loads(project.schema_file.read_text())
    assert on_disk == base_schema(KINDS, DOMAIN_SCHEMA)


def test_example_library_valid_and_files_exist(project):
    from sdai.core.manifest import LibraryStore

    assert LibraryStore(project).validate_all(check_files=True)["ok"]


def test_baseline_eval_report_matches_a_fresh_run(project):
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=project.root)
    assert fresh["passed"] == fresh["total"], [t for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((project.root / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_evals_actually_fail_when_the_system_is_broken(project, monkeypatch):
    """Guard against vacuous evals: weaken the style so the seam check passes everything, and watch tasks fail."""
    import sdai.hooks as h

    real = h.load_style

    def lax(p):
        s = real(p)
        s["ranges"]["baseColor_seam_score"]["max"] = 1000
        s["ranges"]["basecolor_palette_adherence"]["min"] = 0.0
        return s

    monkeypatch.setattr(h, "load_style", lax)
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    rep = evals.run_suite(tasks, h.eval_solver(project), h.eval_checks(project), label="broken", root=project.root)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"rubric-catches-visible-seam", "rubric-catches-wrong-palette"} <= failed


def test_cli_end_to_end(project, capsys):
    assert run(project, hooks_mod.HOOKS, ["recipes"]) == 0
    assert "stylized_stone_wall" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["build", "stylized_stone_wall", "--param", "bevel_softness=3.5"]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["probe-script"]) == 0
    assert "GENERATED by sdai (probe)" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["catalog-diff"]) == 2  # no probe result yet -> clean error, exit 2
    assert "probe-script" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["domain"]["catalog_verified_on_this_machine"] is None
    assert "running generated scripts" in report["domain"]["needs_designer"]
