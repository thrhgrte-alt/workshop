"""The preflight's tunable limits on guide-core's learning layer: same defaults as rules/profiles, no change to results, precedence, export-skill."""

import json
from pathlib import Path

import pytest

from guide_core import agentfiles
from guide_core.params import StepTooLarge
from guide_core.scope import Scope
from preflight import hooks as hooks_mod
from preflight import learning_params as LP
from preflight.domain import engine
from preflight.guide_adapter import all_tools, call_local, run

ROOT = Path(__file__).resolve().parents[1]
SBX, OBBY, TYC = "synthetic-sandbox", "synthetic-obby", "synthetic-mining-tycoon"
ASSET = str(ROOT / "examples/assets/bad_tris_10800.glb")  # 10800 triangles: over the prop budget of 10000
TRI = "limit.prop.MESH_TRI_BUDGET.max_triangles"


def report(project, pid, **kw):
    return call_local(all_tools(project, hooks_mod.HOOKS), "preflight_report", {"project_id": pid, "path": ASSET, "profile": "prop", **kw})


def rules_of(rep):
    return [f["rule"] for f in rep["findings"]]


def test_registered_defaults_are_the_limits_the_profiles_already_resolve(project):
    cfg = engine.load_config(project.root)
    specs = {s.name: s for s in LP.param_specs(cfg)}
    assert len(specs) == 138 and set(n.split(".")[1] for n in specs) == {"prop", "tool", "character_accessory", "terrain_piece"}
    want = {TRI: 10000, "limit.tool.MESH_TRI_BUDGET.max_triangles": 6000, "limit.character_accessory.MESH_TRI_BUDGET.max_triangles": 4000, "limit.terrain_piece.MESH_TRI_BUDGET.max_triangles": 20000,
            "limit.prop.MESH_TRI_BUDGET_TOTAL.max_triangles_total": 15000, "limit.prop.MESH_OBJECT_COUNT.max_mesh_objects": 12, "limit.prop.MAT_COUNT.max_materials": 4,
            "limit.prop.TEX_OVERSIZE.max_dimension_px": 2048, "limit.tool.TEX_OVERSIZE.max_dimension_px": 1024, "limit.prop.COL_PROXY_MISSING.required_above_triangles": 2000,
            "limit.tool.RIG_BONE_COUNT.max_bones": 8, "limit.character_accessory.RIG_BONE_COUNT.max_bones": 16, "limit.prop.XFM_ORIGIN_PLACEMENT.tolerance": 0.1,
            "limit.terrain_piece.XFM_ORIGIN_PLACEMENT.tolerance": 0.15}  # read by hand from rules/profiles/*.yaml
    assert all(specs[n].default == v for n, v in want.items())
    assert specs["limit.prop.MESH_TRI_BUDGET.max_triangles"].verify_against_current_docs  # Roblox-sourced limits keep their "verify" flag
    assert "limit.prop.RIG_BONE_COUNT.max_bones" not in specs  # rig rules do not apply to the prop profile, so nothing is registered for it
    assert "limit.prop.UV_TEXEL_SPREAD.max_spread_ratio" in specs and specs["limit.prop.UV_TEXEL_SPREAD.max_spread_ratio"].max > 4  # a ratio above 1 is not capped at 1
    assert all(not isinstance(s.default, (bool, str, list)) for s in specs.values())


def test_every_numeric_profile_limit_is_registered(project):
    cfg = engine.load_config(project.root)
    registered = {s.name for s in LP.param_specs(cfg)}
    for (p, rid, key), (v, _) in LP.effective_numeric_limits(cfg).items():
        assert f"limit.{p}.{rid}.{key}" in registered and isinstance(v, (int, float))


def test_nothing_learned_means_the_same_config_object(project):
    base = engine.load_config(project.root)
    cfg = engine.load_config(project.root, SBX)
    assert LP.effective_config(project, SBX, cfg) is cfg  # no params file: one stat call, same object
    ps = LP.store(project)
    ps.update(TRI, 11000, Scope(OBBY), approved_by="amy", reason="r")  # a value for ANOTHER project
    assert LP.apply_params(cfg, base, ps, Scope(SBX)) is cfg


def test_a_learned_limit_changes_only_its_project_and_rolls_back_exactly(project):
    before = report(project, SBX)
    assert "MESH_TRI_BUDGET" in rules_of(before) and before["ready_to_upload"] is False
    ps = LP.store(project)
    with pytest.raises(StepTooLarge):
        ps.update(TRI, 15000, Scope(SBX), approved_by="amy", reason="r")
    v = ps.update(TRI, 11000, Scope(SBX), approved_by="amy", reason="the user accepts up to 11k for props in this game", evidence=["run-1"])
    assert v["version"] == 1
    after = report(project, SBX)
    assert "MESH_TRI_BUDGET" not in rules_of(after) and after["ready_to_upload"] is True
    assert "MESH_TRI_BUDGET" in rules_of(report(project, OBBY))  # another project still has the default
    ps.rollback(TRI, Scope(SBX), approved_by="amy", reason="back")
    assert report(project, SBX) == before  # exactly the original report


def test_precedence_call_override_beats_learned_and_the_projects_own_profile_beats_learned(project):
    ps = LP.store(project)
    ps.update(TRI, 11000, Scope(SBX), approved_by="amy", reason="r")
    over = report(project, SBX, limit_overrides={"MESH_TRI_BUDGET": {"max_triangles": 9000}})
    assert [(f["rule"], f["limit"]) for f in over["findings"] if f["rule"] == "MESH_TRI_BUDGET"] == [("MESH_TRI_BUDGET", 9000)]  # explicit value in the call wins
    # the mining-tycoon profile file sets 12000 itself; a learned (lower) 9000 must not replace that explicit number
    ps.update(TRI, 9000, Scope(TYC), approved_by="amy", reason="r")
    tyc = report(project, TYC)
    assert "MESH_TRI_BUDGET" not in rules_of(tyc)  # 10800 <= 12000 from profiles.yaml
    ps.update(TRI, 9000, Scope(OBBY), approved_by="amy", reason="r")
    assert [(f["rule"], f["limit"]) for f in report(project, OBBY)["findings"] if f["rule"] == "MESH_TRI_BUDGET"] == [("MESH_TRI_BUDGET", 4000)]  # obby's profiles.yaml sets 4000 explicitly
    assert "MESH_TRI_BUDGET" not in rules_of(report(project, SBX))  # sandbox has no explicit limit: its learned 11000 applies (10800 passes)


def test_saved_overrides_the_user_recorded_still_win_over_learned_values(project):
    tools = {t.name: t for t in all_tools(project, hooks_mod.HOOKS)}
    tools["record_override"].fn(project_id=SBX, rule="MESH_TRI_BUDGET", asset_type="prop", reason="this crate may be 30k tris for the hero scene", value=30000, dry_run=False)
    LP.store(project).update(TRI, 9000, Scope(SBX), approved_by="amy", reason="r")
    assert "MESH_TRI_BUDGET" not in rules_of(report(project, SBX))


def test_export_skill_is_scoped_and_refuses_unregistered_projects(project, capsys, tmp_path):
    tools = {t.name: t for t in all_tools(project, hooks_mod.HOOKS)}
    rid = tools["record_run"].fn(project_id=SBX, request="crate review")["run_id"]
    tools["record_decision"].fn(project_id=SBX, run_id=rid, decision="revise", reason="too strict", corrections=[{"dimension": "profile_limit", "note": "sandbox crate limit note"}])
    rid2 = tools["record_run"].fn(project_id=OBBY, request="obby review")["run_id"]
    tools["record_decision"].fn(project_id=OBBY, run_id=rid2, decision="revise", reason="x", corrections=[{"dimension": "profile_limit", "note": "obby only note"}])
    LP.store(project).update(TRI, 11000, Scope(SBX), approved_by="amy", reason="r")
    assert run(project, hooks_mod.HOOKS, ["export-skill", "--scope", SBX]) == 0
    sbx = json.loads(capsys.readouterr().out)
    md = sbx["files"]["asset-roblox-preflight/SKILL.md"]
    assert sbx["problems"] == [] and sbx["scope"] == SBX and "Knowledge version 1" in md and "sandbox crate limit note" in md and "obby only note" not in md
    assert f"{TRI}=11000 (v1, project)" in md and "NOT verified: never run against real Blender" in md
    assert run(project, hooks_mod.HOOKS, ["export-skill"]) == 0
    glob = json.loads(capsys.readouterr().out)
    assert "sandbox crate limit note" not in json.dumps(glob) and f"| {TRI} | 10000 | default |" in glob["files"]["asset-roblox-preflight/references/parameters.md"]
    assert run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "nope-project"]) == 2
    assert "unknown project_id" in capsys.readouterr().err
    out = tmp_path / "skills"
    assert run(project, hooks_mod.HOOKS, ["export-skill", "--scope", SBX, "--out", str(out), "--write"]) == 0
    assert agentfiles.validate_skill(out / "asset-roblox-preflight") == []
