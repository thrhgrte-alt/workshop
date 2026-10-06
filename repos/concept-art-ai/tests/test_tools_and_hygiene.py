"""MCP tools end to end, safety defaults, curation and repository hygiene."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from conceptai import hooks as hooks_mod
from conceptai.core import agentfiles, evals
from conceptai.core.cli import all_tools, run
from conceptai.core.manifest import LibraryStore, base_schema
from conceptai.core.mcpkit import build_server, call_local
from conceptai.core.style import load_style, validate_style
from conceptai.domain import synthetic
from conceptai.domain.schema import DOMAIN_SCHEMA

ENV = {"subject": "a ruined lighthouse on a cliff", "kind": "environment", "locked": {"axes": {"materials": "weathered_stone"}}}
PROP = {"subject": "an iron lantern", "kind": "prop"}


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return call_local(tools(project), tool, kw)


@pytest.fixture()
def gen_script(tmp_path, monkeypatch):
    """A real external 'generator' wired through the command adapter: it draws from the prompt-independent seed so runs differ."""
    script = tmp_path / "gen.py"
    script.write_text("import sys, random\nfrom PIL import Image\nout, seed, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])\n"
                      "rng = random.Random(seed)\nimg = Image.new('RGB', (w, h), tuple(rng.randint(30, 220) for _ in range(3)))\n"
                      "for _ in range(6):\n    x, y = rng.randint(0, w - 10), rng.randint(0, h - 10)\n    img.paste(tuple(rng.randint(0, 255) for _ in range(3)), (x, y, x + w // 4, y + h // 4))\nimg.save(out)\n")
    monkeypatch.setenv("CONCEPTAI_IMAGE_COMMAND", json.dumps([sys.executable, str(script), "{out}", "{seed}", "{width}", "{height}"]))
    monkeypatch.setenv("CONCEPTAI_IMAGE_MODEL", "local-test-generator")
    return script


def test_directions_and_prompts_through_tools(project):
    res = call(project, "propose_directions", brief=ENV, n=4, seed=1)
    assert len(res["directions"]) == 4 and res["min_axes_differing"] >= 3 and res["status"] == "concept_only"
    assert all(d["axes"]["materials"] == "weathered_stone" for d in res["directions"])
    pr = call(project, "build_prompt", brief=ENV, direction=res["directions"][0])
    assert "Environment concept art" in pr["positive"] and "watermark" in pr["negative"]
    with pytest.raises(ValueError, match="direction is for kind"):
        call(project, "build_prompt", brief=PROP, direction=res["directions"][0])


def test_generate_is_a_dry_run_by_default_and_writes_nothing(project):
    plan = call(project, "generate_concepts", brief=ENV, n_directions=3, adapter="placeholder")
    assert plan["dry_run"] and len(plan["prompts"]) == 3 and plan["summary"]["images_expected"] == 3
    assert not (project.workspace / "generations").exists() and not (project.workspace / "feedback").exists()
    with pytest.raises(ValueError, match="writes nothing"):
        call(project, "generate_concepts", brief=ENV, adapter="dryrun", dry_run=False)


def test_unconfigured_command_adapter_refuses(project):
    plan = call(project, "generate_concepts", brief=ENV, adapter="command")
    assert plan["summary"]["adapter"]["available"] is False
    with pytest.raises(ValueError, match="not available"):
        call(project, "generate_concepts", brief=ENV, adapter="command", dry_run=False)
    assert not (project.workspace / "generations").exists()


def test_full_loop_with_a_real_external_command(project, gen_script):
    res = call(project, "generate_concepts", brief=ENV, n_directions=3, adapter="command", per_direction=2, seed=5, dry_run=False)
    gid = res["gen_id"]
    assert len(res["outputs"]) == 6 and res["warning"] is None
    rec = call(project, "get_generation", gen_id=gid)
    assert rec["adapter"]["model"] == "local-test-generator" and rec["adapter"]["model_verified"] is False and rec["status"] == "concept_only"
    assert [r["seed"] for r in rec["requests"]] == [5, 1005, 2005] and all(r["prompt"] for r in rec["requests"])
    assert {o["direction_id"] for o in rec["outputs"]} == {d["id"] for d in rec["directions"]}
    for o in rec["outputs"]:
        assert Path(o["path"]).exists() and Path(o["path"]).parent == project.workspace / "generations" / gid
    first = rec["outputs"][0]["output_id"]
    ev = call(project, "evaluate_concept", gen_id=gid, output_id=first)
    assert ev["status"] == "concept_only" and not ev["rubric"]["passed"] and "brief_adherence" in ev["rubric"]["unscored"]
    cmp_ = call(project, "compare_outputs", gen_id=gid)
    assert len(cmp_["pairs"]) == 15 and cmp_["outputs"] == 6
    # nothing is learned until the user's verdict is stored
    assert call(project, "find_past_corrections", request="ruined lighthouse cliff")["corrections"] == []
    call(project, "rate_concept", gen_id=gid, output_id=first, decision="revise", reason="too dark", corrections=[{"dimension": "lighting", "direction": "more", "note": "brighter"}], dry_run=False)
    found = call(project, "find_past_corrections", request="ruined lighthouse cliff")["corrections"]
    assert found and found[0]["corrections"][0]["dimension"] == "lighting"
    assert call(project, "list_generations")["runs"][0]["feedback"] == 1


def test_curation_gate_keeps_ai_outputs_out_of_the_library_until_accepted(project, gen_script):
    gid = call(project, "generate_concepts", brief=PROP, n_directions=2, adapter="command", dry_run=False)["gen_id"]
    out = call(project, "get_generation", gen_id=gid)["outputs"][0]["output_id"]
    with pytest.raises(ValueError, match="no recorded feedback"):
        call(project, "promote_concept", gen_id=gid, output_id=out, title="t", description="d", tags=["lantern"], dry_run=False)
    call(project, "rate_concept", gen_id=gid, output_id=out, decision="revise", reason="not yet", dry_run=False)
    dry = call(project, "promote_concept", gen_id=gid, output_id=out, title="Lantern A", description="d", tags=["lantern"])
    assert dry["dry_run"] and dry["would_create"]["status"] == "candidate" and dry["would_create"]["domain"]["ai_generated"] is True
    assert not (project.workspace / "library").exists()
    with pytest.raises(ValueError, match="no 'accept' decision"):
        call(project, "promote_concept", gen_id=gid, output_id=out, title="Lantern A", description="d", tags=["lantern"], confirm=True, dry_run=False)
    cand = call(project, "promote_concept", gen_id=gid, output_id=out, title="Lantern A", description="d", tags=["lantern"], dry_run=False)["asset"]
    assert cand["status"] == "candidate"
    assert cand["id"] not in [h["id"] for h in call(project, "search_concept_library", query="lantern Lantern A", subject_kind="prop")["positive"]]
    call(project, "rate_concept", gen_id=gid, output_id=out, decision="accept", dry_run=False)
    cur = call(project, "promote_concept", gen_id=gid, output_id=out, title="Lantern B", description="a curated lantern", tags=["lantern"], confirm=True, dry_run=False)["asset"]
    assert cur["status"] == "curated"
    hits = [h["id"] for h in call(project, "search_concept_library", query="Lantern B curated lantern", subject_kind="prop")["positive"]]
    assert cur["id"] in hits
    row = next(a for a in LibraryStore(project).load() if a["id"] == cur["id"])
    assert row["domain"]["ai_generated"] is True and row["domain"]["concept_only"] is True and row["provenance"]["origin"] == "ai-generated" and row["domain"]["model"] == "local-test-generator"
    assert LibraryStore(project).validate_all(check_files=True)["ok"]


def test_synthetic_fixtures_are_never_promoted(project):
    gid = call(project, "generate_concepts", brief=ENV, n_directions=2, adapter="placeholder", dry_run=False)["gen_id"]
    out = call(project, "get_generation", gen_id=gid)["outputs"][0]["output_id"]
    call(project, "rate_concept", gen_id=gid, output_id=out, decision="accept", dry_run=False)
    with pytest.raises(ValueError, match="synthetic fixtures"):
        call(project, "promote_concept", gen_id=gid, output_id=out, title="t", description="d", tags=["x"], confirm=True, dry_run=False)


def test_rating_validates_before_saving(project):
    gid = call(project, "generate_concepts", brief=ENV, n_directions=2, adapter="placeholder", dry_run=False)["gen_id"]
    out = call(project, "get_generation", gen_id=gid)["outputs"][0]["output_id"]
    with pytest.raises(ValueError, match="reason or at least one correction"):
        call(project, "rate_concept", gen_id=gid, output_id=out, decision="reject", dry_run=False)
    with pytest.raises(ValueError, match="unknown correction dimension"):
        call(project, "rate_concept", gen_id=gid, output_id=out, decision="revise", corrections=[{"dimension": "vibes", "note": "x"}], dry_run=False)
    with pytest.raises(ValueError, match="unknown output"):
        call(project, "rate_concept", gen_id=gid, output_id="nope", decision="accept", dry_run=False)
    assert call(project, "get_generation", gen_id=gid)["feedback"] == []


def test_reading_images_is_confined(project, tmp_path):
    outside = synthetic.from_spec({"kind": "solid", "color": "#336699"}, tmp_path / "outside" / "x.png")
    with pytest.raises(PermissionError, match="outside the allowed"):
        call(project, "analyze_image", path=outside)
    with pytest.raises(PermissionError):
        call(project, "analyze_image", path="/etc/passwd")
    with pytest.raises(ValueError, match="not an existing image"):
        call(project, "analyze_image", path=str(project.root / "README.md"))
    ok = call(project, "analyze_image", path=str(project.root / "examples" / "positive" / "env_lighthouse_golden.png"), palette=["#c08040"])
    assert ok["metrics"]["value_range"] > 0.5 and ok["status"] == "concept_only"


def test_asset_root_widens_reading_only(project, tmp_path, monkeypatch):
    img = synthetic.from_spec({"kind": "box", "bg": "#101010", "fg": "#f0f0f0", "box": [0.3, 0.3, 0.7, 0.7]}, tmp_path / "refs" / "a.png")
    monkeypatch.setenv("CONCEPTAI_ASSET_ROOT", str(tmp_path / "refs"))
    assert call(project, "extract_reference_traits", paths=[img])["palette"]
    with pytest.raises(PermissionError):
        call(project, "generate_concepts", brief=ENV, adapter="placeholder", dry_run=False, reference_paths=[str(tmp_path / "other.png")])


def test_extract_reference_traits(project, tmp_path, monkeypatch):
    a = synthetic.from_spec({"kind": "box", "bg": "#c06020", "fg": "#f0e0c0", "box": [0.2, 0.2, 0.8, 0.8]}, tmp_path / "r" / "a.png")
    b = synthetic.from_spec({"kind": "box", "bg": "#c26222", "fg": "#f0e0c0", "box": [0.3, 0.3, 0.7, 0.7]}, tmp_path / "r" / "b.png")
    c = synthetic.from_spec({"kind": "box", "bg": "#206080", "fg": "#40c080", "box": [0.1, 0.5, 0.9, 0.9]}, tmp_path / "r" / "c.png")
    monkeypatch.setenv("CONCEPTAI_ASSET_ROOT", str(tmp_path / "r"))
    two = call(project, "extract_reference_traits", paths=[a, b])
    assert len(two["palette"]) == 2 and "locked" not in two["suggested_brief_fragment"] and "fewer than 3" in two["palette_note"]
    res = call(project, "extract_reference_traits", paths=[a, b, c])
    assert res["palette"][0] in ("#c06020", "#c26222") and len(res["palette"]) == len(set(res["palette"])) <= 6 and res["palette_note"] is None
    assert res["suggested_brief_fragment"]["locked"]["palette"] == res["palette"][:5] and "licensed" in res["caution"]
    with pytest.raises(ValueError, match="between 1 and 12"):
        call(project, "extract_reference_traits", paths=[])
    # the fragment is a valid locked palette for propose_directions
    assert call(project, "propose_directions", brief={**ENV, **res["suggested_brief_fragment"]}, n=2)["directions"][0]["palette_locked"] is True
    with pytest.raises(ValueError, match="3-8 colours"):
        call(project, "propose_directions", brief={**ENV, "locked": {"palette": res["palette"][:2]}})


def test_analysis_tools_on_examples(project):
    root = project.root / "examples"
    flat = call(project, "analyze_image", path=str(root / "negative" / "env_flat_low_contrast.png"))
    assert any(f["metric"] == "value_range" for f in flat["flags"]) and flat["degenerate"]
    noisy = call(project, "analyze_image", path=str(root / "negative" / "prop_fragmented_noise.png"), kind="prop")
    assert {f["metric"] for f in noisy["flags"]} >= {"edge_density", "silhouette_components"}
    dup = call(project, "check_novelty", path=str(root / "negative" / "env_near_duplicate.png"), subject_kind="environment")
    assert dup["nearest"] == "env-lighthouse-golden-001" and dup["novelty"] < 0.05 and dup["min_novelty"] == 0.15
    lock = call(project, "check_composition_lock", path=str(root / "negative" / "env_flat_low_contrast.png"), focal_box=[0.4, 0.4, 0.6, 0.6])
    assert not lock["ok"] and lock["findings"][0]["code"] == "focal_contrast_low"


def test_mcp_session(project, gen_script):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        dirs = await client.call_tool("propose_directions", {"brief": PROP, "n": 3})
        plan = await client.call_tool("generate_concepts", {"brief": PROP, "n_directions": 2, "adapter": "command"})
        bad = await client.call_tool("generate_concepts", {"brief": {"subject": "", "kind": "prop"}})
        refused = await client.call_tool("build_prompt", {"brief": {**PROP, "notes": "game-ready"}, "direction": dirs.structuredContent["directions"][0]})
        return listed, dirs, plan, bad, refused

    listed, dirs, plan, bad, refused = asyncio.run(_run(server, go))
    assert listed["propose_directions"].annotations.readOnlyHint is True and listed["generate_concepts"].annotations.readOnlyHint is False
    assert listed["lora_make_config"].annotations.readOnlyHint is False and listed["lora_validate_dataset"].annotations.readOnlyHint is True
    assert not dirs.isError and len(dirs.structuredContent["directions"]) == 3 and plan.structuredContent["dry_run"] is True
    assert bad.isError and "non-empty" in bad.content[0].text and refused.isError and "production readiness" in refused.content[0].text


async def _run(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_lora_tools_prepare_but_never_train(project, tmp_path, monkeypatch):
    ds = tmp_path / "ds"
    monkeypatch.setenv("CONCEPTAI_ASSET_ROOT", str(ds))
    (ds / "img").mkdir(parents=True)
    items = []
    for i in range(16):
        bg = "#{:02x}{:02x}{:02x}".format((40 + i * 37) % 200 + 20, (90 + i * 53) % 200 + 20, (150 + i * 29) % 200 + 20)
        synthetic.from_spec({"kind": "box", "bg": bg, "fg": "#f0e0a0", "box": [(i % 5) * 0.16, (i // 5) * 0.28 + 0.05, (i % 5) * 0.16 + 0.25, (i // 5) * 0.28 + 0.35], "size": [640, 640]}, ds / "img" / f"{i}.png")
        items.append({"file": f"img/{i}.png", "caption": f"cptstyle stylised ruin {i}", "license": "own-work", "owner": "user"})
    import yaml

    (ds / "dataset.yaml").write_text(yaml.safe_dump({"dataset": {"id": "ruins", "trigger_word": "cptstyle"}, "items": items}))
    rep = call(project, "lora_validate_dataset", dataset_dir=str(ds))
    assert rep["ok"] and rep["usable"] == 16
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(PermissionError):
        call(project, "lora_validate_dataset", dataset_dir=str(outside))  # not under allowed roots / asset root
    dry = call(project, "lora_make_config", dataset_dir=str(ds), base_model="my-base")
    assert dry["dry_run"] and not (project.workspace / "lora").exists() and dry["config"]["command_template"].startswith("<your-trainer>")
    real = call(project, "lora_make_config", dataset_dir=str(ds), base_model="my-base", dry_run=False)
    cfg = yaml.safe_load(Path(real["config_path"]).read_text())
    assert cfg["adapter"]["rank"] == 16 and "has not trained" in cfg["unverified"] and Path(real["validation_plan_path"]).exists()
    assert Path(real["config_path"]).parent.parent.parent == project.workspace
    flagged = call(project, "lora_check_overfit", dataset_dir=str(ds), output_paths=[str(ds / "img" / "3.png")])
    assert not flagged["ok"] and flagged["flagged"][0]["training_image"] == "img/3.png"
    (ds / "dataset.yaml").write_text(yaml.safe_dump({"dataset": {"id": "ruins", "trigger_word": "cptstyle"}, "items": items[:3]}))
    with pytest.raises(ValueError, match="dataset has"):
        call(project, "lora_make_config", dataset_dir=str(ds), base_model="my-base", dry_run=False)


def test_every_tool_is_documented_in_the_readme(project):
    readme = (project.root / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"


def test_every_result_that_can_be_mistaken_for_an_asset_says_concept_only(project, gen_script):
    gid = call(project, "generate_concepts", brief=PROP, n_directions=2, adapter="command", dry_run=False)
    assert gid["status"] == "concept_only"
    rec = call(project, "get_generation", gen_id=gid["gen_id"])
    assert rec["status"] == "concept_only" and "Nothing here is a mesh" in rec["note"]
    text = (project.root / "style" / "style.yaml").read_text()
    assert "Never present it as a production-ready mesh" in text
    for name in ("search_concept_library", "analyze_image", "propose_directions"):
        assert call(project, name, **{"search_concept_library": {"query": "x"}, "analyze_image": {"path": str(project.root / "examples/positive/prop_lantern_round.png"), "kind": "prop"},
                                      "propose_directions": {"brief": PROP}}[name])["status"] == "concept_only"


def test_library_style_skills_schema_and_baseline(project):
    assert LibraryStore(project).validate_all(check_files=True)["ok"]
    assert validate_style(load_style(project)) == []
    assert agentfiles.validate_all_skills(project.root) == [] and agentfiles.sync(project.root, check=True) == []
    from conceptai import KINDS

    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=project.root)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((project.root / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_example_measurements_match_the_code(project):
    from conceptai.domain import analysis

    seen = 0
    for row in LibraryStore(project).load():
        if row["id"].startswith(("env-", "prop-")) and row["path"].startswith("examples/"):
            palette = row["style"].get("palette") or ()
            m = analysis.measure(project.root / row["path"], target_palette=palette)
            for k, v in row["domain"]["measurements"].items():
                assert m[k] == pytest.approx(v, abs=0.002), (row["id"], k)
            seen += 1
    assert seen == 6
    assert all(r["domain"]["synthetic_fixture"] and r["domain"]["concept_only"] for r in LibraryStore(project).load())


def test_example_negatives_actually_fail_their_checks(project):
    """Each avoid-example must be bad in the way its notes say; otherwise it teaches nothing."""
    rows = {r["id"]: r for r in LibraryStore(project).load()}
    ranges = load_style(project)["ranges"]
    assert rows["env-flat-low-contrast-001"]["domain"]["measurements"]["value_range"] < ranges["value_range"]["min"]
    assert rows["env-off-palette-001"]["domain"]["palette_adherence_vs_intended"] < ranges["palette_adherence"]["min"]
    assert rows["prop-fragmented-noise-001"]["domain"]["measurements"]["silhouette_components"] > ranges["silhouette_components"]["max"]
    assert rows["prop-fragmented-noise-001"]["domain"]["measurements"]["edge_density"] > ranges["edge_density"]["max"]
    assert rows["env-near-duplicate-001"]["tags"] and "duplicate" in rows["env-near-duplicate-001"]["tags"]
    for pid in ("env-lighthouse-golden-001", "prop-lantern-round-001"):
        m = rows[pid]["domain"]["measurements"]
        assert m["value_range"] >= ranges["value_range"]["min"] and m["palette_adherence"] >= ranges["palette_adherence"]["min"] and ranges["edge_density"]["min"] <= m["edge_density"] <= ranges["edge_density"]["max"]


def test_evals_fail_when_the_system_is_broken(project, monkeypatch):
    from conceptai.domain import adapters, analysis, direction, prompts

    monkeypatch.setattr(prompts, "FORBIDDEN_CLAIMS", __import__("re").compile(r"zzzz-never"))
    monkeypatch.setattr(analysis, "novelty_vs_library", lambda p, e: {"novelty": 1.0, "nearest": None, "compared": len(e)})
    monkeypatch.setattr(direction, "distance", lambda kind, a, b: 99.0)
    monkeypatch.setattr(direction, "axes_differing", lambda kind, a, b: 6)
    monkeypatch.setattr(adapters, "check_request", lambda r: {"count": 1, "seed": 0, "reference_images": [], "settings": {}, "negative": "", **r})
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="broken", root=project.root)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert {"prompt-refuses-production-ready", "novelty-unknown-when-library-empty", "novelty-identical-is-zero", "adapter-rejects-tiny-size"} <= failed, failed


def test_cli_end_to_end(project, capsys, tmp_path):
    brief = str(project.root / "examples" / "briefs" / "lighthouse_env.yaml")
    assert run(project, hooks_mod.HOOKS, ["directions", brief, "-n", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["directions"]) == 3 and out["min_axes_differing"] >= 3
    assert run(project, hooks_mod.HOOKS, ["prompt", brief]) == 0 and "Environment concept art" in json.loads(capsys.readouterr().out)["positive"]
    assert run(project, hooks_mod.HOOKS, ["analyze", str(project.root / "examples" / "positive" / "env_lighthouse_golden.png")]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["value_range"] > 0.5
    assert run(project, hooks_mod.HOOKS, ["adapters"]) == 0 and {a["name"] for a in json.loads(capsys.readouterr().out)["adapters"]} == {"dryrun", "placeholder", "command"}
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    assert "never trains" in json.loads(capsys.readouterr().out)["domain"]["lora"]
