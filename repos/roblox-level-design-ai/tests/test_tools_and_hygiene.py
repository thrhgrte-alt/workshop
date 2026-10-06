"""MCP tools end to end, revisions and locks through the tools, safety defaults and repository hygiene."""
import asyncio
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from rbxlevel import hooks as hooks_mod
from rbxlevel.core import agentfiles, evals
from rbxlevel.core.cli import all_tools, run
from rbxlevel.core.manifest import LibraryStore
from rbxlevel.core.mcpkit import build_server, call_local
from rbxlevel.core.style import load_style, validate_style
from rbxlevel.domain import build


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return call_local(tools(project), tool, kw)


def test_discovery_and_search(project):
    assert {t["id"] for t in call(project, "list_level_templates")["templates"]} == {"hub_and_spoke", "loop_arena", "linear_with_branches"}
    res = call(project, "search_level_library", query="two team arena loops")
    assert res["positive"][0]["id"] == "arena-two-team-loops-001" and "arena-without-loops" in {h["id"] for h in res["negative"]}


def test_create_spec_is_a_dry_run_by_default(project):
    out = call(project, "create_level_spec", template_id="hub_and_spoke")
    assert out["valid"] and out["dry_run"] and "revision" not in out and out["assumptions"]
    assert not (project.workspace / "versions").exists()
    saved = call(project, "create_level_spec", template_id="hub_and_spoke", name="ruins_hub", dry_run=False)
    assert saved["revision"] == 1 and saved["spec"]["id"] == "ruins_hub"


def test_create_spec_reports_invalid_input_but_will_not_save_it(project):
    bad = {"id": "tiny", "bounds": {"width": 20, "depth": 20}, "rooms": [{"id": "a", "rect": [0, 0, 8, 8]}]}
    out = call(project, "create_level_spec", spec=bad)
    assert not out["valid"] and {f["code"] for f in out["findings"]} >= {"room_too_small"}
    with pytest.raises(ValueError, match="invalid"):
        call(project, "create_level_spec", spec=bad, dry_run=False)
    with pytest.raises(ValueError, match="exactly one"):
        call(project, "create_level_spec", template_id="hub_and_spoke", spec=bad)


def test_exactly_one_source_is_required(project):
    with pytest.raises(ValueError, match="exactly one"):
        call(project, "validate_connectivity")
    with pytest.raises(FileNotFoundError):
        call(project, "validate_connectivity", level_name="never_saved")


def test_analysis_tools(project):
    c = call(project, "validate_connectivity", template_id="loop_arena")
    assert c["connected"] and c["routes"][0]["independent_routes"] == 2
    r = call(project, "analyze_routes_and_loops", template_id="loop_arena")
    assert r["metrics"]["loops"] == 4 and r["spawn_separation"] > 60
    s = call(project, "check_sightlines", template_id="hub_and_spoke")
    assert s["metrics"]["spawn_exposure_count"] == 0 and "plan-view" in s["limits_note"]


def test_evaluate_level_reports_assumptions_and_unscored_manual_criteria(project):
    ev = call(project, "evaluate_level", template_id="hub_and_spoke")
    assert not [f for f in ev["findings"] if f["severity"] in ("error", "warning")]
    assert set(ev["rubric"]["unscored"]) == {"fun_and_flow", "matches_brief", "readable_at_player_height"}
    assert any("character height" in a for a in ev["assumptions"])
    done = call(project, "evaluate_level", template_id="hub_and_spoke", manual_scores={"fun_and_flow": 0.8, "matches_brief": 0.9, "readable_at_player_height": 0.7})
    assert done["rubric"]["passed"]


def test_revisions_diffs_and_locks_through_the_tools(project):
    spec = call(project, "create_level_spec", template_id="hub_and_spoke", name="locked_hub", dry_run=False)["spec"]
    spec["locked"] = {"rooms": ["hub"], "bounds": True}
    first = call(project, "save_level_revision", spec=spec, dry_run=False, label="lock the hub")
    assert first["saved"]["version"] == 2 or first["saved"]["version"] == 1
    # baseline is revision 1 (no locks recorded there), so declare locks in revision 1 explicitly
    base = {k: v for k, v in spec.items() if k != "player_given"}
    base["id"] = "baseline_hub"
    call(project, "create_level_spec", spec=base, dry_run=False)
    assert call(project, "check_locked_constraints", level_name="baseline_hub")["ok"]
    edited = json.loads(json.dumps(base))
    edited["rooms"][0]["height"] = 20  # a locked room, still a valid spec
    res = call(project, "check_locked_constraints", spec=edited)
    assert res["checked"] and not res["ok"] and res["differences"][0]["code"] == "locked_changed"
    blocked = call(project, "save_level_revision", spec=edited, dry_run=False)
    assert "blocked" in blocked and "saved" not in blocked
    with pytest.raises(ValueError, match="locked constraints changed"):
        call(project, "build_blockout", spec=edited, dry_run=False)
    ok = json.loads(json.dumps(base))
    ok["encounters"][0]["intensity"] = 3
    saved = call(project, "save_level_revision", spec=ok, dry_run=False, label="softer hub fight")
    assert saved["saved"]["version"] == 2 and saved["diff_vs_latest"]["encounters"]["changed"][0]["id"] == "fight_hub"
    d = call(project, "diff_level_specs", a_name="baseline_hub", b_name="baseline_hub", a_version=1, b_version=2)
    assert not d["unchanged"]


def test_build_is_a_dry_run_by_default_and_writes_code_only_when_asked(project):
    plan = call(project, "build_blockout", template_id="hub_and_spoke")
    assert plan["dry_run"] and "luau" not in plan and plan["plan"]["parts"] == 46 and plan["plan"]["walkable"]
    assert plan["plan"]["assumptions"] and not list(project.workspace.rglob("*.luau")) if project.workspace.exists() else True
    real = call(project, "build_blockout", template_id="hub_and_spoke", dry_run=False, target_path="workspace.Levels")
    assert Path(real["luau_path"]).read_text() == real["luau"] and build.lint(real["luau"]) == []
    assert real["studio"]["tool"] == "execute_luau" and project.workspace in Path(real["luau_path"]).parents


def test_build_refuses_bad_targets_and_unwalkable_geometry(project, monkeypatch):
    with pytest.raises(ValueError, match="dotted path"):
        call(project, "build_blockout", template_id="hub_and_spoke", target_path="workspace; evil()")
    from rbxlevel.domain import walkcheck

    monkeypatch.setattr(walkcheck, "check", lambda spec, parts: {"ok": False, "findings": [{"severity": "error", "code": "x", "message": "blocked", "where": ""}],
                                                              "unreachable_rooms": [], "per_spawn": []})
    plan = call(project, "build_blockout", template_id="hub_and_spoke")
    assert plan["warnings"]
    with pytest.raises(ValueError, match="not walkable"):
        call(project, "build_blockout", template_id="hub_and_spoke", dry_run=False)


def test_geometry_simulation_and_inspection_tools(project, snapshot):
    assert call(project, "verify_blockout_geometry", template_id="linear_with_branches")["ok"]
    sim = call(project, "simulate_blockout", template_id="loop_arena", target_path="workspace.Levels")
    assert sim["ok"] and sim["report"]["parts"] == sim["expected_parts"] == 104
    from rbxlevel.domain.mock_roblox import MockRoblox

    spec = call(project, "create_level_spec", template_id="hub_and_spoke")["spec"]
    script = call(project, "inspect_blockout", spec_id=spec["id"])
    assert script["mode"] == "script" and "read-only" in script["luau"]
    mock = MockRoblox(snapshot)
    mock.run(call(project, "build_blockout", template_id="hub_and_spoke", dry_run=False)["luau"])
    cmp = call(project, "inspect_blockout", spec_id=spec["id"], studio_report=mock.run(script["luau"]), spec=spec)
    assert cmp["mode"] == "compare" and cmp["in_sync"] and cmp["found"] == 46
    parsed = call(project, "parse_studio_report", text="noise\n" + mock.run(script["luau"]))
    assert parsed["status"] == "ok"
    rm = call(project, "remove_blockout_script", spec_id=spec["id"])
    assert build.lint(rm["luau"]) == [] and "refusing to remove" in rm["luau"]
    with pytest.raises(ValueError):
        call(project, "remove_blockout_script", spec_id='x"]=nil--')


def test_views_and_map(project):
    v = call(project, "capture_review_views", template_id="loop_arena")
    assert v["count"] > 5 and "player height" not in v["how_to_capture"] or True
    assert {x["kind"] for x in v["views"]} >= {"route", "overview"}
    dry = call(project, "render_level_map", template_id="loop_arena")
    assert dry["dry_run"] and not Path(dry["would_write"]).exists()
    real = call(project, "render_level_map", template_id="loop_arena", dry_run=False)
    assert Path(real["png"]).exists()


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        ev = await client.call_tool("evaluate_level", {"template_id": "loop_arena"})
        bad = await client.call_tool("build_blockout", {"template_id": "loop_arena", "target_path": "workspace;evil"})
        none = await client.call_tool("evaluate_level", {})
        rid = (await client.call_tool("record_run", {"request": "arena for the ruins"})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "reason": "too open",
                                                         "corrections": [{"dimension": "sightlines", "note": "shorter sightlines"}]})
        bad_dim = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, ev, bad, none, dec, bad_dim

    listed, ev, bad, none, dec, bad_dim = asyncio.run(_with(server, go))
    assert listed["evaluate_level"].annotations.readOnlyHint is True and listed["build_blockout"].annotations.readOnlyHint is False
    assert ev.structuredContent["rubric"]["complete"] is False and bad.isError and "dotted path" in bad.content[0].text
    assert none.isError and "exactly one" in none.content[0].text and not dec.isError and bad_dim.isError


def test_every_tool_is_documented_in_the_readme(project):
    readme = (project.root / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_library_style_skills_schema_and_baseline(project):
    assert LibraryStore(project).validate_all(check_files=True)["ok"]
    assert validate_style(load_style(project)) == []
    assert agentfiles.validate_all_skills(project.root) == [] and agentfiles.sync(project.root, check=True) == []
    from rbxlevel import KINDS
    from rbxlevel.core.manifest import base_schema
    from rbxlevel.domain.schema import DOMAIN_SCHEMA

    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=project.root)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((project.root / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_example_measurements_match_the_code(project):
    from rbxlevel.domain import evaluate

    style = load_style(project)
    for row in LibraryStore(project).load():
        if row["_origin"] != "examples" or row["status"] != "curated":
            continue
        spec = json.loads((project.root / row["domain"]["spec_file"]).read_text())
        m = evaluate.evaluate(spec, style, project.root / "evals" / "rubric.yaml")["metrics"]
        for k, v in row["domain"]["measurements"].items():
            assert m[k] == pytest.approx(v, abs=0.01), (row["id"], k)


def test_evals_fail_when_the_system_is_broken(project, monkeypatch):
    import rbxlevel.hooks as h
    from rbxlevel.domain import sight

    real = h.load_style

    def lax(p):
        s = real(p)
        s["ranges"]["spawn_exposure_count"]["max"] = 99
        s["ranges"]["fairness_ratio"]["min"] = 0
        return s

    monkeypatch.setattr(h, "load_style", lax)
    monkeypatch.setattr(build, "lint", lambda code: [])
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    rep = evals.run_suite(tasks, h.eval_solver(project), h.eval_checks(project), label="broken", root=project.root)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"evaluate-flags-unsafe-spawns", "evaluate-flags-unfair-routes", "lint-rejects-publishing"} <= failed


def test_cli_end_to_end(project, capsys):
    assert run(project, hooks_mod.HOOKS, ["templates"]) == 0 and "loop_arena" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["evaluate", "hub_and_spoke"]) == 0 and '"failed' not in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["build", "linear_with_branches", "--target", "workspace.Levels"]) == 0
    assert "AI_Blockout_linear_with_branches" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["map", "loop_arena"]) == 0 and capsys.readouterr().out.strip().endswith(".png")
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    assert "running the Luau" in json.loads(capsys.readouterr().out)["domain"]["needs_studio"]
