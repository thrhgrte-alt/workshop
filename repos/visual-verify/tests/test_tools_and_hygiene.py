"""MCP tools end to end, scope and safety defaults, the shared-machinery boundary, docs, evals (including guards against vacuous evals) and repository hygiene."""
import asyncio
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from visualverify import hooks as hooks_mod
from visualverify.guide_adapter import all_tools, cli_run, config, evals, mcpkit

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"project_id": "demo-stylized-obby", "place_id": "stage-1"}
SPEC_READ_ONLY = {"measure_image", "compare_to_reference", "silhouette_iou", "palette_distance", "check_tiling", "check_pbr_ranges", "diff_images", "get_style_brief", "find_past_corrections"}
SPEC_WRITERS = {"save_target_profile", "render_diff_image", "record_run", "record_decision", "promote_run"}
EXTRA_WRITERS = {"ingest_capture"}
EXTRA_READ_ONLY = {"get_target_profile", "list_thresholds"}


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return mcpkit.call_local(tools(project), tool, kw)


def test_every_tool_named_in_the_spec_exists_with_the_right_kind(project):
    by = {t.name: t for t in tools(project)}
    assert SPEC_READ_ONLY | SPEC_WRITERS <= set(by)
    assert all(by[n].read_only for n in SPEC_READ_ONLY | EXTRA_READ_ONLY)
    assert all(not by[n].read_only for n in SPEC_WRITERS | EXTRA_WRITERS)
    assert set(by) == SPEC_READ_ONLY | SPEC_WRITERS | EXTRA_READ_ONLY | EXTRA_WRITERS  # nothing unlabelled or unexpected


def test_tool_functions_are_annotated_scoped_and_writers_default_to_dry_run(project):
    for t in tools(project):
        assert "dict" in str(inspect.signature(t.fn).return_annotation)
        sig = inspect.signature(t.fn).parameters
        for p in ("project_id", "place_id"):
            assert p in sig and sig[p].default is None, (t.name, p)  # optional in the schema, refused at run time with a message that says what is missing
        if "dry_run" in sig:
            assert sig["dry_run"].default is True, t.name
        if not t.read_only and t.name not in ("record_run", "record_decision", "promote_run"):
            assert "dry_run" in sig, t.name


def test_descriptions_are_short_and_say_what_the_tool_is(project):
    for t in tools(project):
        assert 20 < len(t.description) <= 330, (t.name, len(t.description))


def test_no_tool_has_a_cross_project_argument(project):
    for t in tools(project):
        assert not {"a_project_id", "b_project_id", "other_project_id"} & set(inspect.signature(t.fn).parameters)


def test_search_library_is_dropped_and_names_are_unique(project):
    names = [t.name for t in tools(project)]
    assert "search_library" not in names and len(names) == len(set(names))


def test_dry_runs_write_nothing_and_applied_writes_stay_in_the_project_folder(project, images):
    ws = project.workspace
    a = images("a", {"gen": "vstep", "size": [64, 64], "left": [0, 0, 0], "right": [255, 255, 255], "x": 32})
    b = images("b", {"gen": "solid", "size": [64, 64], "color": [128, 128, 128]})
    before = sorted(p for p in ws.rglob("*") if p.is_file())
    for tool, kw in (("save_target_profile", {"name": "look", "reference": a}), ("render_diff_image", {"before": a, "after": b}), ("ingest_capture", {"path": a, "name": "shot"})):
        res = call(project, tool, **MAIN, **kw)
        assert res["dry_run"] is True and "DRY RUN" in res["summary"], tool
    assert sorted(p for p in ws.rglob("*") if p.is_file()) == before
    res = call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False)
    path = Path(res["path"])
    assert path.is_file() and (ws / "projects" / "demo-stylized-obby" / "stage-1") in path.parents
    again = call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False)  # same bytes, nothing refused, nothing overwritten differently
    assert again["path"] == res["path"]
    with pytest.raises(ValueError, match="already exists with different content"):
        call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False, gain=1.0, name=Path(res["path"]).stem)


def test_render_diff_image_is_a_real_picture_and_deterministic(project, images):
    from PIL import Image

    a = images("a", {"gen": "solid", "size": [20, 10], "color": [100, 100, 100]})
    b = images("b", {"gen": "boxed", "base": {"gen": "solid", "size": [20, 10], "color": [100, 100, 100]}, "box": [0, 0, 5, 5], "color": [200, 200, 200]})
    r1 = call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False, mode="side_by_side", name="sbs")
    img = Image.open(r1["path"])
    assert img.size == (20 * 3 + 8, 10)  # three panels and two 4 px gaps
    assert img.getpixel((0, 0))[0] == 100 and img.getpixel((20 + 4, 0))[0] == 200  # before | after
    assert img.getpixel((40 + 8, 0)) == (255, 255, 255) or img.getpixel((40 + 8, 0))[0] > 200  # amplified difference (100/255 * 4 clips to white)
    data = Path(r1["path"]).read_bytes()
    r2 = call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False, mode="side_by_side", name="sbs", overwrite=True)
    assert Path(r2["path"]).read_bytes() == data
    diff = call(project, "render_diff_image", **MAIN, before=a, after=b, dry_run=False, mode="difference", name="dif")
    assert Image.open(diff["path"]).size == (20, 10)
    with pytest.raises(ValueError, match="mode must be"):
        call(project, "render_diff_image", **MAIN, before=a, after=b, mode="swirl")


def test_tools_are_deterministic_in_process(project, images):
    a = images("a", {"gen": "white_noise", "size": [64, 64], "seed": 4})
    b = images("b", {"gen": "smooth_crop", "size": [64, 64], "seed": 4})
    for tool, kw in (("measure_image", {"path": a, "detail": True}), ("compare_to_reference", {"candidate": a, "reference": b, "detail": True}), ("check_tiling", {"path": b, "detail": True}),
                     ("diff_images", {"before": a, "after": b, "detail": True}), ("palette_distance", {"path": a, "target_palette": ["#808080"]})):
        assert call(project, tool, **MAIN, **kw) == call(project, tool, **MAIN, **kw), tool


def test_the_summary_is_the_first_key_and_scope_is_stated(project, images):
    a = images("a", {"gen": "vstep", "size": [64, 64], "left": [0, 0, 0], "right": [255, 255, 255], "x": 32})
    for tool, kw in (("measure_image", {"path": a}), ("check_tiling", {"path": a}), ("check_pbr_ranges", {"albedo": a}), ("diff_images", {"before": a, "after": a}),
                     ("palette_distance", {"path": a, "target_palette": ["#000000"]}), ("get_style_brief", {}), ("list_thresholds", {}), ("get_target_profile", {})):
        res = call(project, tool, **MAIN, **kw)
        assert next(iter(res)) == "summary" and res["project_id"] == "demo-stylized-obby" and res["place_id"] == "stage-1", tool


def test_style_brief_labels_every_threshold_a_placeholder(project):
    res = call(project, "get_style_brief", **MAIN)
    assert "PLACEHOLDER" in res["summary"] and "PLACEHOLDER" in res["brief"]
    assert any(t.startswith("silhouette.iou_min >= 0.8") for t in res["thresholds"])
    assert set(res["correction_dimensions"]) >= {"silhouette", "palette", "value", "edges", "tiling", "texture"}
    pix = call(project, "get_style_brief", project_id="demo-pixel-brawler")
    assert "pixel" in pix["brief"].lower() and "demo-pixel-brawler" not in json.dumps(res)  # the per-project overlay applies to its own project only


async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project, images):
    a = images("a", {"gen": "vstep", "size": [64, 64], "left": [0, 0, 0], "right": [255, 255, 255], "x": 32})
    server = mcpkit.build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        measured = await client.call_tool("measure_image", {**MAIN, "path": a})
        no_scope = await client.call_tool("measure_image", {"path": a})
        unknown = await client.call_tool("measure_image", {"path": a, "project_id": "no-such-game"})
        rid = (await client.call_tool("record_run", {**MAIN, "request": "stage 1 hero", "measure": {"tool": "measure_image", "args": {"path": a}}})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "reason": "too flat", "corrections": [{"dimension": "edges", "note": "needs detail"}]})
        bad = await client.call_tool("record_decision", {**MAIN, "run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        return listed, measured, no_scope, unknown, dec, bad

    listed, measured, no_scope, unknown, dec, bad = asyncio.run(_with(server, go))
    assert listed["measure_image"].annotations.readOnlyHint is True and listed["record_decision"].annotations.readOnlyHint is False
    assert measured.structuredContent["summary"].startswith("measure_image:") and measured.structuredContent["project_id"] == "demo-stylized-obby"
    assert no_scope.isError and "project_id is required" in no_scope.content[0].text
    assert unknown.isError and "unknown project_id" in unknown.content[0].text
    assert not dec.isError and bad.isError


def test_every_tool_is_documented_in_the_readme(project):
    readme = (ROOT / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_rare_group_can_be_disabled(project, monkeypatch):
    all_names = {t.name for t in tools(project)}
    monkeypatch.setenv("VISUALVERIFY_DISABLE_GROUPS", "rare")
    core = {t.name for t in tools(project)}
    assert {"list_thresholds", "promote_run", "get_target_profile"} == all_names - core
    assert SPEC_READ_ONLY <= core and {"record_run", "record_decision", "save_target_profile", "render_diff_image"} <= core


# --- the shared-machinery boundary and the "no network, no model, no randomness" promise ---------------------------------------------------
def _py_files():
    return [f for f in (ROOT / "visualverify").rglob("*.py")]


def test_only_the_adapter_imports_shared_machinery():
    offenders = []
    for f in _py_files():
        if f.name == "guide_adapter.py":
            continue
        if re.search(r"(from\s+guide_core\b|import\s+guide_core\b|from\s+\.+core\b|visualverify\.core\b)", f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, f"import shared machinery only via visualverify.guide_adapter: {offenders}"
    assert not (ROOT / "visualverify" / "core").exists() and not (ROOT / "tests" / "core_suite").exists()


def test_the_adapter_exposes_the_named_interfaces():
    from visualverify import guide_adapter as g

    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope", "params", "observe", "propose", "gate", "promote", "skillgen", "telemetry"):
        assert hasattr(g, name), name
    assert g.mock is None and g.luau_safety is None
    assert hasattr(g.dryrun, "Plan") and hasattr(g.scope, "resolve_inside") and hasattr(g.config, "load_style")


def test_nothing_imports_concept_art_ai_or_the_network_or_a_model_or_randomness():
    banned = re.compile(r"^\s*(?:from|import)\s+(conceptai|socket|urllib|requests|http|httpx|aiohttp|ftplib|smtplib|websockets?|anthropic|openai|torch|tensorflow|transformers|sentence_transformers|random|secrets|subprocess)\b", re.M)
    offenders = {str(f.relative_to(ROOT)): banned.findall(f.read_text(encoding="utf-8")) for f in _py_files() if banned.search(f.read_text(encoding="utf-8"))}
    assert not offenders, offenders
    for f in _py_files():
        text = f.read_text(encoding="utf-8")
        assert "default_rng()" not in text and "np.random.seed" not in text, f
        for m in re.finditer(r"RandomState\(([^)]*)\)", text):
            assert m.group(1).strip(), f"{f.name}: RandomState() without a seed"
    # evalgen.py (generated test images) is the only file that draws random numbers, and only from explicitly seeded RandomState objects
    assert not [f.name for f in _py_files() if f.name != "evalgen.py" and re.search(r"np\.random|numpy\.random|RandomState", f.read_text(encoding="utf-8"))]


# --- layout, docs, skills, schema, examples ----------------------------------------------------------------------------------------------
def test_layout_satisfies_shared_rule_6():
    for p in ("visualverify", "rules", "style", "library", "examples", "evals", "evals/real", "evals/real/README.md", "feedback", "skills", "adapters/mcp-clients", "tests", "samples", "samples/README.md",
              "README.md", "CLAUDE.md", "AGENTS.md", "projects.yaml", "references/README.md", "style/STYLE.md", "style/thresholds.yaml", "ASSET_LICENSING.md", "skills/visual-verify-workflow/SKILL.md"):
        assert (ROOT / p).exists(), p
    assert not [f for f in (ROOT / "evals" / "real").iterdir() if f.name != "README.md"], "evals/real must ship empty (only its README)"


def test_library_style_skills_schema_and_sync(project):
    from visualverify import KINDS
    from visualverify.domain.schema import DOMAIN_SCHEMA

    assert config.LibraryStore(project).validate_all(check_files=True)["ok"]
    assert config.validate_style(config.load_style(project)) == []
    assert config.agentfiles.validate_all_skills(ROOT) == [] and config.agentfiles.sync(ROOT, check=True) == []
    assert json.loads(project.schema_file.read_text()) == config.base_schema(KINDS, DOMAIN_SCHEMA)


def test_scripts_are_current():
    for script in ("build_schema.py", "sync_agent_files.py", "make_examples.py", "make_samples.py"):
        assert subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--check"], capture_output=True).returncode == 0, script


def test_projects_yaml_is_three_clearly_synthetic_projects():
    import yaml

    data = yaml.safe_load((ROOT / "projects.yaml").read_text())
    assert len(data["projects"]) == 3 and all(p["synthetic"] and "SYNTHETIC" in p["alias"] for p in data["projects"])
    assert all(pl["roblox_place_id"] in (0, None) and "SYNTHETIC" in pl["studio_name"] for p in data["projects"] for pl in p["places"])


def test_readme_states_the_honest_limits_and_the_adapter():
    text = (ROOT / "README.md").read_text().lower()
    for phrase in ("never run against", "guide-core", "guide_adapter", "placeholder", "schema_unverified", "taste", "anatomy", "perspective", "self-written", "evals/real", "what you still need to supply",
                   "never uploaded", "follow-up", "the evals only show the tool agrees with itself"):
        assert phrase in text, phrase


def test_dependencies_are_declared():
    text = (ROOT / "pyproject.toml").read_text()
    for dep in ("guide-core", "pillow", "numpy", "mcp"):
        assert dep in text.lower(), dep


def test_cli_end_to_end(project, capsys, images):
    run = cli_run
    a = images("a", {"gen": "vstep", "size": [64, 64], "left": [0, 0, 0], "right": [255, 255, 255], "x": 32})
    assert run(project, hooks_mod.HOOKS, ["projects"]) == 0 and "demo-stylized-obby" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["measure", a, "--project-id", "demo-stylized-obby"]) in (0, 1)
    assert json.loads(capsys.readouterr().out)["summary"].startswith("measure_image:")
    assert run(project, hooks_mod.HOOKS, ["measure", a, "--project-id", "no-such-game"]) == 2
    assert "unknown project_id" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["measure", a, "--project-id", "demo-stylized-obby", "--place-id", "nowhere"]) == 2
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["domain"]["thresholds"] >= 40
    assert run(project, hooks_mod.HOOKS, ["call"]) == 0 and "measure_image" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["tiling", a, "--project-id", "demo-stylized-obby"]) == 1  # a step image is seamed: exit 1
    capsys.readouterr()


def test_real_stdio_smoke():
    smoke = Path("/home/user/workshop/suite/smoke_stdio.py")
    if not smoke.exists():
        pytest.skip("suite smoke script not present")
    proc = subprocess.run([sys.executable, str(smoke), str(ROOT), "visualverify", "get_style_brief", json.dumps(MAIN)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    assert info["called"] == "get_style_brief" and not info["is_error"] and info["tool_count"] == 17


def test_telemetry_is_off_by_default_and_opt_in(project, images, monkeypatch):
    a = images("a", {"gen": "solid", "size": [16, 16], "color": [9, 9, 9]})
    call(project, "get_style_brief", **MAIN)
    assert not (project.workspace / "telemetry.jsonl").exists()
    monkeypatch.setenv("VISUALVERIFY_TELEMETRY", "1")
    call(project, "get_style_brief", **MAIN)
    rows = [json.loads(x) for x in (project.workspace / "telemetry.jsonl").read_text().splitlines()]
    assert rows and rows[0]["tool"] == "get_style_brief" and rows[0]["chars"] > 0


def test_rules_file_covers_every_measuring_tool_and_names_real_thresholds(project):
    import yaml

    rules = yaml.safe_load((ROOT / "rules" / "measurements.yaml").read_text())["measurements"]
    meta = yaml.safe_load((ROOT / "style" / "thresholds.yaml").read_text())["thresholds"]
    assert set(rules) == SPEC_READ_ONLY - {"get_style_brief", "find_past_corrections"}
    for name, entry in rules.items():
        assert set(entry["thresholds"]) <= set(meta), name
        assert {"taste", "anatomy", "perspective"} <= set(entry["not_measured"]), name


def test_the_readme_numbers_match_the_repository():
    import yaml

    readme = (ROOT / "README.md").read_text()
    n_thresholds = len(yaml.safe_load((ROOT / "style" / "thresholds.yaml").read_text())["thresholds"])
    n_tasks = len(evals.load_tasks(ROOT / "evals" / "tasks"))
    assert f"{n_thresholds} named parameters" in readme, n_thresholds
    assert f"{n_tasks}/{n_tasks}" in readme, n_tasks
    assert f"{n_tasks} tasks in `evals/tasks/`" in readme
