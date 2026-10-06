"""MCP tools end to end, scope and safety defaults, shared-machinery boundary, docs, evals (including a guard against vacuous evals) and repository hygiene."""
import asyncio
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from econbal import hooks as hooks_mod
from econbal.domain import analysis as A, luau, places
from econbal.guide_adapter import agentfiles, all_tools, cli, evals, mcpkit
from econbal.guide_adapter import config, manifest

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"project_id": "demo_mine", "place_id": "main"}
STUDIO = [{"name": "Demo Mine Main (SYNTHETIC)", "place_id": 0, "studio_id": "s1"}]
SPEC_READ_ONLY = {"get_style_brief", "validate_economy_spec", "simulate_progression", "time_to_upgrade_table", "flow_report", "find_dominant_strategies", "find_dead_options", "sensitivity",
                  "compare_specs", "plot_curves", "find_past_corrections"}
SPEC_WRITERS = {"propose_rebalance", "export_values_luau", "save_spec_version", "record_run", "record_decision", "promote_run"}


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return mcpkit.call_local(tools(project), tool, kw)


def test_every_tool_named_in_the_spec_exists_with_the_right_kind(project):
    by = {t.name: t for t in tools(project)}
    assert SPEC_READ_ONLY | SPEC_WRITERS <= set(by)
    assert all(by[n].read_only for n in SPEC_READ_ONLY)
    assert all(not by[n].read_only for n in SPEC_WRITERS)


def test_tool_functions_are_annotated_and_writers_default_to_dry_run(project):
    for t in tools(project):
        assert inspect.signature(t.fn).return_annotation in ("dict[str, Any]", "dict[str, typing.Any]") or "dict" in str(inspect.signature(t.fn).return_annotation)
        sig = inspect.signature(t.fn).parameters
        if "dry_run" in sig:
            assert sig["dry_run"].default is True, t.name
        if not t.read_only and t.name not in ("record_run", "record_decision", "promote_run"):
            assert "dry_run" in sig, t.name
        for p in ("project_id", "place_id"):
            if t.name != "compare_specs":
                assert p in sig and sig[p].default is None, (t.name, p)  # optional in the schema, refused at run time with a message that says what is missing
    assert {"a_project_id", "b_place_id"} <= set(inspect.signature(next(t for t in tools(project) if t.name == "compare_specs").fn).parameters)


def test_no_tool_has_a_cross_place_apply(project):
    for name in ("propose_rebalance", "export_values_luau", "save_spec_version"):
        params = set(inspect.signature(next(t for t in tools(project) if t.name == name).fn).parameters)
        assert not {"a_project_id", "b_project_id", "a_place_id", "b_place_id", "other_place_id"} & params


def test_dry_runs_write_nothing_and_real_writes_go_to_the_place_folder(project):
    ws = project.workspace
    res = call(project, "propose_rebalance", project_id="demo_mine", place_id="hardcore", finding_code="under_band")
    assert res["dry_run"] and not ws.exists()
    res = call(project, "propose_rebalance", project_id="demo_mine", place_id="hardcore", finding_code="under_band", dry_run=False)
    path = Path(res["written"])
    assert path.is_file() and (ws / "projects" / "demo_mine" / "hardcore") in path.parents
    assert S_value(path) != S_value(ROOT / "projects" / "demo_mine" / "hardcore" / "economy.yaml")  # a NEW file with the proposal
    again = call(project, "propose_rebalance", project_id="demo_mine", place_id="hardcore", finding_code="under_band", dry_run=False)
    assert again["written"] == res["written"]  # same content, same name, nothing overwritten


def S_value(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_tools_are_deterministic(project):
    for tool, kw in (("check_economy", {}), ("simulate_progression", {}), ("time_to_upgrade_table", {"detail": True}), ("flow_report", {}), ("find_dominant_strategies", {})):
        assert call(project, tool, **MAIN, **kw) == call(project, tool, **MAIN, **kw)
    a = call(project, "simulate_progression", **MAIN, seed=5)
    b = call(project, "simulate_progression", **MAIN, seed=5)
    assert a == b


def test_summary_is_the_first_key_and_scope_is_stated(project):
    for tool in ("check_economy", "time_to_upgrade_table", "flow_report", "find_dead_options", "monetisation_check", "validate_economy_spec", "get_style_brief"):
        res = call(project, tool, **MAIN)
        assert next(iter(res)) == "summary" and res["project_id"] == "demo_mine" and res["place_id"] == "main", tool


def test_export_values_dry_run_then_write(project):
    dry = call(project, "export_values_luau", **MAIN, studios=STUDIO, all_values=True, verify_on_mock=True)
    assert dry["dry_run"] and "luau" not in dry and dry["lint"] == [] and dry["mock_run"]["status"] in ("ok", "skipped")
    real = call(project, "export_values_luau", **MAIN, studios=STUDIO, all_values=True, dry_run=False)
    assert Path(real["luau_path"]).read_text() == real["luau"] and luau.lint(real["luau"]) == []
    with pytest.raises(ValueError, match="nothing to export"):
        call(project, "export_values_luau", **MAIN, studios=STUDIO, spec=S_value(ROOT / "projects" / "demo_mine" / "main" / "economy.yaml"))  # identical to the place file: no diff
    with pytest.raises(ValueError, match="config_name"):
        call(project, "export_values_luau", **MAIN, studios=STUDIO, all_values=True, config_name="x; y")


def test_export_of_a_proposal_contains_only_the_changed_value(project):
    prop = call(project, "propose_rebalance", project_id="demo_mine", place_id="hardcore", finding_code="under_band", dry_run=False)
    call(project, "save_spec_version", name="p1", project_id="demo_mine", place_id="hardcore", spec=S_value(prop["written"]), dry_run=False)
    studios = [{"name": "Demo Mine Hardcore (SYNTHETIC)", "studio_id": "h"}]
    res = call(project, "export_values_luau", project_id="demo_mine", place_id="hardcore", studios=studios, spec_name="p1")
    assert res["mode"] == "changed values only" and res["values"] == len(prop["changes"])
    assert {c["path"] for c in res["changed"]} == {c["path"] for c in prop["changes"]}


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = mcpkit.build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        checked = await client.call_tool("check_economy", MAIN)
        no_scope = await client.call_tool("check_economy", {})
        wall = await client.call_tool("check_economy", {**MAIN, "spec": json.loads(json.dumps(yaml.safe_load((ROOT / "examples" / "economies" / "wall.yaml").read_text())))})
        rid = (await client.call_tool("record_run", {**MAIN, "request": "tier 4 felt too slow"})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "reason": "slow", "corrections": [{"dimension": "pacing", "tier": 4, "felt": "too_slow", "note": "n"}]})
        bad = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, checked, no_scope, wall, dec, bad

    listed, checked, no_scope, wall, dec, bad = asyncio.run(_with(server, go))
    assert listed["check_economy"].annotations.readOnlyHint is True and listed["propose_rebalance"].annotations.readOnlyHint is False
    assert checked.structuredContent["verdict"] == "pass" and no_scope.isError and "missing project_id and place_id" in no_scope.content[0].text
    assert wall.structuredContent["verdict"] == "fail" and not dec.isError and bad.isError


def test_every_tool_is_documented_in_the_readme(project):
    readme = (ROOT / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_rare_group_can_be_disabled(project, monkeypatch):
    all_names = {t.name for t in tools(project)}
    monkeypatch.setenv("ECONBAL_DISABLE_GROUPS", "rare")
    core = {t.name for t in tools(project)}
    assert {"sensitivity", "plot_curves", "export_values_luau", "emit_import_luau"} <= all_names - core
    assert {"check_economy", "propose_rebalance", "record_run", "time_to_upgrade_table"} <= core
    assert all(t.description.startswith("[rare]") for t in tools_with(project, all_names - core))


def tools_with(project, names):
    import os

    os.environ.pop("ECONBAL_DISABLE_GROUPS", None)
    try:
        return [t for t in all_tools(project, hooks_mod.HOOKS) if t.name in names]
    finally:
        os.environ["ECONBAL_DISABLE_GROUPS"] = "rare"


def test_core_tools_are_replaced_by_scoped_ones_and_unscoped_search_is_dropped(project):
    names = [t.name for t in tools(project)]
    assert len(names) == len(set(names)) and "search_library" not in names
    with pytest.raises(ValueError, match="missing project_id"):
        call(project, "record_run", request="x")


# --- the shared-machinery boundary ---------------------------------------------------------------------------------------------------
def test_only_the_adapter_imports_shared_machinery():
    pkg = ROOT / "econbal"
    offenders = []
    for f in pkg.rglob("*.py"):
        if "core" in f.relative_to(pkg).parts or f.name == "guide_adapter.py":
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"(from\s+\.+core\b|import\s+\.+core\b|econbal\.core\b|from\s+econbal\s+import\s+core|from\s+guide_core\b|import\s+guide_core\b)", text):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"import shared machinery only via econbal.guide_adapter: {offenders}"


def test_the_adapter_exposes_the_named_interfaces():
    from econbal import guide_adapter as g

    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope"):
        assert hasattr(g, name), name
    assert hasattr(g.dryrun, "Plan") and hasattr(g.dryrun, "Versioner") and hasattr(g.scope, "resolve_inside") and hasattr(g.config, "load_style")
    assert g.luau_safety.lint is luau.lint


# --- repo layout, docs, skills, schema -------------------------------------------------------------------------------------------------
def test_layout_satisfies_shared_rule_7():
    for p in ("econbal", "rules", "style", "library", "examples", "evals", "feedback", "skills", "adapters/mcp-clients", "tests", "README.md", "CLAUDE.md", "AGENTS.md", "projects.yaml",
              "projects", "references/README.md", "style/STYLE.md", "ASSET_LICENSING.md"):
        assert (ROOT / p).exists(), p


def test_library_style_skills_schema_and_baseline(project):
    from econbal import KINDS
    from econbal.domain.schema import DOMAIN_SCHEMA

    assert manifest.LibraryStore(project).validate_all(check_files=True)["ok"]
    assert config.validate_style(config.load_style(project)) == []
    assert agentfiles.validate_all_skills(ROOT) == [] and agentfiles.sync(ROOT, check=True) == []
    assert json.loads(project.schema_file.read_text()) == manifest.base_schema(KINDS, DOMAIN_SCHEMA)
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=ROOT)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((ROOT / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_scripts_are_current():
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "build_schema.py"), "--check"], capture_output=True).returncode == 0
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_agent_files.py"), "--check"], capture_output=True).returncode == 0


def test_eval_suite_is_big_enough_and_not_trivially_shaped(project):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    assert len(tasks) >= 45
    assert all(t["checks"] for t in tasks)
    ids = {t["id"] for t in tasks}
    for prefix in ("hand-", "toy-", "validate-", "rebalance-", "luau-", "import-", "normalise-", "scope-", "learning-", "budget-"):
        assert sum(i.startswith(prefix) for i in ids) >= 3, prefix
    bad_good = {"toy-wall-is-flagged", "toy-runaway-is-flagged", "toy-dominant-is-flagged", "toy-balanced-passes"}
    assert bad_good <= ids


def test_every_tool_has_an_output_budget_eval(project):
    ids = {t["id"] for t in evals.load_tasks(ROOT / "evals" / "tasks")}
    for t in tools(project):
        if t.name in ("record_decision", "promote_run", "find_past_corrections") or True:
            pass
    must = [t.name for t in tools(project) if t.name not in ("record_decision", "promote_run")]
    missing = [n for n in must if f"budget-{n.replace('_', '-')}" not in ids]
    assert not missing, f"add an output-size budget eval for: {missing}"


def test_evals_fail_when_the_system_is_broken(project, monkeypatch):
    """Vacuity guard: break the judge in five different ways and demand that the matching evals notice."""
    import econbal.domain.analysis as an
    import econbal.domain.luau as lu
    import econbal.domain.places as pl
    import econbal.domain.rebalance as rb

    monkeypatch.setattr(an, "timing_findings", lambda ctx, rows, sims: [])
    monkeypatch.setattr(lu, "lint", lambda code: [])
    real_resolve = pl.resolve
    monkeypatch.setattr(pl, "resolve", lambda project, pid, plid: real_resolve(project, pid or "demo_mine", plid or "main"))  # "helpfully" guesses the scope
    monkeypatch.setattr(pl, "match_studio", lambda entry, studios: {"matched_by": "anything", "studio_id": "x"})
    monkeypatch.setattr(rb, "_trial", lambda *a, **k: None)
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="broken", root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"toy-wall-is-flagged", "toy-wall-gap-and-cost-cliffs", "luau-lint-rejects-loadstring", "scope-missing-both-is-refused", "scope-import-refused-when-another-place-is-open",
            "scope-export-refused-when-another-place-is-open", "rebalance-wall-smallest-single-change"} <= failed


def test_evals_fail_when_the_simulator_is_wrong(project, monkeypatch):
    import econbal.domain.sim as sm

    real = sm.source_rates
    monkeypatch.setattr(sm, "source_rates", lambda *a, **k: {k2: v * 1.01 for k2, v in real(*a, **k).items()})
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="skew", root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"hand-single-step", "hand-two-steps", "toy-balanced-regular-steps-are-the-designed-ones"} & failed


def test_examples_measurements_match_the_code(project):
    from econbal.guide_adapter import manifest as mf

    store = mf.LibraryStore(project)
    for row in store.load():
        if row["_origin"] != "examples" or row["status"] != "curated" or row["kind"] == "playtest_note":
            continue
        raw = yaml.safe_load((ROOT / row["domain"]["spec_file"]).read_text())
        ctx = A.make_ctx(raw, config.load_style(project), A.load_rules(ROOT))
        res = A.check(ctx)
        assert res["verdict"] == row["domain"]["expected_verdict"], row["id"]
        assert sorted({f["code"] for f in res["findings"] if f["severity"] in ("error", "warning")}) == row["domain"]["expected_codes"], row["id"]
        for k, v in row["domain"]["measurements"].items():
            assert res["metrics"][k] == pytest.approx(v, abs=0.002), (row["id"], k)


def test_starter_bands_are_labelled_placeholders_everywhere(project):
    style = config.load_style(project)
    assert style["placeholder"] is True and "PLACEHOLDER" in style["name"]
    assert all(b["placeholder"] for b in style["target_bands"])
    brief = config.style_brief(style, ["pacing"])
    for b in style["target_bands"][:3]:
        assert f"{b['min_minutes']}-{b['max_minutes']}" in " ".join(style["traits"].values())
    assert "PLACEHOLDER" in brief
    assert "PLACEHOLDER" in (ROOT / "style" / "STYLE.md").read_text()
    res = call(project, "check_economy", **MAIN)
    assert "PLACEHOLDER" in res["summary"]


def test_readme_states_the_honest_limits_and_the_adapter():
    text = (ROOT / "README.md").read_text().lower()
    for phrase in ("never run against", "guide-core", "guide_adapter", "retention", "revenue", "placeholder", "archetype", "what you still need to supply"):
        assert phrase in text, phrase


def test_cli_end_to_end(project, capsys):
    run = cli.run
    assert run(project, hooks_mod.HOOKS, ["places"]) == 0 and "demo_mine/main" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["check", "--project-id", "demo_mine", "--place-id", "main"]) == 0
    assert '"verdict": "pass"' in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["check", "--project-id", "demo_tycoon", "--place-id", "main"]) == 1
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["check", "--project-id", "demo_mine", "--place-id", "nowhere"]) == 2
    assert "unknown place" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["table", "--project-id", "demo_mine", "--place-id", "main"]) == 0
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["plot", "--project-id", "demo_mine", "--place-id", "main"]) == 0
    assert capsys.readouterr().out.strip().endswith(".png")
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["domain"]["projects"]["places"][0]["spec_ok"] is True
    assert run(project, hooks_mod.HOOKS, ["call"]) == 0 and "check_economy" in capsys.readouterr().out


def test_real_stdio_smoke():
    smoke = Path("/home/user/workshop/suite/smoke_stdio.py")
    if not smoke.exists():
        pytest.skip("suite smoke script not present")
    proc = subprocess.run([sys.executable, str(smoke), str(ROOT), "econbal", "get_style_brief", json.dumps(MAIN)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    assert info["called"] == "get_style_brief" and not info["is_error"] and info["tool_count"] >= 20


def test_dependencies_are_declared():
    text = (ROOT / "pyproject.toml").read_text()
    assert "pillow" in text.lower() and "lupa" in text and "mcp" in text
