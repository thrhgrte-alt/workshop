"""The tunable numbers on guide-core's learning layer: same defaults as style/style.yaml, no change to results, scoped, bounded, reversible, export-skill."""

import json
from pathlib import Path

import pytest
import yaml

from conftest import MAIN
from playqa import hooks as hooks_mod
from playqa import learning_params as LP
from playqa.domain import places
from playqa.guide_adapter import agentfiles, all_tools, mcpkit, params as P, scope as S

ROOT = Path(__file__).resolve().parents[1]
SC_MAIN, SC_TYC = S.Scope("demo_mine", "main"), S.Scope("demo_tycoon", "main")


def call(project, tool, **kw):
    return mcpkit.call_local(all_tools(project, hooks_mod.HOOKS), tool, kw)


def test_registered_defaults_are_the_numbers_already_in_the_style_file(project):
    specs = {s.name: s for s in LP.param_specs(LP.global_style(project))}
    # counted by hand from style/style.yaml: 19 settings + 18 range bounds (boot errors, boot warnings, move fraction, waypoints, end gap, economy cap, economy floor, 5 performance percentages = 12 single bounds;
    # the 3 economy_expect_* ranges have both a min and a max = 6)
    assert len(specs) == 37 and sum(n.startswith("setting.") for n in specs) == 19 and sum(n.startswith("threshold.") for n in specs) == 18
    want = {"setting.boot_window_seconds": 10.0, "setting.flaky_repeats": 3, "setting.flaky_confirm_fail_fraction": 1.0, "setting.remote_max_probes": 6, "setting.console_max_findings": 10,
            "threshold.boot_error_lines.max": 0, "threshold.boot_warning_lines.max": 0, "threshold.spawn_min_move_fraction.min": 0.5, "threshold.reach_min_waypoints.min": 2,
            "threshold.reach_end_gap_studs.max": 4.0, "threshold.economy_max_abs_delta.max": 1000000, "threshold.economy_min_balance.min": 0, "threshold.economy_expect_gain.min": 1, "threshold.economy_expect_gain.max": 1000000, "threshold.economy_expect_spend.min": -1000000, "threshold.economy_expect_spend.max": -1,
            "threshold.economy_expect_unchanged.min": 0, "threshold.economy_expect_unchanged.max": 0, "threshold.perf_part_count_increase_pct.max": 10.0,
            "threshold.perf_script_count_increase_pct.max": 10.0, "threshold.perf_memory_increase_pct.max": 25.0, "threshold.perf_frame_ms_increase_pct.max": 25.0, "threshold.perf_memory_growth_pct_over_run.max": 20.0}
    assert {n: specs[n].default for n in want} == want
    assert specs["threshold.boot_error_lines.max"].locked and not any(s.locked for n, s in specs.items() if n != "threshold.boot_error_lines.max")
    assert specs["setting.agent_radius"].verify_against_current_docs and specs["setting.agent_height"].verify_against_current_docs
    assert specs["setting.flaky_repeats"].kind == "int" and specs["setting.boot_window_seconds"].kind == "number"


def test_every_number_in_the_style_file_is_registered_and_labelled_a_placeholder():
    style = yaml.safe_load((ROOT / "style" / "style.yaml").read_text())
    assert style["placeholder"] is True and "PLACEHOLDER" in style["name"]
    for k, e in style["settings"].items():
        assert set(e) >= {"value", "min", "max", "step", "note"} and e["min"] <= e["value"] <= e["max"], k
        assert "PLACEHOLDER" in e["note"] or e.get("verify_against_current_docs"), k
    for k, r in style["ranges"].items():
        assert "PLACEHOLDER" in r["note"] or r.get("locked"), k
    assert "PLACEHOLDER" in (ROOT / "style" / "STYLE.md").read_text()
    assert [k for k, e in style["settings"].items() if e.get("verify_against_current_docs")] == ["agent_radius", "agent_height"]


def test_checks_in_the_library_that_come_from_roblox_say_to_verify_them():
    from playqa.domain import checks as K

    lib = K.load_library(ROOT)
    assert {c for c, v in lib.items() if v.get("verify_against_current_docs")} == {"boot", "reachability", "remotes", "perf_snapshot"}
    assert yaml.safe_load((ROOT / "rules" / "log_patterns.yaml").read_text())["verify_against_current_docs"] is True


def test_nothing_learned_means_the_same_style_object_and_other_places_are_unaffected(project):
    ctx = places.resolve(project, "demo_mine", "main")
    base = LP.global_style(project)
    assert LP.apply_params(base, LP.store(project), SC_MAIN) is base
    LP.store(project).update("setting.boot_window_seconds", 15.0, SC_TYC, approved_by="amy", reason="r")  # a value for ANOTHER place
    assert LP.apply_params(base, LP.store(project), SC_MAIN) is base
    assert places.resolve(project, "demo_mine", "main").style["settings"]["boot_window_seconds"]["value"] == ctx.style["settings"]["boot_window_seconds"]["value"] == 10.0
    assert places.resolve(project, "demo_tycoon", "main").style["settings"]["boot_window_seconds"]["value"] == 15.0


def test_a_learned_value_reaches_the_checks_and_rolls_back_exactly(project):
    def spawn_need():
        return places.resolve(project, "demo_mine", "main").style["ranges"]["spawn_min_move_fraction"]["min"]

    ps = LP.store(project)
    assert spawn_need() == 0.5
    v = ps.update("threshold.spawn_min_move_fraction.min", 0.6, SC_MAIN, approved_by="amy", reason="a character that moves 5 of 8 studs is stuck for us", evidence=["run-1", "run-2"])
    assert v["version"] == 1 and v["evidence"] == ["run-1", "run-2"] and spawn_need() == 0.6
    ps.rollback("threshold.spawn_min_move_fraction.min", SC_MAIN, approved_by="amy", reason="worse")
    assert spawn_need() == 0.5


def test_a_learned_threshold_changes_a_real_verdict_for_its_own_place_only(project):
    from playqa.domain.harness import run_world_check

    base = run_world_check(project, "perf_snapshot", baseline="clean", setup={"add_parts": 3}, repeats=3)
    assert base["verdict"] == "fail"
    ps = LP.store(project)
    for v in (15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0):
        ps.update("threshold.perf_part_count_increase_pct.max", v, SC_MAIN, approved_by="amy", reason="our maps grow in steps")
    assert run_world_check(project, "perf_snapshot", baseline="clean", setup={"add_parts": 3}, repeats=3)["verdict"] == "pass"
    for _ in range(8):
        ps.rollback("threshold.perf_part_count_increase_pct.max", SC_MAIN, approved_by="amy", reason="back")
    assert run_world_check(project, "perf_snapshot", baseline="clean", setup={"add_parts": 3}, repeats=3)["verdict"] == "fail"


def test_steps_are_bounded_ranges_respected_and_locked_values_refused(project):
    ps = LP.store(project)
    with pytest.raises(P.StepTooLarge):
        ps.update("threshold.reach_end_gap_studs.max", 8.0, SC_MAIN, approved_by="amy", reason="r")  # step 1.0
    with pytest.raises(P.OutOfRange):
        ps.update("setting.flaky_repeats", 11, SC_MAIN, approved_by="amy", reason="r") if False else ps.update("threshold.spawn_min_move_fraction.min", 1.5, SC_MAIN, approved_by="amy", reason="r")
    with pytest.raises(P.LockedParameter):
        ps.update("threshold.boot_error_lines.max", 1, SC_MAIN, approved_by="amy", reason="r")
    with pytest.raises(P.ParamError):
        ps.update("setting.flaky_repeats", 2, SC_MAIN, approved_by="model", reason="r")  # a model cannot approve
    assert places.resolve(project, "demo_mine", "main").style["ranges"]["boot_error_lines"]["max"] == 0


def test_the_places_own_explicit_number_beats_a_learned_one(project):
    LP.store(project).update("setting.boot_window_seconds", 15.0, S.Scope("demo_obby", "main"), approved_by="amy", reason="r")
    ctx = places.resolve(project, "demo_obby", "main")
    assert ctx.style["settings"]["boot_window_seconds"]["value"] == 15.0 and ctx.style["settings"]["boot_window_seconds"]["source"] == "playtest.yaml"


def cli(project, capsys, *argv):
    from playqa.guide_adapter import config

    code = config.run(project, hooks_mod.HOOKS, list(argv))
    return code, capsys.readouterr().out


def test_export_skill_dry_run_states_scope_version_and_limits(project, capsys, tmp_path):
    code, text = cli(project, capsys, "export-skill", "--scope", "demo_mine", "--place", "main", "--out", str(tmp_path / "s"))
    res = json.loads(text)
    md = res["files"]["studio-playtest-qa/SKILL.md"]
    assert code == 0 and res["problems"] == [] and res["dry_run"] is True and not (tmp_path / "s").exists() and res["scope"] == "demo_mine/main"
    assert "Knowledge version 0" in md and "`explain_failure`" in md and "never run against real Studio" in md and "placeholder" in md and "schema_unverified" in md
    assert "Nothing learned yet" in md and res["skill_lines"] <= 70


def test_export_skill_scopes_corrections_and_parameter_values_by_place(project, capsys):
    for pid, note in (("demo_mine", "mine note about the boot window"), ("demo_tycoon", "tycoon note about the economy cap")):
        rid = call(project, "record_run", request=f"playtest {pid}", project_id=pid, place_id="main", dry_run=False)["run_id"]
        call(project, "record_decision", run_id=rid, decision="revise", reason="felt wrong", corrections=[{"dimension": "threshold", "note": note}], project_id=pid, place_id="main", dry_run=False)
    LP.store(project).update("setting.boot_window_seconds", 15.0, SC_MAIN, approved_by="amy", reason="r")
    mine = json.loads(cli(project, capsys, "export-skill", "--scope", "demo_mine", "--place", "main")[1])
    tyc = json.loads(cli(project, capsys, "export-skill", "--scope", "demo_tycoon", "--place", "main")[1])
    assert "mine note" in mine["files"]["studio-playtest-qa/SKILL.md"] and "tycoon note" not in mine["files"]["studio-playtest-qa/SKILL.md"]
    assert "tycoon note" in tyc["files"]["studio-playtest-qa/SKILL.md"] and "mine note" not in tyc["files"]["studio-playtest-qa/SKILL.md"]
    assert "setting.boot_window_seconds=15" in mine["files"]["studio-playtest-qa/SKILL.md"]
    assert "| setting.boot_window_seconds | 10" in tyc["files"]["studio-playtest-qa/references/parameters.md"]


def test_export_skill_refuses_unregistered_projects_and_places_and_writes_a_valid_folder(project, capsys, tmp_path):
    from playqa.guide_adapter import config

    assert config.run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "nope"]) == 2
    assert "unknown project_id 'nope'" in capsys.readouterr().err
    assert config.run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "demo_mine", "--place", "nope"]) == 2
    assert "does not belong to project" in capsys.readouterr().err
    out = tmp_path / "skills"
    code, _ = cli(project, capsys, "export-skill", "--scope", "demo_mine", "--out", str(out), "--write")
    assert code == 0 and agentfiles.validate_skill(out / "studio-playtest-qa") == []
    assert json.loads(cli(project, capsys, "export-skill")[1])["scope"] == "global"


def test_the_improvement_loop_modules_are_reachable_from_the_adapter(project):
    from playqa import guide_adapter as g

    for name in ("observe", "propose", "gate", "promote", "params", "skillgen"):
        assert hasattr(g, name)
    call(project, "record_run", request="boot check", **MAIN, dry_run=False, tools=["explain_failure"])
    rows = g.observe.RunLog(project.workspace / "learning" / "observations.jsonl").rows(kind="run")
    assert len(rows) == 1 and rows[0]["project_id"] == "demo_mine" and rows[0]["tools"] == ["explain_failure"]
