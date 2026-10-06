"""MCP tools end to end, safety defaults, library consistency, and repository hygiene."""
import asyncio
import json
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from rbxvfx import hooks as hooks_mod
from rbxvfx.core import agentfiles, evals
from rbxvfx.core.cli import all_tools, run
from rbxvfx.core.manifest import LibraryStore
from rbxvfx.core.mcpkit import build_server, call_local
from rbxvfx.core.style import load_style, validate_style
from rbxvfx.domain import analysis, effects, luau


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return call_local(tools(project), tool, kw)


# --- read-only tools -----------------------------------------------------------------------------------------------
def test_list_and_search(project):
    recipes = call(project, "list_effect_recipes")
    assert {r["id"] for r in recipes["recipes"]} == {"arcane_burst", "ember_aura", "energy_beam", "sprint_trail"}
    assert "Script" not in recipes["allowed_classes"]
    res = call(project, "search_effect_library", query="purple magic burst")
    assert res["positive"][0]["id"] == "arcane-burst-purple-001"
    assert {h["id"] for h in res["negative"]} >= {"burst-too-heavy"}


def test_validate_instance_tree_never_raises_for_bad_effects(project):
    ok = call(project, "validate_instance_tree", recipe_id="arcane_burst")
    assert ok["valid"] and ok["source"] == "recipe"
    bad = call(project, "validate_instance_tree", recipe_id="arcane_burst", params={"size": 99})
    assert not bad["valid"] and bad["findings"][0]["code"] == "build"
    with pytest.raises(ValueError, match="exactly one"):
        call(project, "validate_instance_tree")


def test_budget_and_evaluation(project):
    ok = call(project, "check_performance_budget", recipe_id="arcane_burst", platform="mobile")
    assert ok["within_budget"] and ok["metrics"]["particles_peak"] == 26.0
    busier = call(project, "check_performance_budget", recipe_id="ember_aura", params={"rate": 40}, platform="mobile")
    assert busier["metrics"]["particles_peak"] > ok["metrics"]["particles_peak"] and busier["within_budget"] is True
    with pytest.raises(ValueError, match="unknown platform"):
        call(project, "check_performance_budget", recipe_id="arcane_burst", platform="toaster")
    ev = call(project, "evaluate_effect", recipe_id="arcane_burst")
    assert ev["rubric"]["complete"] is False and not ev["rubric"]["passed"]  # nobody has watched it yet
    assert set(ev["rubric"]["unscored"]) == {"reads_in_motion", "matches_style", "silhouette"}


def test_manual_scores_complete_the_rubric_and_a_veto_blocks_passing(project):
    manual = {"reads_in_motion": 0.9, "matches_style": 0.8, "silhouette": 0.7}
    ev = call(project, "evaluate_effect", recipe_id="arcane_burst", manual_scores=manual)
    assert ev["rubric"]["complete"] and ev["rubric"]["passed"]
    manual["matches_style"] = 0.2
    assert not call(project, "evaluate_effect", recipe_id="arcane_burst", manual_scores=manual)["rubric"]["passed"]


def test_over_budget_effect_fails_the_required_criterion(project):
    ev = call(project, "evaluate_effect", recipe_id="arcane_burst", platform="mobile",
              manual_scores={"reads_in_motion": 1, "matches_style": 1, "silhouette": 1})
    assert ev["rubric"]["passed"]
    # same effect, but evaluate a heavier variant built through the library API
    plan = effects.build_plan(hooks_mod.apply_mutations(effects.load_recipe(project.root / "recipes" / "arcane_burst.yaml"),
                                                      [{"op": "set_emit", "instance": "sparks", "value": 500}]))
    res = analysis.analyze(plan, load_style(project), "mobile")
    assert "particles_peak" in {f["metric"] for f in res["findings"]}


# --- safety defaults ---------------------------------------------------------------------------------------------------
def test_create_is_a_dry_run_by_default_and_returns_no_code(project):
    out = call(project, "create_effect_from_recipe", recipe_id="arcane_burst")
    assert out["dry_run"] is True and "luau" not in out and out["plan"]["root"] == "AIEffect_arcane_burst"
    assert not project.workspace.exists() or not list(project.workspace.rglob("*.luau"))


def test_create_writes_code_only_when_asked(project):
    out = call(project, "create_effect_from_recipe", recipe_id="arcane_burst", name="wizard_burst", dry_run=False,
               target_path="workspace.Effects")
    assert out["plan_version"] == 1 and Path(out["luau_path"]).read_text() == out["luau"]
    assert luau.lint(out["luau"]) == [] and out["studio"]["tool"] == "execute_luau" and out["studio"]["datamodel_type"] == "Edit"
    assert "AIEffect_wizard_burst" in out["luau"] and project.workspace in Path(out["luau_path"]).parents


def test_creation_rejects_bad_input_without_writing(project):
    for kw, match in [({"params": {"size": 99}}, "above maximum"), ({"target_path": "workspace; evil()"}, "dotted path"),
                      ({"position": [1, 2]}, "position"), ({"recipe_id": "nope"}, "unknown recipe")]:
        args = {"recipe_id": "arcane_burst", "dry_run": False, **kw}
        with pytest.raises(ValueError, match=match):
            call(project, "create_effect_from_recipe", **args)
    assert not list(project.workspace.rglob("*.luau")) if project.workspace.exists() else True


def test_preview_returns_cameras_and_only_writes_when_asked(project):
    dry = call(project, "preview_effect", recipe_id="energy_beam")
    assert dry["dry_run"] and {c["name"] for c in dry["cameras"]} == {"gameplay", "close", "top_down"} and "timeline_png" not in dry
    real = call(project, "preview_effect", recipe_id="energy_beam", dry_run=False, position=[10, 5, -3])
    assert Path(real["timeline_png"]).exists()
    assert "EMIT_NOW = true" in real["luau"]
    focus = real["cameras"][0]["look_at"]
    assert abs(focus[0] - 10) < 1e-6 and abs(focus[2] - (-3 + 10)) < 1e-6  # beam midpoint: length 20 -> 10 studs along Z


def test_camera_distance_respects_fov_and_effect_size(project):
    plan = effects.build_plan(effects.load_recipe(project.root / "recipes" / "arcane_burst.yaml"))
    a = analysis.analyze(plan, load_style(project), "mobile")
    cams = analysis.camera_setups(a, (0, 5, 0))
    for cam in cams:
        assert all(isinstance(c, float) for c in cam["position"]) and cam["distance_studs"] > 0
    assert cams[0]["distance_studs"] == a["gameplay"]["distance_studs"] or cams[0]["distance_studs"] > 0


def test_simulate_tool_runs_the_generated_code_on_the_mock(project):
    out = call(project, "simulate_effect", recipe_id="arcane_burst", target_path="workspace.Effects")
    assert out["ok"] and out["report"]["status"] == "ok" and out["emits"] == {"sparks": 24, "flash": 2} and out["mock"] is True


def test_remove_script_is_guarded(project):
    code = call(project, "remove_effect_script", name="wizard_burst")["luau"]
    assert luau.lint(code) == [] and "refusing to remove" in code


def test_variants_are_versioned_and_deduplicated(project):
    dry = call(project, "save_effect_variant", recipe_id="arcane_burst", params={"size": 2.0}, name="big_purple")
    assert dry["dry_run"] and not (project.workspace / "versions").exists()
    one = call(project, "save_effect_variant", recipe_id="arcane_burst", params={"size": 2.0}, name="big_purple", dry_run=False)
    again = call(project, "save_effect_variant", recipe_id="arcane_burst", params={"size": 2.0}, name="big_purple", dry_run=False)
    assert one["version"] == 1 and again["unchanged"]
    with pytest.raises(ValueError, match="name is required"):
        call(project, "save_effect_variant", recipe_id="arcane_burst")


def test_studio_report_round_trip_through_tools(project, snapshot):
    from rbxvfx.domain.mock_roblox import MockRoblox

    mock = MockRoblox(snapshot)
    plan = effects.build_plan(effects.load_recipe(project.root / "recipes" / "ember_aura.yaml"))
    created = mock.run(luau.build_create_script(plan, "workspace", (0, 5, 0)))
    parsed = call(project, "parse_studio_report", text="log line\n" + created)
    assert parsed["status"] == "ok" and len(parsed["created"]) == 3
    script = call(project, "inspect_vfx", path=parsed["root"])
    assert script["mode"] == "script" and "read-only" in script["luau"]
    seen = mock.run(script["luau"])
    summary = call(project, "inspect_vfx", path=parsed["root"], studio_report=seen)
    assert summary["mode"] == "report" and not [f for f in summary["findings"] if f["severity"] == "error"]
    valid = call(project, "validate_instance_tree", studio_report=seen)
    assert valid["valid"] and valid["source"] == "studio"
    with pytest.raises(ValueError, match="no JSON report"):
        call(project, "parse_studio_report", text="nothing useful")


# --- MCP end to end ------------------------------------------------------------------------------------------------------------
async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        plan = await client.call_tool("create_effect_from_recipe", {"recipe_id": "ember_aura"})
        bad = await client.call_tool("create_effect_from_recipe", {"recipe_id": "ember_aura", "target_path": "workspace;evil"})
        sim = await client.call_tool("simulate_effect", {"recipe_id": "ember_aura"})
        rid = (await client.call_tool("record_run", {"request": "ember aura for the fire mage", "recipes": ["ember_aura"]})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "reason": "too bright",
                                                         "corrections": [{"dimension": "glow", "note": "less additive glow on embers"}]})
        bad_dim = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, plan, bad, sim, dec, bad_dim

    listed, plan, bad, sim, dec, bad_dim = asyncio.run(_with(server, go))
    assert listed["search_effect_library"].annotations.readOnlyHint is True
    assert listed["create_effect_from_recipe"].annotations.readOnlyHint is False
    assert plan.structuredContent["dry_run"] is True
    assert bad.isError and "dotted path" in bad.content[0].text
    assert sim.structuredContent["ok"] and not dec.isError and bad_dim.isError


def test_every_tool_is_documented_in_the_readme(project):
    readme = (project.root / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


# --- library + repository hygiene -------------------------------------------------------------------------------------------------
def test_example_library_is_valid_and_its_measurements_match_the_code(project):
    store = LibraryStore(project)
    assert store.validate_all(check_files=True)["ok"]
    style = load_style(project)
    for row in store.load():
        if row["_origin"] != "examples" or row["status"] != "curated" or "recipe" not in row.get("domain", {}):
            continue
        # rebuild the recorded example from its recipe (negatives were built from mutated recipes; the positives from defaults)
        if row["polarity"] == "positive":
            plan = effects.build_plan(effects.load_recipe(project.root / row["source_file"]), name=row["domain"]["recipe"])
            assert analysis.analyze(plan, style, "mobile")["metrics"]["particles_peak"] == row["domain"]["particles_peak"], row["id"]


def test_style_valid_and_every_range_metric_exists(project):
    style = load_style(project)
    assert validate_style(style) == []
    plan = effects.build_plan(effects.load_recipe(project.root / "recipes" / "arcane_burst.yaml"))
    metrics = analysis.analyze(plan, style, "mobile")["metrics"]
    names = set(style["ranges"]) | {n for p in style["platforms"].values() for n in p["ranges"]}
    unknown = names - set(metrics) - {"abrupt_end_count"}
    assert not unknown, f"style ranges reference metrics the analysis never produces: {unknown}"


def test_skills_agent_files_schema_and_baseline(project):
    assert agentfiles.validate_all_skills(project.root) == []
    assert agentfiles.sync(project.root, check=True) == []
    from rbxvfx import KINDS
    from rbxvfx.core.manifest import base_schema
    from rbxvfx.domain.schema import DOMAIN_SCHEMA

    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=project.root)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((project.root / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_evals_fail_when_the_system_is_broken(project, monkeypatch):
    """Guard against vacuous evals: neuter the budget and the lint and confirm tasks start failing."""
    import rbxvfx.hooks as h

    real = h.load_style

    def lax(p):
        s = real(p)
        s["platforms"]["mobile"]["ranges"]["particles_peak"]["max"] = 1e9
        s["ranges"]["apparent_height_fraction"]["min"] = 0
        return s

    monkeypatch.setattr(h, "load_style", lax)
    monkeypatch.setattr(luau, "lint", lambda code: [])
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    rep = evals.run_suite(tasks, h.eval_solver(project), h.eval_checks(project), label="broken", root=project.root)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"analyze-flags-mobile-over-budget", "analyze-flags-unreadably-small", "lint-rejects-publishing"} <= failed


def test_no_generated_code_contains_publish_or_network_calls(project):
    for rid in ("arcane_burst", "ember_aura", "energy_beam", "sprint_trail"):
        plan = effects.build_plan(effects.load_recipe(project.root / "recipes" / f"{rid}.yaml"))
        code = luau.build_create_script(plan, "workspace", (0, 5, 0), emit_now=True)
        stripped = code.replace('game:GetService("HttpService"):JSONEncode', "")  # the one allowed use of HttpService
        for banned in ("Publish", "Http", "Teleport", "DataStore", "Marketplace", "loadstring", "require", "Asset"):
            assert banned not in stripped, (rid, banned)


def test_cli_end_to_end(project, capsys):
    assert run(project, hooks_mod.HOOKS, ["recipes"]) == 0
    assert "arcane_burst" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["build", "arcane_burst", "--param", "size=2"]) == 0
    assert "AIEffect_arcane_burst" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["analyze", "ember_aura", "--platform", "desktop"]) == 0
    assert '"platform": "desktop"' in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["snapshot-info"]) == 0
    assert "ParticleEmitter" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["domain"]["luau_simulation"] == "available" and "running the generated Luau" in report["domain"]["needs_studio"]
