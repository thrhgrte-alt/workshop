"""The balancer's tunable parameters on guide-core's learning layer: same defaults as style/style.yaml, no change to results, export-skill."""

import json
from pathlib import Path

import pytest
import yaml

from guide_core import agentfiles, mcpkit
from guide_core.params import StepTooLarge
from guide_core.scope import Scope
from econbal import hooks as hooks_mod
from econbal import learning_params as LP
from econbal.domain import places
from econbal.guide_adapter import all_tools

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"project_id": "demo_mine", "place_id": "main"}
HARD = {"project_id": "demo_mine", "place_id": "hardcore"}
SC_MAIN, SC_HARD = Scope("demo_mine", "main"), Scope("demo_mine", "hardcore")


def call(project, tool, **kw):
    return mcpkit.call_local(all_tools(project, hooks_mod.HOOKS), tool, kw)


def bands_of(project, place):
    return {b["id"]: (b["min_minutes"], b["max_minutes"]) for b in call(project, "get_style_brief", **place)["bands"]}


def test_registered_defaults_are_the_numbers_already_in_the_style_file(project):
    specs = {s.name: s for s in LP.param_specs(LP.global_style(project))}
    assert len(specs) == 20  # 3 bands x (min, max) + 11 thresholds + 3 learning settings, counted by hand from style/style.yaml
    want = {"band.early.min_minutes": 5, "band.early.max_minutes": 15, "band.mid.min_minutes": 15, "band.mid.max_minutes": 45, "band.late.min_minutes": 45, "band.late.max_minutes": 120,
            "threshold.max_cost_jump_ratio.max": 5.0, "threshold.max_gap_jump_ratio.max": 4.0, "threshold.wall_factor.max": 3.0, "threshold.max_idle_minutes.max": 240,
            "threshold.max_days_per_step.max": 7.0, "threshold.max_payback_minutes.max": 240, "threshold.min_content_days.min": 7.0, "threshold.max_tail_net_share.max": 0.8,
            "threshold.max_boost_speedup.max": 3.0, "threshold.max_free_gap_over_band.max": 2.0, "threshold.dominance_margin.min": 0.1,
            "learning.min_votes": 2, "learning.shrink": 0.2, "learning.grow": 0.25}
    assert {n: s.default for n, s in specs.items()} == want
    assert all(not s.locked for s in specs.values()) and specs["threshold.max_idle_minutes.max"].kind == "int" and specs["threshold.wall_factor.max"].kind == "number"
    assert specs["threshold.max_tail_net_share.max"].max == 1.0  # a share cannot exceed 1


def test_nothing_learned_means_the_same_style_object_and_other_places_are_unaffected(project):
    entry = places.resolve(project, "demo_mine", "main")
    style, _ = places.layered_style(project, entry)
    fresh = LP.apply_params(style, LP.store(project), SC_MAIN)
    assert fresh is style
    LP.store(project).update("band.early.max_minutes", 13, SC_HARD, approved_by="amy", reason="r")  # a value for ANOTHER place
    assert LP.apply_params(style, LP.store(project), SC_MAIN) is style


def test_a_learned_band_changes_only_its_own_place_and_rolls_back_exactly(project):
    before_main, before_hard = bands_of(project, MAIN), bands_of(project, HARD)
    assert before_main == {"early": (5, 15), "mid": (15, 45), "late": (45, 120)}
    ps = LP.store(project)
    v = ps.update("band.early.max_minutes", 13, SC_MAIN, approved_by="amy", reason="playtests: tier 2 felt too slow at 15", evidence=["run-1", "run-2"])
    assert v["version"] == 1 and v["evidence"] == ["run-1", "run-2"]
    assert bands_of(project, MAIN) == {"early": (5, 13), "mid": (15, 45), "late": (45, 120)}
    assert bands_of(project, HARD) == before_hard  # the hardcore place has its own bands and its own (default) parameters
    ps.rollback("band.early.max_minutes", SC_MAIN, approved_by="amy", reason="worse")
    assert bands_of(project, MAIN) == before_main


def test_learned_thresholds_reach_the_style_ranges_and_are_bounded(project):
    ps = LP.store(project)
    with pytest.raises(StepTooLarge):
        ps.update("threshold.max_cost_jump_ratio.max", 8.0, SC_MAIN, approved_by="amy", reason="r")
    ps.update("threshold.max_cost_jump_ratio.max", 4.5, SC_MAIN, approved_by="amy", reason="r")
    assert call(project, "get_style_brief", **MAIN)["ranges"]["max_cost_jump_ratio"]["max"] == 4.5
    assert call(project, "get_style_brief", **HARD)["ranges"]["max_cost_jump_ratio"]["max"] == 5.0


def test_a_learned_value_that_breaks_a_band_is_refused_loudly(project):
    ps = LP.store(project)
    for v in (13, 11, 9, 7, 5, 3):  # early max 15 -> 3 in bounded steps of 2; the band minimum stays 5
        ps.update("band.early.max_minutes", v, SC_MAIN, approved_by="amy", reason="r")
    with pytest.raises(ValueError, match="band 'early' invalid"):
        call(project, "get_style_brief", **MAIN)
    ps.rollback("band.early.max_minutes", SC_MAIN, approved_by="amy", reason="fix")
    assert bands_of(project, MAIN)["early"] == (5, 5)


def test_findings_follow_the_learned_band_and_return_when_it_is_rolled_back(project):
    base_main, base_hard = call(project, "check_economy", **MAIN), call(project, "check_economy", **HARD)
    assert base_main["verdict"] == "pass" and [f["code"] for f in base_main["findings"]] == []
    ps = LP.store(project)
    for v in (13, 11, 9, 7):  # tighten the early band maximum 15 -> 7 in bounded steps of 2
        ps.update("band.early.max_minutes", v, SC_MAIN, approved_by="amy", reason="tier 1-3 should come faster")
    tight = call(project, "check_economy", **MAIN)
    assert tight["verdict"] == "fail" and [f["code"] for f in tight["findings"]] == ["over_band"] * 3
    assert call(project, "check_economy", **HARD) == base_hard  # the other place is untouched
    for _ in range(4):
        ps.rollback("band.early.max_minutes", SC_MAIN, approved_by="amy", reason="back")
    assert call(project, "check_economy", **MAIN) == base_main  # exactly the original answer, not an approximation of it


def cli(project, capsys, *argv):
    from econbal.guide_adapter import cli as core_cli

    code = core_cli.run(project, hooks_mod.HOOKS, list(argv))
    return code, capsys.readouterr().out


def test_export_skill_dry_run_states_scope_version_and_limits(project, capsys, tmp_path):
    code, text = cli(project, capsys, "export-skill", "--scope", "demo_mine", "--place", "main", "--out", str(tmp_path / "s"))
    res = json.loads(text)
    md = res["files"]["roblox-economy-balancer/SKILL.md"]
    assert code == 0 and res["problems"] == [] and res["dry_run"] is True and not (tmp_path / "s").exists() and res["scope"] == "demo_mine/main"
    assert "Knowledge version 0" in md and "`check_economy`" in md and "NOT verified: never run against a real game" in md and "placeholders" in md
    assert "Nothing learned yet" in md and res["skill_lines"] <= 70


def test_export_skill_scopes_corrections_and_parameter_values_by_place(project, capsys, tmp_path):
    for place, note in ((MAIN, "main place note about early pacing"), (HARD, "hardcore place note about late pacing")):
        rid = call(project, "record_run", request=f"playtest {place['place_id']}", **place)["run_id"]
        call(project, "record_decision", run_id=rid, decision="revise", reason="felt slow", corrections=[{"dimension": "pacing", "tier": 4, "felt": "too_slow", "note": note}], **place)
    LP.store(project).update("band.early.max_minutes", 13, SC_MAIN, approved_by="amy", reason="r")
    main = json.loads(cli(project, capsys, "export-skill", "--scope", "demo_mine", "--place", "main")[1])
    hard = json.loads(cli(project, capsys, "export-skill", "--scope", "demo_mine", "--place", "hardcore")[1])
    assert "main place note" in main["files"]["roblox-economy-balancer/SKILL.md"] and "hardcore place note" not in main["files"]["roblox-economy-balancer/SKILL.md"]
    assert "hardcore place note" in hard["files"]["roblox-economy-balancer/SKILL.md"] and "main place note" not in hard["files"]["roblox-economy-balancer/SKILL.md"]
    assert "band.early.max_minutes=13 (v1, project)" in main["files"]["roblox-economy-balancer/SKILL.md"]
    assert "| band.early.max_minutes | 15 | default |" in hard["files"]["roblox-economy-balancer/references/parameters.md"]


def test_export_skill_refuses_unregistered_projects_and_places_and_writes_a_valid_folder(project, capsys, tmp_path):
    from econbal.guide_adapter import cli as core_cli

    assert core_cli.run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "nope"]) == 2
    assert "unknown project_id 'nope'" in capsys.readouterr().err
    assert core_cli.run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "demo_mine", "--place", "nope"]) == 2
    assert "unknown place 'demo_mine/nope'" in capsys.readouterr().err
    out = tmp_path / "skills"
    code, text = cli(project, capsys, "export-skill", "--scope", "demo_mine", "--out", str(out), "--write")
    assert code == 0 and agentfiles.validate_skill(out / "roblox-economy-balancer") == []
    glob = json.loads(cli(project, capsys, "export-skill")[1])
    assert glob["scope"] == "global"
