"""MCP tools end to end, versions/undo/locks through the tools, safety defaults and repository hygiene."""
import asyncio
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from setdress import hooks as hooks_mod
from setdress.core import agentfiles, evals
from setdress.core.cli import all_tools, run
from setdress.core.manifest import LibraryStore
from setdress.core.mcpkit import build_server, call_local
from setdress.core.style import load_style, validate_style
from setdress.domain import export, scene as SC


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return call_local(tools(project), tool, kw)


def sc_file(project, name):
    return json.loads((project.root / "examples" / "scenes" / f"{name}.json").read_text())


def test_kit_tools(project):
    hits = call(project, "search_kit", query="round table")["hits"]
    assert hits[0]["id"] == "table_round" and "seat_front:seat" in hits[0]["sockets"]
    rep = call(project, "kit_report")
    assert rep["modules"] == 16 and rep["unresolved"] == {} and "surface" in rep["socket_types"]
    assert call(project, "search_kit", tags=["seat"], max_width=2.0)["count"] >= 2


def test_inspect_and_checks(project):
    out = call(project, "inspect_scene", scene=sc_file(project, "tavern_corner"))
    assert out["instances"] == 25 and out["locked"] == ["bar_001", "hearth_001"] and not [f for f in out["findings"] if f["severity"] == "error"]
    bad = call(project, "check_collisions_and_clearance", scene=sc_file(project, "tavern_blocked_path"))
    assert not bad["ok"] and {f["code"] for f in bad["findings"]} >= {"blocks_path", "blocks_sightline"}
    with pytest.raises(ValueError, match="exactly one"):
        call(project, "inspect_scene")


def test_placing_is_a_dry_run_by_default_and_saves_versions_when_applied(project):
    base = sc_file(project, "tavern_bare")
    plan = call(project, "place_module", module="table_round", at=[10, 24], scene=base)
    assert plan["dry_run"] and plan["ok"] and "saved" not in plan and not (project.workspace / "versions").exists()
    first = call(project, "place_module", module="table_round", at=[10, 24], scene=base, dry_run=False)
    assert first["saved"]["version"] == 1 and first["scene_hash_after"]
    second = call(project, "snap_to_socket", scene_name="tavern_bare", target_id=first["plan"]["adds"][0]["id"], target_socket="seat_front", module="chair", module_socket="front", dry_run=False)
    assert second["saved"]["version"] == 2 and second["ok"]
    insp = call(project, "inspect_scene", scene_name="tavern_bare")
    assert insp["instances"] == 4 and insp["history"] == [1, 2]


def test_invalid_placements_are_refused_with_the_reason(project):
    base = sc_file(project, "tavern_bare")
    bad = call(project, "place_module", module="crate_small", at=[20, 8], scene=base)
    assert not bad["ok"] and bad["findings"][0]["code"] == "blocks_path"
    with pytest.raises(ValueError, match="refusing to save"):
        call(project, "place_module", module="crate_small", at=[20, 8], scene=base, dry_run=False)
    with pytest.raises(ValueError, match="approved kit"):
        call(project, "place_module", module="dragon_statue", at=[10, 24], scene=base)
    assert not (project.workspace / "versions").exists()


def test_undo_restores_the_previous_version(project):
    base = sc_file(project, "tavern_bare")
    call(project, "place_module", module="table_round", at=[10, 24], scene=base, dry_run=False)
    call(project, "place_module", module="crate_small", at=[30, 24], scene_name="tavern_bare", dry_run=False)
    assert call(project, "inspect_scene", scene_name="tavern_bare")["instances"] == 4
    dry = call(project, "undo_last_change", scene_name="tavern_bare")
    assert dry["dry_run"] and len(dry["diff"]["removed"]) == 1 and dry["restores_version"] == 1
    call(project, "undo_last_change", scene_name="tavern_bare", dry_run=False)
    assert call(project, "inspect_scene", scene_name="tavern_bare")["instances"] == 3
    assert call(project, "inspect_scene", scene_name="tavern_bare")["history"] == [1, 2, 3]  # history is never lost
    with pytest.raises(ValueError, match="no earlier version"):
        call(project, "undo_last_change", scene_name="never_saved")


def test_locked_pieces_cannot_be_moved_through_the_tools(project):
    base = sc_file(project, "tavern_bare")
    call(project, "place_module", module="table_round", at=[10, 24], scene=base, dry_run=False)
    with pytest.raises(ValueError, match="locked"):
        call(project, "apply_scene_plan", scene_name="tavern_bare", plan={"moves": [{"id": "hearth_001", "from": {}, "to": {"at": [5, 15]}}]})
    assert call(project, "check_locked_constraints", scene_name="tavern_bare")["ok"]
    tampered = json.loads(json.dumps(sc_file(project, "tavern_bare")))
    tampered["instances"][0]["at"] = [4, 15]
    res = call(project, "check_locked_constraints", scene=tampered)
    assert res["checked"] and not res["ok"] and res["differences"][0]["code"] == "locked_changed"
    with pytest.raises(ValueError, match="refusing to save"):
        call(project, "apply_scene_plan", scene=tampered, plan={"adds": []}, dry_run=False)


def test_dress_region_plan_then_apply(project):
    base = sc_file(project, "tavern_bare")
    plan = call(project, "dress_region", rules={"density": 0.2, "exclude_tags": ["wall"]}, seed=3, scene=base)
    assert plan["dry_run"] and plan["ok"] and plan["adds"] > 5 and plan["explain"] and plan["saved"] is None
    applied = call(project, "dress_region", rules={"density": 0.2, "exclude_tags": ["wall"]}, seed=3, scene=base, dry_run=False)
    assert applied["saved"]["version"] == 1 and applied["adds"] == plan["adds"]
    comp = call(project, "validate_composition", scene_name="tavern_bare")
    assert not [f for f in comp["findings"] if f["severity"] == "error"]
    variant = call(project, "save_scene_variant", scene_name="tavern_bare", name="seed_3", dry_run=False)
    assert variant["version"] == 1
    with pytest.raises(ValueError, match="variant name"):
        call(project, "save_scene_variant", scene_name="tavern_bare", name="bad name")


def test_composition_rubric_leaves_judgment_to_a_person(project):
    corner = sc_file(project, "tavern_corner")
    ev = call(project, "validate_composition", scene=corner)
    assert not ev["range_findings"] and {"reads_as_lived_in", "matches_style", "supports_gameplay"} <= set(ev["rubric"]["unscored"])
    assert "locks_respected" in ev["rubric"]["unscored"]  # an inline scene has no saved baseline to compare with
    assert ev["rubric"]["complete"] is False and not ev["rubric"]["passed"]
    call(project, "apply_scene_plan", scene=corner, plan={"adds": []}, dry_run=False)  # the first saved version becomes the baseline
    manual = {"reads_as_lived_in": 0.8, "matches_style": 0.9, "supports_gameplay": 0.7}
    done = call(project, "validate_composition", scene_name="tavern_corner", manual_scores=manual)
    assert done["rubric"]["passed"], done["rubric"]
    veto = call(project, "validate_composition", scene_name="tavern_corner", manual_scores={**manual, "reads_as_lived_in": 0.1})
    assert not veto["rubric"]["passed"]
    blocked = call(project, "validate_composition", scene=sc_file(project, "tavern_blocked_path"))
    assert "paths_clear" in {c["id"] for c in blocked["rubric"]["criteria"] if c["passed"] is False}


def test_diff_and_export(project):
    call(project, "place_module", module="table_round", at=[10, 24], scene=sc_file(project, "tavern_bare"), dry_run=False)
    call(project, "place_module", module="crate_small", at=[30, 24], scene_name="tavern_bare", dry_run=False)
    d = call(project, "diff_scenes", a_name="tavern_bare", b_name="tavern_bare", a_version=1, b_version=2)
    assert len(d["added"]) == 1 and not d["unchanged"]
    dry = call(project, "export_scene", scene=sc_file(project, "tavern_corner"))
    assert dry["dry_run"] and "luau" not in dry and not list(project.workspace.rglob("*.luau")) if project.workspace.exists() else True
    real = call(project, "export_scene", scene=sc_file(project, "tavern_corner"), dry_run=False, target_path="workspace.Props")
    assert Path(real["luau_path"]).read_text() == real["luau"] and export.lint(real["luau"]) == [] and real["studio"]["tool"] == "execute_luau"
    js = call(project, "export_scene", scene=sc_file(project, "tavern_corner"), format="json", dry_run=False)
    assert js["data"]["format"] == "setdress-scene/1" and Path(js["path"]).exists()
    with pytest.raises(ValueError, match="refusing to export"):
        call(project, "export_scene", scene=sc_file(project, "tavern_blocked_path"), dry_run=False)
    with pytest.raises(ValueError, match="dotted path"):
        call(project, "export_scene", scene=sc_file(project, "tavern_corner"), target_path="workspace; evil()")


@pytest.mark.parametrize("mode", ["primitives", "clone"])
def test_simulate_and_roundtrip_tools(project, snapshot, mode):
    sim = call(project, "simulate_scene", scene=sc_file(project, "tavern_corner"), mode=mode, target_path="workspace.Props")
    assert sim["ok"] and sim["report"]["instances"] == 25


def test_inspect_placed_detects_drift(project, snapshot):
    from setdress.domain.mock_roblox import MockRoblox

    scene = sc_file(project, "tavern_corner")
    script = call(project, "inspect_placed", scene_id="tavern_corner")
    assert script["mode"] == "script" and "read-only" in script["luau"]
    mock = MockRoblox(snapshot)
    mock.run(call(project, "export_scene", scene=scene, dry_run=False)["luau"])
    ok = call(project, "inspect_placed", scene_id="tavern_corner", studio_report=mock.run(script["luau"]), scene=scene)
    assert ok["in_sync"] and ok["found"] == 25
    mock.run("workspace:FindFirstChild('AI_SetDress_tavern_corner'):FindFirstChild('table_001').Position = Vector3.new(1, 1, 1)")
    assert call(project, "inspect_placed", scene_id="tavern_corner", studio_report=mock.run(script["luau"]), scene=scene)["moved"][0]["id"] == "table_001"
    assert call(project, "parse_studio_report", text="log\n" + mock.run(script["luau"]))["status"] == "ok"
    rm = call(project, "remove_scene_script", scene_id="tavern_corner")
    assert export.lint(rm["luau"]) == [] and "refusing to remove" in rm["luau"]


def test_map_tool(project):
    dry = call(project, "render_scene_map", scene=sc_file(project, "tavern_corner"))
    assert dry["dry_run"] and not Path(dry["would_write"]).exists()
    assert Path(call(project, "render_scene_map", scene=sc_file(project, "tavern_corner"), dry_run=False)["png"]).exists()


def test_a_user_kit_overrides_the_example(project, tmp_path, monkeypatch):
    kit = tmp_path / "mykit.yaml"
    kit.write_text("modules:\n  - {id: stump, footprint: [2, 2], height: 2, tags: [nature]}\n")
    monkeypatch.setenv("SETDRESS_KIT", str(kit))
    assert call(project, "search_kit", tags=["nature"])["hits"][0]["id"] == "stump"
    rep = call(project, "kit_report")
    assert rep["unresolved"]["stump"] == ["pivot", "collision", "material", "color", "scale"] or "stump" in rep["unresolved"]
    kit.write_text("modules:\n  - {id: bad, footprint: [0, 2], height: 2}\n")
    with pytest.raises(ValueError, match="kit has errors"):
        call(project, "search_kit")


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)
    base = sc_file(project, "tavern_bare")

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        found = await client.call_tool("search_kit", {"query": "chair"})
        placed = await client.call_tool("place_module", {"module": "crate_small", "at": [10, 24], "scene": base})
        blocked = await client.call_tool("place_module", {"module": "crate_small", "at": [20, 8], "scene": base})
        unknown = await client.call_tool("place_module", {"module": "dragon_statue", "at": [10, 24], "scene": base})
        rid = (await client.call_tool("record_run", {"request": "dress the tavern"})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "reason": "too empty", "corrections": [{"dimension": "density", "note": "more props"}]})
        bad_dim = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, found, placed, blocked, unknown, dec, bad_dim

    listed, found, placed, blocked, unknown, dec, bad_dim = asyncio.run(_with(server, go))
    assert listed["search_kit"].annotations.readOnlyHint is True and listed["dress_region"].annotations.readOnlyHint is False
    assert found.structuredContent["hits"][0]["id"] == "chair" and placed.structuredContent["ok"] and not blocked.structuredContent["ok"]
    assert unknown.isError and "approved kit" in unknown.content[0].text and not dec.isError and bad_dim.isError


def test_every_tool_is_documented_in_the_readme(project):
    readme = (project.root / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_library_style_skills_schema_and_baseline(project):
    assert LibraryStore(project).validate_all(check_files=True)["ok"]
    assert validate_style(load_style(project)) == []
    assert agentfiles.validate_all_skills(project.root) == [] and agentfiles.sync(project.root, check=True) == []
    from setdress import KINDS
    from setdress.core.manifest import base_schema
    from setdress.domain.schema import DOMAIN_SCHEMA

    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=project.root)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((project.root / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_example_measurements_match_the_code(project):
    from setdress.domain import compose
    from setdress.tools import load_kit

    kit, _ = load_kit(project)
    for row in LibraryStore(project).load():
        if row["_origin"] != "examples" or row["status"] != "curated":
            continue
        m = compose.measure(SC.normalize(json.loads((project.root / row["domain"]["scene_file"]).read_text())), kit)["metrics"]
        for k, v in row["domain"]["measurements"].items():
            assert m[k] == pytest.approx(v, abs=0.002), (row["id"], k)


def test_evals_fail_when_the_system_is_broken(project, monkeypatch):
    import setdress.hooks as h

    real = h.load_style

    def lax(p):
        s = real(p)
        s["ranges"]["coverage"] = {"min": 0, "max": 9, "severity": "warning"}
        s["ranges"]["blocked_paths"]["max"] = 99
        return s

    monkeypatch.setattr(h, "load_style", lax)
    monkeypatch.setattr(export, "lint", lambda code: [])
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    rep = evals.run_suite(tasks, h.eval_solver(project), h.eval_checks(project), label="broken", root=project.root)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"measure-bare-scene-is-too-empty", "measure-flags-overcrowding", "lint-rejects-publishing"} <= failed


def test_cli_end_to_end(project, capsys):
    scene = str(project.root / "examples" / "scenes" / "tavern_corner.json")
    assert run(project, hooks_mod.HOOKS, ["kit"]) == 0 and "table_round" not in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["validate", scene]) == 0 and '"errors": 0' in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["validate", str(project.root / "examples" / "scenes" / "tavern_blocked_path.json")]) == 1
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["measure", scene]) == 0 and "coverage" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["dress", str(project.root / "examples" / "scenes" / "tavern_bare.json"), "--seed", "2"]) == 0
    assert '"adds"' in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["export", scene]) == 0 and "AI_SetDress_tavern_corner" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["map", scene]) == 0 and capsys.readouterr().out.strip().endswith(".png")
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    assert "running the Luau" in json.loads(capsys.readouterr().out)["domain"]["needs_studio"]
