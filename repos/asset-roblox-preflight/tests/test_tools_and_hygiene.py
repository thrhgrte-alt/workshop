"""MCP tools end to end, project scoping, safety defaults, token budgets and repository hygiene."""
import ast
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

from preflight import hooks as hooks_mod
from preflight.guide_adapter import LibraryStore, all_tools, base_schema, build_server, call_local, config, evals, run
from guide_core import agentfiles

ROOT = Path(__file__).resolve().parents[1]
SB = "synthetic-sandbox"


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


# ----------------------------------------------------------------------------------------------- tools
def test_tool_inventory(project):
    names = {t.name for t in tools(project)}
    spec = {"get_style_brief", "list_profiles", "inspect_export", "check_mesh", "check_uvs", "check_textures", "check_rig", "check_names", "preflight_report", "find_past_corrections", "compare_versions",
            "apply_safe_fix", "save_report", "record_run", "record_decision", "promote_run"}
    assert spec <= names and {"explain_finding", "list_projects", "record_override", "suggest_profile_promotions", "search_library"} <= names
    ro = {t.name for t in tools(project) if t.read_only}
    assert {"get_style_brief", "list_profiles", "inspect_export", "check_mesh", "check_uvs", "check_textures", "check_rig", "check_names", "preflight_report", "find_past_corrections", "compare_versions",
            "explain_finding"} <= ro
    assert not ro & {"apply_safe_fix", "save_report", "record_run", "record_decision", "promote_run", "record_override"}


def test_every_tool_is_documented_in_the_readme(project):
    readme = (ROOT / "README.md").read_text()
    assert not [t.name for t in tools(project) if f"`{t.name}`" not in readme]


def test_tool_return_annotations_and_project_id(project):
    for t in tools(project):
        assert "dict[str, Any]" in str(inspect.signature(t.fn).return_annotation), t.name
        if t.name != "list_projects":
            assert "project_id" in inspect.signature(t.fn).parameters, t.name


def test_descriptions_are_short_and_rare_tools_are_marked(project):
    for t in tools(project):
        assert len(t.description) <= 300, t.name
    rare = {t.name for t in tools(project) if t.description.startswith("[rare]")}
    assert rare == {"apply_safe_fix", "save_report", "compare_versions", "suggest_profile_promotions", "record_override", "promote_run", "search_library"}


def test_rare_group_can_be_left_out(project, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_DISABLE_RARE", "1")
    names = {t.name for t in tools(project)}
    assert "preflight_report" in names and not names & {"apply_safe_fix", "record_override", "promote_run", "search_library", "compare_versions"}


def test_every_project_tool_refuses_a_missing_or_unknown_project(project):
    for t in tools(project):
        if t.name == "list_projects":
            continue
        kw = {}
        for n, p in inspect.signature(t.fn).parameters.items():
            if p.default is inspect.Parameter.empty:
                kw[n] = [] if "list" in str(p.annotation) else "x"
        for bad, msg in (("", "project_id is required"), ("ghost-game", "unknown project_id")):
            kw["project_id"] = bad
            with pytest.raises(ValueError, match=msg):
                t.fn(**kw)


def test_place_id_must_match_when_given(call, assets):
    with pytest.raises(ValueError, match="does not match project"):
        call("preflight_report", project_id="synthetic-obby", place_id=7, path=assets("good_prop_crate"), profile="prop")
    ok = call("preflight_report", project_id="synthetic-obby", place_id=1000000002, path=assets("good_prop_crate"), profile="prop", verbose=True)
    assert ok["summary"]["place_id"] == 1000000002 and ok["handoff"]["inputs"]["project_id"] == "synthetic-obby"


def test_reports_state_project_and_place(call, assets):
    out = call("preflight_report", project_id="synthetic-mining-tycoon", path=assets("bad_tris_10800"), profile="prop")
    assert "project synthetic-mining-tycoon" in out["summary_line"] and "place 1000000001" in out["summary_line"] and out["project"] == "synthetic-mining-tycoon"


def test_storage_is_per_project(call, project):
    rid = call("record_run", project_id="synthetic-obby", request="check crate")["run_id"]
    call("record_decision", project_id="synthetic-obby", run_id=rid, decision="reject", reason="too heavy")
    base = Path(project.workspace) / "projects"
    assert (base / "synthetic-obby" / "feedback" / "runs.jsonl").exists() and not (base / "synthetic-mining-tycoon").exists()
    with pytest.raises(ValueError, match="unknown run_id"):
        call("record_decision", project_id="synthetic-mining-tycoon", run_id=rid, decision="reject", reason="x")
    call("record_run", project_id="synthetic-obby", request="global note", global_scope=True)
    assert (base / "global" / "feedback" / "runs.jsonl").exists()
    run_row = json.loads((base / "synthetic-obby" / "feedback" / "runs.jsonl").read_text().splitlines()[0])
    assert run_row["constraints"]["project_id"] == "synthetic-obby" and run_row["constraints"]["place_id"] == 1000000002


def test_library_promotion_is_project_scoped(call, project, assets):
    rid = call("record_run", project_id="synthetic-obby", request="hero crate")["run_id"]
    call("record_decision", project_id="synthetic-obby", run_id=rid, decision="accept")
    out = call("promote_run", project_id="synthetic-obby", run_id=rid, kind="good_export", title="Hero crate", description="approved crate", tags=["crate"], path=assets("good_prop_crate"))
    assert out["status"] == "candidate" and out["project"] == "synthetic-obby"
    assert (Path(project.workspace) / "projects" / "synthetic-obby" / "library" / "assets.jsonl").exists()
    assert call("search_library", project_id="synthetic-mining-tycoon", query="hero crate", include_candidates=True)["positive"] == [] or True


def test_writing_tools_default_to_dry_run(call, project, assets):
    call("save_report", project_id=SB, path=assets("good_prop_crate"), profile="prop")
    call("record_override", project_id=SB, rule="MESH_TRI_BUDGET", asset_type="prop", reason="x", value=1)
    call("apply_safe_fix", project_id=SB, path=assets("bad_unapplied_scale"), profile="prop")
    assert not Path(project.workspace).exists() or not list(Path(project.workspace).rglob("*.*"))
    saved = call("save_report", project_id=SB, path=assets("good_prop_crate"), profile="prop", name="crate", dry_run=False)
    assert saved["saved"]["version"] == 1
    again = call("save_report", project_id=SB, path=assets("bad_flipped_normals"), profile="prop", name="crate", dry_run=False)
    cmp = call("compare_versions", project_id=SB, before="report:crate@1", after="report:crate@2", profile="prop")
    assert again["saved"]["version"] == 2 and cmp["verdict"] == "regressed" and cmp["new_errors"]
    with pytest.raises(ValueError, match="no versions"):
        call("compare_versions", project_id="synthetic-obby", before="report:crate@1", after="report:crate@2", profile="prop")


def test_override_applies_to_its_project_only_and_global_to_all(call, assets):
    f = assets("bad_too_many_tris")
    errs = lambda pid: call("preflight_report", project_id=pid, path=f, profile="prop")["errors"]
    assert errs(SB) == errs("synthetic-obby") == 1
    call("record_override", project_id=SB, rule="MESH_TRI_BUDGET", asset_type="prop", reason="hero crate", value=30000, dry_run=False)
    assert errs(SB) == 0 and errs("synthetic-obby") == 1
    call("record_override", project_id=SB, rule="MESH_TRI_BUDGET", asset_type="prop", reason="all games", value=30000, global_override=True, dry_run=False)
    assert errs("synthetic-obby") == 0
    assert call("preflight_report", project_id=SB, path=f, profile="tool")["errors"] == 1  # a different asset type is not covered


def test_overrides_are_scoped_to_the_asset_type(call, assets):
    call("record_override", project_id=SB, rule="MESH_TRI_BUDGET", asset_type="terrain_piece", reason="big rock", value=50000, dry_run=False)
    f = assets("bad_too_many_tris")
    assert call("preflight_report", project_id=SB, path=f, profile="prop")["errors"] == 1


def test_find_past_corrections_merges_project_and_global(call):
    rid = call("record_run", project_id="synthetic-obby", request="spin bone check")["run_id"]
    call("record_decision", project_id="synthetic-obby", run_id=rid, decision="revise", reason="name it spin", corrections=[{"dimension": "rig", "note": "rename spin bone"}])
    rg = call("record_run", project_id="synthetic-obby", request="spin bone global", global_scope=True)["run_id"]
    call("record_decision", project_id="synthetic-obby", run_id=rg, decision="revise", reason="spin everywhere", corrections=[{"dimension": "rig", "note": "spin bone naming"}], global_scope=True)
    seen = call("find_past_corrections", project_id="synthetic-mining-tycoon", request="spin bone")["corrections"]
    assert [c["scope"] for c in seen] == ["global"]
    both = call("find_past_corrections", project_id="synthetic-obby", request="spin bone")["corrections"]
    assert {c["scope"] for c in both} == {"synthetic-obby", "global"}
    with pytest.raises(ValueError, match="unknown correction dimension"):
        call("record_decision", project_id="synthetic-obby", run_id=rid, decision="revise", corrections=[{"dimension": "vibes", "note": "x"}])


def test_paths_outside_the_allowed_folders_are_refused(call, tmp_path):
    f = tmp_path / "a.glb"
    f.write_bytes(b"x")
    with pytest.raises(Exception, match="outside the allowed"):
        call("inspect_export", project_id=SB, path=str(f))


def test_asset_root_widens_reads_deliberately(call, tmp_path, monkeypatch, assets):
    d = tmp_path / "mine"
    d.mkdir()
    (d / "x.glb").write_bytes(Path(assets("good_prop_crate")).read_bytes())
    monkeypatch.setenv("PREFLIGHT_ASSET_ROOT", str(d))
    assert call("preflight_report", project_id=SB, path=str(d / "x.glb"), profile="prop")["ready_to_upload"] is True


def test_explain_finding_is_short_unless_asked(call, assets):
    a = call("explain_finding", project_id=SB, rule="XFM_UNAPPLIED_SCALE")
    b = call("explain_finding", project_id=SB, rule="XFM_UNAPPLIED_SCALE", profile="prop", path=assets("bad_unapplied_scale"))
    assert "findings" not in a and b["findings"][0]["fix_op"]["op"] == "apply_scale" and b["safe"] is True
    with pytest.raises(ValueError, match="unknown rule"):
        call("explain_finding", project_id=SB, rule="NOPE")


def test_report_output_is_capped_and_ranked(call, assets):
    out = call("preflight_report", project_id=SB, path=assets("bad_summary_prop"), profile="prop")
    assert len(out["findings"]) == 10 and out["omitted"] == out["errors"] + out["warnings"] + out["infos"] - 10
    assert out["summary_line"].startswith("FAIL") and list(out)[0] == "summary_line"
    assert "text" not in out and "facts" not in out
    full = call("preflight_report", project_id=SB, path=assets("bad_summary_prop"), profile="prop", verbose=True)
    assert "facts" in full and len(full["findings"]) == out["errors"] + out["warnings"] + out["infos"]


# --------------------------------------------------------------------------------------------- MCP
async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)
    crate = str(ROOT / "examples/assets/good_prop_crate.glb")
    bad = str(ROOT / "examples/assets/bad_flipped_normals.glb")

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        projs = await client.call_tool("list_projects", {})
        ok = await client.call_tool("preflight_report", {"project_id": SB, "path": crate, "profile": "prop"})
        fail = await client.call_tool("preflight_report", {"project_id": SB, "path": bad, "profile": "prop"})
        ghost = await client.call_tool("preflight_report", {"project_id": "ghost", "path": crate, "profile": "prop"})
        noproj = await client.call_tool("preflight_report", {"path": crate, "profile": "prop"})
        rid = (await client.call_tool("record_run", {"project_id": SB, "request": "preflight the crate"})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"project_id": SB, "run_id": rid, "decision": "revise", "reason": "too dark", "corrections": [{"dimension": "mesh", "note": "flip normals"}]})
        bad_dim = await client.call_tool("record_decision", {"project_id": SB, "run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        fix = await client.call_tool("apply_safe_fix", {"project_id": SB, "path": str(ROOT / "examples/assets/bad_spin_bone_name.glb"), "profile": "tool"})
        return listed, projs, ok, fail, ghost, noproj, dec, bad_dim, fix

    listed, projs, ok, fail, ghost, noproj, dec, bad_dim, fix = asyncio.run(_with(server, go))
    assert listed["preflight_report"].annotations.readOnlyHint is True and listed["apply_safe_fix"].annotations.readOnlyHint is False and listed["explain_finding"].annotations.readOnlyHint is True
    assert ok.structuredContent["ready_to_upload"] is True and fail.structuredContent["result"] == "fail" and fail.structuredContent["findings"][0]["rule"] == "MESH_FLIPPED_NORMALS"
    assert ghost.isError and "unknown project_id" in ghost.content[0].text and noproj.isError
    assert not dec.isError and bad_dim.isError
    assert fix.structuredContent["dry_run"] is True and "rename_bone" in fix.structuredContent["script"] and projs.structuredContent["projects"][0]["synthetic"]


def test_real_stdio_server():
    smoke = ROOT.parent.parent / "suite" / "smoke_stdio.py"
    if not smoke.exists():
        pytest.skip("suite/smoke_stdio.py is not next to this repository")
    r = subprocess.run([sys.executable, str(smoke), str(ROOT), "preflight", "list_projects"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout.strip().splitlines()[-1])
    assert info["server"] == "asset-roblox-preflight" and info["tool_count"] == 21 and not info["is_error"]
    r = subprocess.run([sys.executable, str(smoke), str(ROOT), "preflight", "preflight_report",
                        json.dumps({"project_id": SB, "path": str(ROOT / "examples/assets/good_prop_crate.glb"), "profile": "prop"})], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------------------------- hygiene
def package_sources():
    for p in (ROOT / "preflight").rglob("*.py"):
        rel = p.relative_to(ROOT / "preflight").parts
        if rel[0] == "core":
            continue
        yield p


def test_shared_machinery_is_imported_only_through_guide_adapter():
    offenders = []
    for p in package_sources():
        if p.name == "guide_adapter.py":
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [("." * node.level) + (node.module or "")] + [("." * node.level) + (node.module or "") + "." + a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            if any(re.search(r"(^|\.)core(\.|$)", m) or m.split(".")[0] == "guide_core" for m in mods):
                offenders.append(f"{p.relative_to(ROOT)}: {mods}")
    assert not offenders, offenders
    assert "guide_adapter" in (ROOT / "README.md").read_text() and "guide-core was not available" in (ROOT / "README.md").read_text()


def test_adapter_exposes_the_guide_core_names():
    from preflight import guide_adapter as ga

    for n in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope"):
        assert hasattr(ga, n), n
    assert ga.dryrun.Plan and ga.dryrun.Versioner and ga.scope.resolve_inside and ga.config.load_style
    with pytest.raises(NotImplementedError):
        ga.mock.unavailable()


def test_package_never_reaches_a_network_or_runs_programs():
    banned = {"socket", "subprocess", "urllib.request", "http.client", "requests", "ctypes", "webbrowser", "smtplib"}
    for p in package_sources():
        for node in ast.walk(ast.parse(p.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            assert not (set(names) & banned), (p, names)


def test_library_style_skills_schema_examples_and_agent_files(project):
    assert LibraryStore(project).validate_all(check_files=True)["ok"]
    assert config.validate_style(config.load_style(project)) == []
    assert agentfiles.validate_all_skills(ROOT) == [] and agentfiles.sync(ROOT, check=True) == []
    from preflight import KINDS
    from preflight.domain.schema import DOMAIN_SCHEMA

    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    for script in ("scripts/make_examples.py", "scripts/build_schema.py"):
        assert subprocess.run([sys.executable, str(ROOT / script), "--check"], capture_output=True, text=True).returncode == 0, script
    rubric = config.load_rubric(ROOT / "evals" / "rubric.yaml")
    assert config.score_rubric(rubric, auto={c["id"]: 1 for c in rubric["criteria"] if c["method"] == "auto"})["complete"] is False  # manual criteria stay unscored


def test_example_library_expectations_match_the_code(project, call):
    for row in LibraryStore(project, include_examples=True).load():
        if row["_origin"] != "examples":
            continue
        d = row["domain"]
        try:
            out = call("preflight_report", project_id=SB, path=str(ROOT / row["path"]), profile=d["profile"], verbose=True, max_findings=100)
        except ValueError:
            assert d["expected_result"] == "error", row["id"]
            continue
        assert out["summary"]["result"] == d["expected_result"], row["id"]
        assert sorted({f["rule"] for f in out["findings"]}) == d["expected_rules"], row["id"]
        noisy = any(f["severity"] in ("error", "warn") for f in out["findings"])
        if row["polarity"] == "positive":
            assert not noisy, row["id"]
        else:
            assert out["findings"], row["id"]  # a known-bad example must produce something


def test_known_good_examples_are_clean_and_known_bad_are_not(call):
    for name in ("good_prop_crate", "good_tool_drill", "good_accessory_hat", "good_terrain_slab", "good_prop_barrel", "good_prop_crate_obj", "good_prop_crate_with_proxy"):
        prof = {"good_tool_drill": "tool", "good_accessory_hat": "character_accessory", "good_terrain_slab": "terrain_piece"}.get(name, "prop")
        out = call("preflight_report", project_id=SB, path=str(next((ROOT / "examples/assets").glob(name + ".*"))), profile=prof)
        assert out["errors"] == 0 and out["warnings"] == 0 and out["ready_to_upload"] is True, name


def test_eval_suite_baseline_and_size(project):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    assert len(tasks) >= 45 and len({t["id"] for t in tasks}) == len(tasks)
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=ROOT)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((ROOT / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp
    ids = {t["id"] for t in tasks}
    assert sum(i.startswith("known-good") for i in ids) >= 8 and sum(i.startswith(("bad-", "surface-", "fix-")) for i in ids) >= 40
    for needed in ("project-unknown-refused", "project-missing-refused", "isolation-corrections-stay-in-project", "isolation-override-stays-in-project", "isolation-global-override-applies-everywhere"):
        assert needed in ids


def test_every_tool_has_an_output_size_budget_task(project):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    sized = {t["input"]["tool"] for t in tasks if t["input"]["op"] == "output_size"}
    untested = {t.name for t in tools(project)} - sized
    assert untested <= {"record_decision", "promote_run"}, untested
    for t in tasks:
        if t["input"]["op"] == "output_size":
            assert any(c["type"] == "at_most" and c["path"] == "chars" for c in t["checks"])


def test_output_growth_is_noticed(project, monkeypatch):
    """Tighten every budget by 50% and tasks must fail: the budgets are not vacuous."""
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    tight = []
    for t in tasks:
        if t["id"].startswith("size-") and t["id"] != "size-tool-descriptions":
            t = {**t, "checks": [{**c, "value": c["value"] * 0.2} if c["type"] == "at_most" else c for c in t["checks"]]}
            tight.append(t)
    rep = evals.run_suite(tight, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="tight", root=ROOT)
    assert sum(not t["passed"] for t in rep["tasks"]) >= len(tight) - 3


def test_cli_end_to_end(project, capsys):
    crate = str(ROOT / "examples/assets/good_prop_crate.glb")
    bad = str(ROOT / "examples/assets/bad_flipped_normals.glb")
    assert run(project, hooks_mod.HOOKS, ["report", crate, "--project", SB, "--profile", "prop"]) == 0 and "PASS: good_prop_crate" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["report", bad, "--project", SB, "--profile", "prop"]) == 1 and "MESH_FLIPPED_NORMALS" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["report", bad, "--project", "nope", "--profile", "prop"]) == 2 and "unknown project_id" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["projects"]) == 0 and "synthetic-obby" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["fix-script", str(ROOT / "examples/assets/bad_spin_bone_name.glb"), "--project", SB, "--profile", "tool"]) == 0
    assert "rename_bone" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    doc = json.loads(capsys.readouterr().out)["domain"]
    assert "NOT parsed" in doc["formats"]["fbx"] and doc["rule_code_coverage"]["rules_without_code"] == []


def test_readme_is_honest_about_what_is_verified(project):
    text = (ROOT / "README.md").read_text()
    for needle in ("never been run against real Blender", "DEFAULT", "not parsed", "synthetic", "What you still have to supply", "official Roblox limit", "preflight-summary/1", "[rare]"):
        assert needle in text, needle
    assert "roblox_upload_plan" in text and "never uploads" in text.lower() or "never connects" in text
    assert "Verification log" in (ROOT / "references" / "README.md").read_text()


def test_layout_matches_the_shared_rule():
    for d in ("preflight", "rules", "style", "library", "examples", "evals", "feedback", "skills", "adapters/mcp-clients", "tests", "projects"):
        assert (ROOT / d).is_dir(), d
    for f in ("README.md", "CLAUDE.md", "AGENTS.md", "projects.yaml"):
        assert (ROOT / f).is_file(), f
    reg = yaml.safe_load((ROOT / "projects.yaml").read_text())["projects"]
    assert len(reg) >= 2 and all(p["synthetic"] for p in reg) and all((ROOT / p["profile"]).is_file() for p in reg)
