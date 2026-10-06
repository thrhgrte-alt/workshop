"""MCP tools end to end, scope and safety defaults, the shared-machinery boundary, docs, evals (including guards against vacuous evals) and repository hygiene."""
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

from placemap import evalkit, hooks as hooks_mod
from placemap.domain import places
from placemap.guide_adapter import agentfiles, all_tools, cli, config, evals, manifest, mcpkit, scope as S, telemetry

ROOT = Path(__file__).resolve().parents[1]
SPEC_READ_ONLY = {"find_in_place", "get_path_info", "who_uses", "list_remotes", "show_dependencies", "summarize_area", "diff_snapshots", "find_past_corrections", "find_across_places",
                  "compare_places", "find_shared_code"}
SPEC_WRITERS = {"plan_refresh", "ingest_snapshot", "label_landmark", "record_run", "record_decision"}
EXTRA_WRITERS = {"undo_label", "prune_snapshots"}
EXTRA_READERS = {"get_style_brief", "list_places", "list_candidates", "list_snapshots", "list_roles"}


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def test_every_tool_named_in_the_spec_exists_with_the_right_kind(project):
    by = {t.name: t for t in tools(project)}
    assert SPEC_READ_ONLY | SPEC_WRITERS | EXTRA_WRITERS | EXTRA_READERS == set(by)
    assert all(by[n].read_only for n in SPEC_READ_ONLY | EXTRA_READERS)
    assert all(not by[n].read_only for n in SPEC_WRITERS | EXTRA_WRITERS)
    assert "search_library" not in by and "promote_run" not in by  # the unscoped library tools are not exposed


def test_tool_functions_are_annotated_scoped_and_writers_default_to_dry_run(project):
    for t in tools(project):
        sig = inspect.signature(t.fn).parameters
        assert "dict" in str(inspect.signature(t.fn).return_annotation), t.name
        if "dry_run" in sig:
            assert sig["dry_run"].default is True, t.name
        if not t.read_only and t.name not in ("record_run", "record_decision"):
            assert "dry_run" in sig, t.name
        assert "project_id" in sig and sig["project_id"].default is None, t.name  # optional in the schema, refused at run time with a message that says what is missing
        if t.name not in ("find_across_places", "compare_places", "find_shared_code", "list_places", "get_style_brief", "diff_snapshots"):
            assert "place_id" in sig, t.name
        assert len(t.description) <= 300, (t.name, len(t.description))


def test_no_cross_place_tool_can_write(project):
    for t in tools(project):
        if t.name in ("find_across_places", "compare_places", "find_shared_code"):
            assert t.read_only and "dry_run" not in inspect.signature(t.fn).parameters
    for name in ("label_landmark", "ingest_snapshot", "plan_refresh", "prune_snapshots", "undo_label"):
        params = set(inspect.signature(next(t for t in tools(project) if t.name == name).fn).parameters)
        assert not {"place_ids", "place_a", "place_b"} & params, name


def test_rare_group_is_a_proper_subset_that_can_be_disabled(project, monkeypatch):
    monkeypatch.setenv("PLACEMAP_DISABLE_GROUPS", "rare")
    core = {t.name for t in tools(project)}
    monkeypatch.delenv("PLACEMAP_DISABLE_GROUPS")
    allt = {t.name for t in tools(project)}
    assert allt - core == {"compare_places", "find_shared_code", "list_snapshots", "list_roles", "undo_label", "prune_snapshots"}
    assert SPEC_WRITERS <= core and {"find_in_place", "who_uses", "list_remotes"} <= core


def test_dry_runs_write_nothing_and_real_writes_go_to_the_place_folder(world, call, project):
    ws = project.workspace
    before = sorted(p for p in ws.rglob("*") if p.is_file())
    for tool, kw in (("label_landmark", {"path": "Workspace/Npcs/Carl", "role": "vendor", "label": "confirm", "project_id": "demo_roles", "place_id": "role-lab"}),
                     ("prune_snapshots", {"project_id": "demo_roles", "place_id": "role-lab"}),
                     ("ingest_snapshot", {"project_id": "demo_tycoon", "place_id": "tycoon-main", "file": "samples/synthetic/collect-tycoon.json"})):
        assert call(tool, **kw)["dry_run"] is True
    assert sorted(p for p in ws.rglob("*") if p.is_file()) == before
    res = call("label_landmark", path="Workspace/Npcs/Carl", role="vendor", label="confirm", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    labels = ws / "projects" / "demo_roles" / "role-lab" / "labels.jsonl"
    assert labels.is_file() and res["label_id"] in labels.read_text()
    assert (ws / "learning" / "params.json").is_file()


def test_snapshots_summaries_and_labels_land_in_the_right_folders(world, project):
    ws = project.workspace
    for pid, plid in (("demo_mine", "dive-and-mine"), ("demo_mine", "dive-and-mine-hardcore"), ("demo_roles", "role-lab"), ("demo_tycoon", "tycoon-main")):
        assert list((ws / "projects" / pid / plid / "snapshots").glob("snap-*.json")), (pid, plid)
    assert (ws / "summary_cache").is_dir() and not list((ws / "summary_cache").rglob("*.jsonl"))
    cached = json.loads(next((ws / "summary_cache").rglob("*.json")).read_text())
    assert "path" not in cached and "place_id" not in json.dumps(cached)  # the shared cache holds nothing about any place


def test_tools_are_deterministic(world, call):
    for tool, kw in (("find_in_place", {"query": "vendors", "role": "vendor"}), ("who_uses", {"path": "ReplicatedStorage/Modules/Economy"}), ("list_remotes", {}),
                     ("summarize_area", {"path": "ServerScriptService"}), ("get_path_info", {"path": "Workspace/Vendors/Bob", "detail": True})):
        kw = {**kw, "project_id": "demo_mine", "place_id": "dive-and-mine"}
        assert call(tool, **kw) == call(tool, **kw)


def test_results_are_capped_unless_detail_is_asked_for(world, call):
    short = call("list_remotes", project_id="demo_mine", place_id="dive-and-mine")
    full = call("list_remotes", project_id="demo_mine", place_id="dive-and-mine", detail=True)
    assert len(short["remotes"]) == 10 and short["more"] == 2 and len(full["remotes"]) == 12
    top = call("find_in_place", query="collectibles", role="collectible", project_id="demo_mine", place_id="dive-and-mine")
    assert len(top["selected"]) == 8 and "more_selected" not in top
    small = call("find_in_place", query="nodes", role="resource_node", k=5, project_id="demo_mine", place_id="dive-and-mine")
    assert len(small["selected"]) == 5 and small["more_selected"] == 7
    assert next(iter(short)) == "summary"  # the verdict line is the first field


def test_every_answer_flags_staleness_and_the_parser_status(world, call, monkeypatch):
    fresh = call("find_in_place", query="Bob", project_id="demo_mine", place_id="dive-and-mine")
    assert fresh["snapshot"]["stale"] is False and fresh["snapshot"]["parser"] == "schema_unverified" and "STALE" not in fresh["summary"]
    monkeypatch.setenv("PLACEMAP_NOW", "2026-10-20T12:00:00+00:00")
    old = call("get_path_info", path="Workspace/Vendors/Bob", project_id="demo_mine", place_id="dive-and-mine")
    assert old["snapshot"]["stale"] is True and "plan_refresh" in old["snapshot"]["offer"]


# --- MCP in memory and over real stdio ---------------------------------------------------------------------------------------------------------------------
def test_mcp_session_lists_tools_with_labels_and_calls_them(world):
    async def go():
        server = mcpkit.build_server(world, tools(world), hooks_mod.HOOKS.instructions)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            listed = (await client.list_tools()).tools
            names = {t.name for t in listed}
            ro = {t.name for t in listed if t.annotations and t.annotations.readOnlyHint}
            res = await client.call_tool("find_in_place", {"query": "vendors", "role": "vendor", "project_id": "demo_roles", "place_id": "role-lab"})
            bad = await client.call_tool("find_in_place", {"query": "Bob"})
            return names, ro, res, bad

    names, ro, res, bad = asyncio.run(go())
    assert SPEC_READ_ONLY <= ro and not (SPEC_WRITERS & ro) and SPEC_WRITERS <= names
    assert not res.isError and res.structuredContent["selected"][0]["path"] == "role-lab::Workspace/Npcs/Bob"
    assert bad.isError and "project_id" in json.dumps([c.model_dump() for c in bad.content])


def test_real_stdio_smoke():
    smoke = Path("/home/user/workshop/suite/smoke_stdio.py")
    if not smoke.exists():
        pytest.skip("suite smoke script not present")
    proc = subprocess.run([sys.executable, str(smoke), str(ROOT), "placemap", "list_places"], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    assert info["called"] == "list_places" and not info["is_error"] and info["tool_count"] >= 20 and info["server"] == "place-map"


def test_the_server_wraps_every_tool_with_telemetry_and_budgets_hold(world, call, project):
    t = telemetry.Telemetry(project.workspace / "tel.jsonl")
    wrapped = t.instrument(tools(project))
    budgets = yaml.safe_load((ROOT / "evals" / "budgets.yaml").read_text())["tools"]
    for name, b in budgets.items():
        mcpkit.call_local(wrapped, name, b["args"])
    summary = t.summary()
    assert set(summary) == set(budgets)
    assert telemetry.check_budgets(summary, {n: b["max_chars"] for n, b in budgets.items()}) == []
    assert all(s["calls"] == 1 and s["errors"] == 0 for s in summary.values())


def test_every_tool_with_a_typical_output_has_a_budget(project):
    budgets = set(yaml.safe_load((ROOT / "evals" / "budgets.yaml").read_text())["tools"])
    exempt = {"record_run", "record_decision", "undo_label", "get_style_brief", "diff_snapshots"}
    missing = [t.name for t in tools(project) if t.name not in budgets and t.name not in exempt]
    assert not missing, f"add an output-size budget for: {missing}"


# --- the shared-machinery boundary --------------------------------------------------------------------------------------------------------------------------
def test_only_the_adapter_imports_shared_machinery():
    pkg = ROOT / "placemap"
    offenders = []
    for f in pkg.rglob("*.py"):
        if f.name == "guide_adapter.py":
            continue
        text = f.read_text(encoding="utf-8")
        if re.search(r"(from\s+guide_core\b|import\s+guide_core\b|from\s+\.+core\b|placemap\.core\b)", text):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"import shared machinery only via placemap.guide_adapter: {offenders}"
    assert not (pkg / "core").exists() and not (ROOT / "tests" / "core_suite").exists()


def test_the_adapter_exposes_the_named_interfaces():
    from placemap import guide_adapter as g

    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope", "params", "promote", "skillgen", "observe", "telemetry", "propose", "gate"):
        assert hasattr(g, name), name
    assert hasattr(g.dryrun, "Plan") and hasattr(g.scope, "match_open_studio") and hasattr(g.luau_safety, "lint") and hasattr(g.mock, "MockRoblox")


def test_the_package_never_reaches_the_network_or_studio():
    pat = re.compile(r"^\s*(import|from)\s+(requests|urllib|socket|http\.client|websocket|websockets|aiohttp|httpx|ftplib|smtplib|subprocess)\b", re.M)
    offenders = [str(f.relative_to(ROOT)) for f in (ROOT / "placemap").rglob("*.py") if pat.search(f.read_text())]
    assert offenders == []


# --- repo layout, docs, skills, schema ---------------------------------------------------------------------------------------------------------------------------
def test_layout_satisfies_shared_rule_6():
    for p in ("placemap", "rules", "style", "library", "examples", "evals", "evals/real/README.md", "feedback", "skills", "adapters/mcp-clients", "tests", "samples/README.md", "README.md", "CLAUDE.md",
              "AGENTS.md", "projects.yaml", "style/STYLE.md", "ASSET_LICENSING.md", "rules/roles.yaml", "rules/synonyms.yaml", "rules/limits.yaml", "pyproject.toml"):
        assert (ROOT / p).exists(), p
    assert sorted(p.name for p in (ROOT / "evals" / "real").iterdir()) == ["README.md"]  # empty until the user supplies real examples


def test_style_skills_schema_and_baseline(project):
    from placemap import KINDS
    from placemap.domain.schema import DOMAIN_SCHEMA

    assert manifest.LibraryStore(project).validate_all(check_files=True)["ok"]
    assert config.validate_style(config.load_style(project)) == []
    assert agentfiles.validate_all_skills(ROOT) == [] and agentfiles.sync(ROOT, check=True) == []
    assert json.loads(project.schema_file.read_text()) == manifest.base_schema(KINDS, DOMAIN_SCHEMA)
    baseline = json.loads((ROOT / "evals" / "reports" / "baseline.json").read_text())
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    fresh = evals.run_suite(tasks, evalkit.eval_solver(project), evalkit.eval_checks(project), label="t", root=ROOT)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_scripts_are_current():
    for script in ("build_schema.py", "sync_agent_files.py", "make_examples.py"):
        assert subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--check"], capture_output=True).returncode == 0, script


def test_readme_documents_every_tool_in_backticks(project):
    text = (ROOT / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in text]
    assert not missing, missing
    for t in tools(project):
        assert f"| `{t.name}` |" in text, t.name  # in the tool table, with its Writes? and Group columns


def test_readme_states_the_honest_limits_and_the_adapter():
    text = (ROOT / "README.md").read_text().lower()
    for phrase in ("never run against", "guide-core", "guide_adapter", "schema_unverified", "placeholder", "agrees with itself", "evals/real", "what you still need to supply", "not verified",
                   "learning improves this only as far as", "never connects to roblox studio"):
        assert phrase in text, phrase


def test_samples_readme_names_the_hub_call_and_file_for_every_parsed_output():
    text = (ROOT / "samples" / "README.md").read_text()
    for needle in ("list_roblox_studios", "roblox_studio_execute_luau", "roblox_studio_search_game_tree", "roblox_studio_script_read", "samples/hub/", "schema_unverified"):
        assert needle in text, needle
    assert "SYNTHETIC" in text and (ROOT / "samples" / "synthetic").is_dir()


def test_every_parser_reports_schema_unverified(world, call):
    for f in ("samples/synthetic/tree-generic.json", "samples/synthetic/tree-outline.txt", "samples/synthetic/script-read.json"):
        res = call("ingest_snapshot", project_id="demo_tycoon", place_id="tycoon-main", file=f)
        assert res["parser_status"] == "schema_unverified", f
    res = call("ingest_snapshot", project_id="demo_tycoon", place_id="tycoon-main", file="samples/synthetic/collect-tycoon.json")
    assert "schema_unverified" in res["parser_status"] and any("schema_unverified" in w for w in res["warnings"])
    plan = call("plan_refresh", project_id="demo_tycoon", place_id="tycoon-main", studios=[{"name": "Demo Tycoon (SYNTHETIC)", "place_id": 9000000001, "studio_id": "s"}])
    assert plan["input_schema"] == "schema_unverified"


def test_projects_yaml_is_clearly_synthetic():
    reg = yaml.safe_load((ROOT / "projects.yaml").read_text())
    assert len(reg["projects"]) >= 3 and all(p["synthetic"] is True and "SYNTHETIC" in p["alias"] for p in reg["projects"])
    assert "SYNTHETIC" in (ROOT / "projects.yaml").read_text().splitlines()[0]


def test_dependencies_are_declared():
    text = (ROOT / "pyproject.toml").read_text()
    assert "guide-core" in text and "lupa" in text and "mcp" in text and "place-map-mcp" in text


# --- evals --------------------------------------------------------------------------------------------------------------------------------------------------
def test_eval_suite_is_big_enough_and_has_known_good_and_known_bad_cases():
    tasks = evals.load_tasks(ROOT / "evals" / "tasks", strict=True)
    assert len(tasks) >= 45 and all(t["checks"] for t in tasks)
    tags = {tag for t in tasks for tag in t.get("tags", [])}
    assert {"known-good", "known-bad", "decoy", "wrong-place", "refresh", "multi", "roles", "scope", "tokens", "determinism", "drift", "precision-recall"} <= tags
    assert sum("known-bad" in t.get("tags", []) for t in tasks) >= 15 and sum("known-good" in t.get("tags", []) for t in tasks) >= 30
    ids = {t["id"] for t in tasks}
    for needed in ("roles-weird-names-are-found-by-structure", "roles-decoys-with-matching-names-and-no-interaction-are-not-confident", "multi-shared-code-finds-identical-copies-and-the-drifted-module",
                   "multi-wrong-open-place-for-a-sibling-is-refused", "multi-cross-place-search-labels-every-hit-with-its-place", "refresh-refuses-when-another-place-is-open-and-says-which"):
        assert needed in ids, needed


def test_the_synthetic_mining_place_has_exactly_forty_scripts(world):
    from placemap.domain.snapshot import SnapshotStore

    ctx = places.resolve(world, "demo_mine", "dive-and-mine")
    assert len(SnapshotStore(ctx.wproject).load().scripts) == 40


def test_the_real_set_is_reported_as_zero_not_as_a_pass(project):
    split = evals.load_split(ROOT / "evals" / "tasks", ROOT / "evals" / "real")
    assert split["real"] == [] and len(split["self_written"]) >= 45
    rep = evals.run_split({"self_written": [], "real": []}, lambda t: {}, {}, label="x", root=ROOT)
    assert "0 cases" in evals.render_markdown(rep)


def _failed(project, label):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks")
    rep = evals.run_suite(tasks, evalkit.eval_solver(project), evalkit.eval_checks(project), label=label, root=ROOT)
    return {t["id"] for t in rep["tasks"] if not t["passed"]}


def test_evals_fail_when_scoring_is_broken(project, monkeypatch):
    """Vacuity guard 1: break the judge (the formula, the repetition feature, the container rule) and demand that the role evals notice."""
    from placemap.domain import roles as R

    monkeypatch.setattr(R.Scorer, "combine", staticmethod(lambda w, f: 0.0))
    failed = _failed(project, "no-scores")
    assert {"roles-weird-names-are-found-by-structure", "roles-precision-and-recall-on-the-self-written-places", "graph-path-info-for-a-vendor", "roles-formula-clamps-and-counts-negative-weights-against"} <= failed


def test_evals_fail_when_the_scorer_ignores_negative_weights(project, monkeypatch):
    from placemap.domain import roles as R
    from placemap.domain.util import clamp

    monkeypatch.setattr(R.Scorer, "combine", staticmethod(lambda w, f: clamp(sum(max(0.0, w[k]) * f[k] for k in w) / sum(abs(v) for v in w.values())) if w else 0.0))
    failed = _failed(project, "no-negatives")
    assert "roles-formula-clamps-and-counts-negative-weights-against" in failed


def test_evals_fail_when_matching_widens(project, monkeypatch):
    """Vacuity guard 2: a search that returns every near match as the answer, and a role that selects borderline candidates, must fail the literal-naming evals."""
    from placemap.domain import search as SE

    real = SE.exact_name_matches
    monkeypatch.setattr(SE, "exact_name_matches", lambda idx, q: real(idx, q) + [p for p in idx.snap.by_path if idx.name(p).lower() in ("zed", "shopsign")])
    failed = _failed(project, "widened")
    assert {"search-where-is-bob-exact-path-only", "search-near-matches-are-listed-as-not-selected"} & failed


def test_evals_fail_when_scope_and_place_checks_are_removed(project, monkeypatch):
    """Vacuity guard 3: a tool that guesses the project and place, and a place check that accepts anything."""
    from placemap.domain import ingest as IN, places as PL

    real = PL.resolve
    monkeypatch.setattr(PL, "resolve", lambda p, pid, plid, **kw: real(p, pid or "demo_mine", plid or "dive-and-mine", **kw))
    monkeypatch.setattr(PL, "match_open", lambda ctx, studios: {"matched_by": "anything", "name": "x", "studio_id": "x"})
    failed = _failed(project, "guessing")
    assert {"scope-every-tool-refuses-without-a-project", "refresh-refuses-when-another-place-is-open-and-says-which", "refresh-refuses-without-the-open-studio-list",
            "multi-wrong-open-place-for-a-sibling-is-refused"} <= failed


def test_evals_fail_when_the_lint_the_secret_filter_or_the_cache_is_broken(project, monkeypatch):
    from placemap.domain import collector, ingest as IN
    from placemap.domain.secrets import SecretFilter

    monkeypatch.setattr(SecretFilter, "attr_is_secret", lambda self, k, v: False)
    monkeypatch.setattr(SecretFilter, "value_is_secret", lambda self, v: False)
    failed = _failed(project, "leaky")
    assert {"ingest-drops-secret-attributes-before-storing", "scan-secret-looking-strings-are-not-kept"} <= failed


def test_evals_fail_when_the_graph_or_the_diff_is_wrong(project, monkeypatch):
    from placemap.domain import graph as G

    monkeypatch.setattr(G.Graph, "transitive_dependents", lambda self, path, depth=3, cap=200: [d for d in G.Graph.__dict__["transitive_dependents"](self, path, 1, cap)])
    failed = _failed(project, "shallow")
    assert {"graph-what-breaks-if-economy-changes", "graph-leaf-module-nothing-requires-signal-except-two"} & failed


def test_evals_fail_when_the_collector_stops_filtering_secrets_or_the_guard(project, monkeypatch):
    from placemap.domain import collector

    monkeypatch.setattr(collector, "TEMPLATE", collector.TEMPLATE.replace("if keyIsSecret(k) or valueIsSecret(v) then", "if false then"))
    failed = _failed(project, "luau-leak")
    assert "collector-skips-secret-attributes" in failed or not evalkit.mock_extra.M.available()


# --- CLI ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_cli_end_to_end(project, capsys):
    run = cli.run
    assert run(project, hooks_mod.HOOKS, ["places"]) == 0 and "demo_mine" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["domain"]["projects"]["places"]) == 4 and "schema_unverified" in out["domain"]["parsers"]
    assert run(project, hooks_mod.HOOKS, ["call"]) == 0 and "find_in_place" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["call", "find_in_place", "--json", '{"query": "Bob"}']) == 2  # refused: no project_id
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["call", "list_places", "--json", '{"project_id": "nope"}']) == 2
    assert "unknown project_id" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["telemetry"]) == 0
