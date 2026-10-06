"""MCP tools end to end, scope and safety defaults, the shared-machinery boundary, docs, evals (including a guard against vacuous evals) and repository hygiene."""
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

from conftest import MAIN, STUDIO
from playqa import KINDS, hooks as hooks_mod
from playqa.domain.harness import run_world_check
from playqa.domain.schema import DOMAIN_SCHEMA
from playqa.guide_adapter import agentfiles, all_tools, config, evals, manifest, mcpkit

ROOT = Path(__file__).resolve().parents[1]
READ_ONLY = {"list_checks", "plan_playtest", "parse_console_log", "compare_baseline", "explain_failure", "find_past_corrections"}
WRITERS = {"generate_check_script", "save_baseline", "record_result", "record_run", "record_decision", "promote_run"}
SPEC_TOOLS = {"list_checks", "plan_playtest", "parse_console_log", "compare_baseline", "explain_failure", "find_past_corrections", "generate_check_script", "save_baseline", "record_result",
              "record_run", "record_decision", "promote_run"}  # exactly the tool list of the spec for studio-playtest-qa


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return mcpkit.call_local(tools(project), tool, kw)


def test_the_tool_list_is_exactly_the_specs_and_labelled(project):
    by = {t.name: t for t in tools(project)}
    assert set(by) == SPEC_TOOLS
    assert all(by[n].read_only for n in READ_ONLY) and all(not by[n].read_only for n in WRITERS)


def test_tool_functions_are_annotated_scoped_and_writers_default_to_dry_run(project):
    for t in tools(project):
        sig = inspect.signature(t.fn).parameters
        assert "dict" in str(inspect.signature(t.fn).return_annotation)
        assert "project_id" in sig and "place_id" in sig and sig["project_id"].default is None and sig["place_id"].default is None, t.name
        if not t.read_only:
            assert "dry_run" in sig and sig["dry_run"].default is True, t.name
        else:
            assert "dry_run" not in sig, t.name
        assert len(t.description) <= 300, (t.name, len(t.description))


def test_descriptions_are_concise_and_the_rare_group_is_marked(project):
    for t in tools(project):
        assert len(t.description) <= 300
    assert {t.name for t in tools(project) if t.effective_group == "rare"} == {"compare_baseline", "save_baseline", "promote_run"}
    assert all(t.description.startswith("[rare]") for t in tools(project) if t.effective_group == "rare")


def test_rare_group_can_be_disabled(project, monkeypatch):
    all_names = {t.name for t in tools(project)}
    monkeypatch.setenv("PLAYQA_DISABLE_GROUPS", "rare")
    core = {t.name for t in tools(project)}
    assert all_names - core == {"compare_baseline", "save_baseline", "promote_run"}
    assert {"list_checks", "plan_playtest", "explain_failure", "generate_check_script", "record_result"} <= core


def test_unscoped_core_tools_are_replaced_by_scoped_ones(project):
    names = [t.name for t in tools(project)]
    assert len(names) == len(set(names)) and "search_library" not in names and "get_style_brief" not in names
    with pytest.raises(ValueError, match="missing project_id and place_id"):
        call(project, "record_run", request="x")


def test_summary_is_the_first_key_and_scope_is_stated(project):
    for tool, kw in (("list_checks", {}), ("plan_playtest", {"studios": STUDIO}), ("parse_console_log", {"console_log": "10:00:00.000 hi"}), ("find_past_corrections", {"request": "x"}),
                     ("generate_check_script", {"studios": STUDIO, "check_id": "boot"}), ("record_run", {"request": "x"})):
        res = call(project, tool, **MAIN, **kw)
        assert next(iter(res)) == "summary" and res["project_id"] == "demo_mine" and res["place_id"] == "main" and res["synthetic_place"] is True, tool


def test_dry_runs_write_nothing_and_real_writes_go_to_the_place_folder(project):
    ws = project.workspace
    dry = call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="boot")
    assert dry["dry_run"] is True and "luau" not in dry and dry["lint"] == [] and dry["steps"][0]["op"] == "write" and not ws.exists()
    real = call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="boot", dry_run=False)
    path = Path(real["path"])
    assert path.read_text() == real["luau"] and (ws / "projects" / "demo_mine" / "main") in path.parents and real["dry_run"] is False
    assert dry["sha256"] == real["sha256"]


def test_generated_scripts_are_not_written_to_another_place(project):
    call(project, "generate_check_script", project_id="demo_tycoon", place_id="main", studios=[{"name": "x", "place_id": 9000000001, "studio_id": "s"}], check_id="boot", dry_run=False)
    written = sorted(str(p.relative_to(project.workspace)) for p in project.workspace.rglob("*") if p.is_file())
    assert written == ["projects/demo_tycoon/main/output/scripts/boot-1.luau"]


def test_tools_are_deterministic(project):
    from playqa.domain.mockworld import World

    raw = World().run(call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="boot", dry_run=False)["luau"])
    for tool, kw in (("list_checks", {"detail": True}), ("plan_playtest", {"studios": STUDIO, "detail": True}), ("explain_failure", {"check_id": "boot", "results": [raw], "console_log": "10:00:00.000 hi"}),
                     ("parse_console_log", {"console_log": "10:00:00.000 hi"})):
        assert call(project, tool, **MAIN, **kw) == call(project, tool, **MAIN, **kw)


def test_plan_names_the_hub_calls_in_order_and_repeats_flaky_checks(project):
    res = call(project, "plan_playtest", **MAIN, studios=STUDIO, detail=True)
    sessions = res["sessions"]
    runs = [s for sess in sessions for s in sess["steps"] if s.startswith("execute_luau")]
    assert sum(1 for r in runs if r.startswith("execute_luau remotes#")) == 3 and sum(1 for r in runs if r.startswith("execute_luau economy_smoke#")) == 3
    assert sum(1 for r in runs if r.startswith("execute_luau perf_snapshot#")) == 6 and sum(1 for r in runs if r.startswith("execute_luau boot#")) == 1 and sum(1 for r in runs if r.startswith("execute_luau data_roundtrip#")) == 1
    first = sessions[0]
    assert first["steps"][0] == "start_play" and first["steps"][-1] == "stop_play" and first["steps"][1].startswith("execute_luau boot#1")
    assert any(s.startswith("get_console_output") for s in first["steps"]) and any(s.startswith("screen_capture") for s in first["steps"])
    perf = next(s for s in sessions if s["checks"] == ["perf_snapshot"])
    assert [x.split(" ")[0] for x in perf["steps"][1:4]] == ["execute_luau", "wait", "execute_luau"] and "phase=start" in perf["steps"][1] and "phase=end" in perf["steps"][3]
    assert res["input_status"] == "schema_unverified" and set(res["hub_calls"]) == {"roblox_studio_start_stop_play", "execute_luau", "get_console_output", "screen_capture"}
    assert len(res["sha256_prefix"]) == len(runs)
    short = call(project, "plan_playtest", **MAIN, studios=STUDIO, repeats=2)
    assert sum(1 for sess in short["sessions"] for s in sess["steps"] if s.startswith("execute_luau remotes#")) == 2


def test_the_plan_sha_matches_the_script_the_tool_returns(project):
    plan = call(project, "plan_playtest", **MAIN, studios=STUDIO, detail=True, check_ids=["economy_smoke"], repeats=2)
    for rep in (1, 2):
        real = call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="economy_smoke", repeat_no=rep)
        assert plan["sha256_prefix"][f"economy_smoke#{rep}"] == real["sha256"][:10]


def test_checks_a_place_does_not_configure_are_skipped_not_invented(project):
    res = call(project, "plan_playtest", project_id="demo_obby", place_id="main", studios=[{"name": "Demo Obby (SYNTHETIC)", "place_id": 0, "studio_id": "s"}])
    ran = {c for s in res["sessions"] for c in s["checks"]}
    assert ran == {"boot", "spawn", "reachability"} and {s["check"] for s in res["skipped"]} == {"economy_smoke", "perf_snapshot", "remotes", "data_roundtrip"}


def test_record_flow_and_flaky_hint(project):
    from playqa.domain.mockworld import World

    w = World()
    luau = call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="spawn", dry_run=False)["luau"]
    ok = w.run(luau)
    bad = World(["spawn_stuck"]).run(luau)
    verdicts = []
    for raw in (ok, bad, ok):
        res = call(project, "record_result", **MAIN, check_id="spawn", results=[raw], dry_run=False)
        verdicts.append((res["verdict"], res.get("flaky_hint")))
    assert [v for v, _ in verdicts] == ["pass", "fail", "pass"]
    assert verdicts[0][1] is None and verdicts[2][1] and "consider adding it to `flaky:`" in verdicts[2][1]
    rows = [json.loads(l) for l in (project.workspace / "projects" / "demo_mine" / "main" / "results" / "results.jsonl").read_text().splitlines()]
    assert [r["verdict"] for r in rows] == ["pass", "fail", "pass"] and rows[1]["failed_assertions"] == ["not_stuck"]
    assert not (project.workspace / "projects" / "demo_tycoon").exists()


def test_baseline_save_compare_and_versions(project):
    from playqa.domain.mockworld import World

    def perf(world):
        s = world.run(call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="perf_snapshot", phase="start", dry_run=False)["luau"])
        world.advance(300)
        e = world.run(call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="perf_snapshot", phase="end", dry_run=False)["luau"])
        return [s, e]

    none = call(project, "compare_baseline", **MAIN, results=perf(World()))
    assert "baseline" not in none and "nothing was compared" in none["summary"]
    dry = call(project, "save_baseline", **MAIN, results=perf(World()))
    assert dry["dry_run"] is True and not (project.workspace / "projects" / "demo_mine" / "main" / "versions").exists()
    saved = call(project, "save_baseline", **MAIN, results=perf(World()), dry_run=False, note="first")
    assert saved["dry_run"] is False and saved["baseline_name"] == "default"
    w = World()
    w.add_parts(3)
    bad = call(project, "compare_baseline", **MAIN, results=perf(w))
    assert "4 of 8 comparable" not in bad["summary"] and "2 of 8" in bad["summary"] and {r["metric"] for r in bad["rows"]} == {"part_count"}
    again = call(project, "save_baseline", **MAIN, results=perf(w), dry_run=False)
    assert again["dry_run"] is False
    from playqa.domain import store
    from playqa.guide_adapter import scope as S

    hist = store.versioner(project, S.Scope("demo_mine", "main")).history("baseline-default")
    assert [h["version"] for h in hist] == [1, 2]
    with pytest.raises(ValueError, match="both a 'start' and an 'end'"):
        call(project, "save_baseline", **MAIN, results=perf(World())[:1])


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = mcpkit.build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        checks = await client.call_tool("list_checks", MAIN)
        no_scope = await client.call_tool("list_checks", {})
        wrong = await client.call_tool("plan_playtest", {**MAIN, "studios": [{"name": "Demo Tycoon (SYNTHETIC)", "place_id": 9000000001}]})
        rid = (await client.call_tool("record_run", {**MAIN, "request": "boot window", "dry_run": False})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "reason": "window", "corrections": [{"dimension": "threshold", "note": "n"}], "dry_run": False})
        bad = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, checks, no_scope, wrong, dec, bad

    listed, checks, no_scope, wrong, dec, bad = asyncio.run(_with(server, go))
    assert listed["list_checks"].annotations.readOnlyHint is True and listed["generate_check_script"].annotations.readOnlyHint is False
    assert "7 can run" in checks.structuredContent["summary"] and no_scope.isError and "missing project_id and place_id" in no_scope.content[0].text
    assert wrong.isError and "are not place 'demo_mine/main'" in wrong.content[0].text
    assert not dec.isError and bad.isError


def test_real_stdio_smoke():
    smoke = Path("/home/user/workshop/suite/smoke_stdio.py")
    if not smoke.exists():
        pytest.skip("suite smoke script not present")
    proc = subprocess.run([sys.executable, str(smoke), str(ROOT), "playqa", "list_checks", json.dumps(MAIN)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    assert info["called"] == "list_checks" and not info["is_error"] and info["tool_count"] == 12 and info["read_only"] == 6


# --- the shared-machinery boundary --------------------------------------------------------------------------------------------------------------
def test_only_the_adapter_imports_shared_machinery():
    pkg = ROOT / "playqa"
    offenders = []
    for f in pkg.rglob("*.py"):
        if f.name == "guide_adapter.py":
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"(from\s+\.+core\b|import\s+\.+core\b|playqa\.core\b|from\s+guide_core\b|import\s+guide_core\b)", text):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"import shared machinery only via playqa.guide_adapter: {offenders}"
    assert not (pkg / "core").exists() and not (ROOT / "tests" / "core_suite").exists()


def test_the_adapter_exposes_the_named_interfaces():
    from playqa import guide_adapter as g

    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope", "params", "promote", "skillgen", "observe"):
        assert hasattr(g, name), name
    assert hasattr(g.dryrun, "Plan") and hasattr(g.scope, "match_open_studio") and hasattr(g.luau_safety, "assert_safe") and hasattr(g.mock, "MockRoblox") and hasattr(g.config, "load_style")


def test_nothing_in_the_package_can_reach_the_network_or_studio():
    text = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "playqa").rglob("*.py"))
    for needle in ("import socket", "import urllib", "import requests", "import http.client", "import subprocess", "import ssl", "from urllib", "webbrowser"):
        assert needle not in text, needle


# --- layout, docs, skills, schema ---------------------------------------------------------------------------------------------------------------
def test_layout_satisfies_the_spec():
    for p in ("playqa", "playqa/guide_adapter.py", "rules", "style", "library", "examples", "evals", "evals/real/README.md", "feedback", "skills/studio-playtest-qa-workflow/SKILL.md", "adapters/mcp-clients", "tests", "samples/README.md",
              "README.md", "CLAUDE.md", "AGENTS.md", "projects.yaml", "pyproject.toml", "style/STYLE.md", "scripts/sync_agent_files.py", "scripts/make_examples.py"):
        assert (ROOT / p).exists(), p
    assert not list((ROOT / "evals" / "real").glob("*.y*ml")), "evals/real/ ships empty: real examples are the user's to add"


def test_the_registry_has_three_clearly_synthetic_projects():
    reg = yaml.safe_load((ROOT / "projects.yaml").read_text())
    assert len(reg["projects"]) == 3 and all(p["synthetic"] is True and "SYNTHETIC" in p["alias"] for p in reg["projects"])
    assert all("SYNTHETIC" in pl["studio_name"] for p in reg["projects"] for pl in p["places"])


def test_every_tool_is_documented_in_the_readme(project):
    readme = (ROOT / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_readme_states_the_honest_limits_and_the_adapter():
    text = (ROOT / "README.md").read_text().lower()
    for phrase in ("never run against", "guide-core", "guide_adapter", "schema_unverified", "placeholder", "self-written", "evals/real", "what you still need to supply", "test mode", "flaky"):
        assert phrase in text, phrase


def test_every_parser_is_marked_schema_unverified_in_readme_and_output(project):
    readme = (ROOT / "README.md").read_text()
    for what in ("execute_luau", "get_console_output", "screen_capture", "list_roblox_studios", "roblox_studio_start_stop_play"):
        assert what in readme
    assert call(project, "list_checks", **MAIN)["input_status"] == "schema_unverified"
    assert call(project, "parse_console_log", **MAIN, console_log="10:00:00.000 x")["summary"].endswith("schema_unverified")
    assert call(project, "plan_playtest", **MAIN, studios=STUDIO)["input_status"] == "schema_unverified"
    assert call(project, "plan_playtest", **MAIN, studios=STUDIO)["studio"]["input_schema"] == "schema_unverified"
    from playqa.domain.mockworld import World

    raw = World().run(call(project, "generate_check_script", **MAIN, studios=STUDIO, check_id="boot", dry_run=False)["luau"])
    assert call(project, "explain_failure", **MAIN, check_id="boot", results=[raw], console_log="10:00:00.000 x")["input_status"] == "schema_unverified"


def test_samples_readme_names_the_hub_call_and_the_save_path_for_every_parsed_output():
    text = (ROOT / "samples" / "README.md").read_text()
    for needle in ("execute_luau", "get_console_output", "screen_capture", "list_roblox_studios", "roblox_studio_start_stop_play", "schema_unverified", "SYNTHETIC", "samples/execute_luau/", "samples/get_console_output/"):
        assert needle in text, needle
    for f in (ROOT / "samples").rglob("*.json"):
        assert "_synthetic" in json.loads(f.read_text()), f


def test_synthetic_samples_parse_with_the_documented_parsers(project):
    from playqa.domain.schema import extract_result

    for name, expect in (("boot-clean.json", "boot"), ("boot-error.json", "boot")):
        doc, err, notes = extract_result(json.loads((ROOT / "samples" / "execute_luau" / name).read_text()))
        assert doc and doc["check_id"] == expect and err is None
    doc, err, _ = extract_result(json.loads((ROOT / "samples" / "execute_luau" / "place-guard-refusal.json").read_text()))
    assert doc is None and "wrong place" in err
    res = call(project, "parse_console_log", **MAIN, console_log=(ROOT / "samples" / "get_console_output" / "boot-error.txt").read_text())
    assert res["errors"] == 1
    res = call(project, "parse_console_log", **MAIN, console_log=json.loads((ROOT / "samples" / "get_console_output" / "boot-error-entries.json").read_text()))
    assert res["errors"] == 1


def test_library_style_skills_schema_and_baseline(project):
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


def test_examples_are_current_and_match_the_code(project):
    """The synthetic library entries record a verdict and failed assertions; re-judge the stored bundles and require the same."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_examples_mod", ROOT / "scripts" / "make_examples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = manifest.LibraryStore(project)
    seen = 0
    for row in store.load():
        if row["_origin"] != "examples" or row["kind"] == "baseline":
            continue
        bundle = json.loads((ROOT / row["domain"]["result_file"]).read_text())
        assert "SYNTHETIC" in bundle["_synthetic"]
        base = json.loads((ROOT / "examples" / "baselines" / f"{row['domain']['baseline_name']}.json").read_text()) if row["domain"].get("baseline_name") else None
        agg, rep = mod.judge_bundle(bundle, baseline=base)
        assert rep["verdict"] == row["domain"]["verdict"], row["id"]
        assert sorted({f["assertion"] + (f"[{f['subject']}]" if f.get("subject") else "") for f in rep.get("failures", [])}) == row["domain"]["failed_assertions"], row["id"]
        if "measures" in row["domain"]:
            assert rep["failures"][0]["evidence"]["measured"] == pytest.approx(row["domain"]["measures"]["first_failure_measured"]), row["id"]
        seen += 1
    assert seen == 7
    before = {p: p.read_bytes() for p in (ROOT / "examples" / "results").glob("*.json")}
    for p, data in before.items():
        assert json.loads(data)["check_id"]


# --- evals -----------------------------------------------------------------------------------------------------------------------------------------
def test_eval_suite_is_big_enough_and_not_trivially_shaped(project):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks", strict=True)
    assert len(tasks) >= 45
    assert all(t["checks"] for t in tasks)
    ids = {t["id"] for t in tasks}
    for prefix in ("boot-", "console-", "spawn-", "reach-", "remotes-", "economy-", "data-", "perf-", "agg-", "scope-", "studio-", "evidence-", "gen-", "learning-", "budget-", "shape-"):
        assert sum(i.startswith(prefix) for i in ids) >= 3, prefix
    must = {"boot-clean-passes", "boot-script-error-is-caught", "reach-clean-passes", "reach-unreachable-room-is-caught", "remotes-clean-passes", "remotes-erroring-handler-is-caught", "economy-clean-passes",
            "economy-currency-bug-is-caught", "data-check-without-test-mode-switch-is-refused", "studio-wrong-open-place-refuses-the-plan", "scope-missing-both-is-refused"}
    assert must <= ids
    tags = {tg for t in tasks for tg in t.get("tags", [])}
    assert {"known-good", "planted-fault", "refusal", "hand-computed"} <= tags


def test_refusal_cases_are_in_the_evals():
    tasks = evals.load_tasks(ROOT / "evals" / "tasks", strict=True)
    refusals = [t for t in tasks if "refusal" in t.get("tags", [])]
    assert len(refusals) >= 15
    ids = {t["id"] for t in refusals}
    assert {"data-check-without-test-mode-switch-is-refused", "studio-wrong-open-place-refuses-the-plan", "scope-missing-both-is-refused", "scope-missing-place-is-refused", "scope-missing-project-is-refused"} <= ids


def test_every_tool_has_an_output_budget_eval(project):
    ids = {t["id"] for t in evals.load_tasks(ROOT / "evals" / "tasks")}
    missing = [t.name for t in tools(project) if f"budget-{t.name.replace('_', '-')}" not in ids]
    assert not missing, f"add an output-size budget eval for: {missing}"


def test_the_real_eval_folder_is_reported_as_zero_not_as_a_pass(project):
    split = evals.load_split(ROOT / "evals" / "tasks", ROOT / "evals" / "real")
    assert len(split["real"]) == 0
    rep = evals.run_split(split, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=ROOT)
    md = evals.render_markdown(rep)
    assert rep["real_count"] == 0 and "0 cases" in md and "agrees with itself" in md


def test_evals_fail_when_the_judge_is_broken(project, monkeypatch):
    """Vacuity guard: break the tool in several different ways and demand that the matching evals notice."""
    from playqa.domain import evaluate as E, gen, places, reach, report as RP, baseline as B
    from playqa.guide_adapter import luau_safety, scope as S

    monkeypatch.setattr(reach, "flood", lambda areas, edges, min_waypoints: {"reached": {a: {"parent": "spawn", "hops": 1, "edge": {"from": "spawn", "to": a, "status": "Success", "waypoints": 9, "length": 1.0, "end_gap": 0.0}} for a in areas},
                                                                               "unreached": [], "attempts": {a: [] for a in areas}})
    monkeypatch.setattr(RP, "match_cause", lambda rules, kind, f: rules[-1])
    monkeypatch.setattr(gen, "require_data_config", lambda cfg: None)
    monkeypatch.setattr(S, "match_open_studio", lambda place, studios, label="": {"matched_by": "anything", "studio_id": "x", "input_schema": "schema_unverified"})
    monkeypatch.setattr(E, "is_flaky", lambda check, cfg: False)
    monkeypatch.setattr(luau_safety, "assert_safe", lambda code, **kw: code)
    monkeypatch.setitem(E.EVALUATORS, "economy", lambda doc, ctx, out: None)
    monkeypatch.setattr(B, "compare", lambda base, s, e, ranges: [])
    real_resolve = places.resolve
    monkeypatch.setattr(places, "resolve", lambda project, pid, plid, **kw: real_resolve(project, pid or "demo_mine", plid or "main", **kw))  # "helpfully" guesses the scope
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="broken", root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"reach-unreachable-room-is-caught", "reach-without-the-hop-edge-b-is-unreachable", "boot-script-error-is-caught", "remotes-erroring-handler-is-caught", "economy-currency-bug-is-caught",
            "economy-doubled-sale-is-caught", "data-check-without-test-mode-switch-is-refused", "studio-wrong-open-place-refuses-the-plan", "studio-wrong-open-place-refuses-the-script",
            "remotes-one-failed-run-of-three-is-flaky-not-a-failure", "gen-forbidden-token-inside-a-value-is-caught-by-the-lint", "perf-part-bloat-is-caught", "scope-missing-both-is-refused",
            "scope-every-tool-refuses-without-scope"} <= failed


def test_evals_fail_when_the_console_parser_is_blind(project, monkeypatch):
    from playqa.domain import console as CON

    real = CON.parse

    def blind(*a, **k):
        r = real(*a, **k)
        r.update(errors=0, warnings=0, findings=[])
        return r

    monkeypatch.setattr(CON, "parse", blind)
    monkeypatch.setattr(CON, "parse_all_probes", lambda *a, **k: {})
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="blind", root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"boot-warning-is-caught", "boot-script-error-is-caught", "console-stack-trace-is-one-error", "remotes-event-error-is-seen-only-through-the-console"} <= failed


def test_evals_fail_when_the_mock_world_stops_planting_faults(project, monkeypatch):
    from playqa.domain import mockworld

    monkeypatch.setattr(mockworld, "LUA", mockworld.LUA.replace("FAULTS = FAULTS or {}", "FAULTS = {}"))
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="nofaults", root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"boot-script-error-is-caught", "reach-unreachable-room-is-caught", "remotes-erroring-handler-is-caught", "economy-currency-bug-is-caught", "spawn-stuck-character-is-caught", "data-lost-value-is-caught"} <= failed


def test_the_budget_evals_notice_growth(project, monkeypatch):
    from playqa.domain import places

    real = places.head

    def fat(ctx, summary, **rest):
        out = real(ctx, summary, **rest)
        out["padding"] = "x" * 20000
        return out

    monkeypatch.setattr(places, "head", fat)
    tasks = [t for t in evals.load_tasks(ROOT / "evals" / "tasks") if t["id"].startswith("budget-")]
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="fat", root=ROOT)
    assert sum(1 for t in rep["tasks"] if not t["passed"]) >= 10


def test_cli_end_to_end(project, capsys):
    run = config.run
    assert run(project, hooks_mod.HOOKS, ["places"]) == 0 and "demo_mine/main" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["checks", "--project-id", "demo_obby", "--place-id", "main"]) == 0
    assert "3 can run" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["checks", "--project-id", "demo_obby", "--place-id", "nowhere"]) == 2
    assert "does not belong to project" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["domain"]["projects"]["places"][0]["sections"][0] == "boot" and "schema_unverified" in out["domain"]["input_formats"]
    assert run(project, hooks_mod.HOOKS, ["call"]) == 0 and "plan_playtest" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["call", "list_checks", "--json", json.dumps(MAIN)]) == 0 and "7 checks" in capsys.readouterr().out


def test_dependencies_are_declared():
    text = (ROOT / "pyproject.toml").read_text()
    assert "guide-core" in text and "lupa" in text and "mcp" in text


def test_telemetry_is_off_by_default_and_records_sizes_when_on(project, monkeypatch):
    call(project, "list_checks", **MAIN)
    assert not (project.workspace / "telemetry.jsonl").exists()
    monkeypatch.setenv("PLAYQA_TELEMETRY", "1")
    call(project, "list_checks", **MAIN)
    rows = [json.loads(l) for l in (project.workspace / "telemetry.jsonl").read_text().splitlines()]
    assert rows[0]["tool"] == "list_checks" and rows[0]["chars"] > 500 and set(rows[0]) == {"tool", "at", "chars", "seconds", "ok"}
