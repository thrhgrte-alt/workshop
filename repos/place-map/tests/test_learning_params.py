"""The learning layer: registered parameters, export-skill, observations, precedence, and the improvement loop's entry points."""
import json
from pathlib import Path

import pytest
import yaml

from placemap import learning_params as LP
from placemap.guide_adapter import cli, observe, params as P, propose, scope as S, skillgen
from placemap.hooks import HOOKS

ROOT = Path(__file__).resolve().parents[1]


def test_with_nothing_learned_every_value_is_its_default_and_none_is_flagged_as_learned(project):
    ps = LP.store(project, "demo_roles")
    snap = ps.snapshot(S.Scope("demo_roles", "role-lab"))
    assert snap and all(v["source"] == "default" and v["value"] == v["default"] for v in snap.values())
    assert not (project.workspace / "learning" / "params.json").exists()  # reading never creates state


def test_the_documented_precedence_explicit_label_then_project_then_global_then_default(project):
    ps = LP.store(project, "demo_roles")
    name = "weight.vendor.has_interaction"
    project_scope = S.Scope("demo_roles", "role-lab")
    ps.update(name, 3.1, S.Scope.make_global(), approved_by="tester", reason="g")
    assert ps.resolve(name, project_scope).source == "global" and ps.value(name, S.Scope("demo_mine", "dive-and-mine")) == 3.1
    ps.update(name, 3.2, S.Scope("demo_roles"), approved_by="tester", reason="p")
    r = ps.resolve(name, project_scope)
    assert (r.source, r.value) == ("project", 3.2) and [o["source"] for o in r.overridden] == ["global", "default"]
    r = ps.resolve(name, project_scope, explicit=3.4)
    assert (r.source, r.value) == ("user_label", 3.4)


def test_every_registered_parameter_has_a_range_a_step_and_a_description(project):
    for s in LP.param_specs(project):
        assert s.description and s.min is not None and s.max is not None and s.max_step and s.min <= s.default <= s.max, s.name
        if s.group in ("collector", "snapshots", "features", "learning", "search", "output"):
            assert s.name.split(".")[0] in ("collector", "snapshots", "feature", "learning", "search", "output")


def test_limits_are_flagged_placeholder_and_roblox_dependent_ones_are_marked_to_verify():
    lim = yaml.safe_load((ROOT / "rules" / "limits.yaml").read_text())
    assert lim["placeholder"] is True
    for key in ("max_instances", "max_source_chars", "max_total_source_chars", "print_chunk_chars"):
        assert lim["collector"][key]["verify_against_current_docs"] is True, key
    assert "PLACEHOLDER" in (ROOT / "rules" / "limits.yaml").read_text() and "PLACEHOLDER" in (ROOT / "rules" / "roles.yaml").read_text()
    assert yaml.safe_load((ROOT / "rules" / "roles.yaml").read_text())["placeholder"] is True


def _skill_text(res: dict) -> str:
    return next(v for k, v in res["files"].items() if k.endswith("SKILL.md"))


def test_export_skill_is_a_dry_run_unless_write_and_states_its_scope(project, call, capsys, tmp_path):
    out = tmp_path / "skills"
    assert cli.run(project, HOOKS, ["export-skill", "--scope", "demo_roles", "--place", "role-lab", "--out", str(out)]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["dry_run"] is True and not out.exists() and "place-map" in _skill_text(res) and "demo_roles/role-lab" in _skill_text(res)
    assert "schema_unverified" in _skill_text(res) and "find_in_place" in _skill_text(res)
    assert cli.run(project, HOOKS, ["export-skill", "--scope", "demo_roles", "--place", "role-lab", "--out", str(out), "--write"]) == 0
    capsys.readouterr()
    skill = next(out.rglob("SKILL.md"))
    assert skill.read_text().startswith("---\nname: place-map") and len(skill.read_text().splitlines()) <= skillgen.MAX_SKILL_LINES


def test_export_skill_carries_learned_values_and_this_places_corrections(world, call, project, capsys, tmp_path):
    call("label_landmark", path="Workspace/Npcs/Carl", role="vendor", label="confirm", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    rid = call("record_run", request="where is the shop", project_id="demo_roles", place_id="role-lab")["run_id"]
    call("record_decision", run_id=rid, decision="revise", reason="Carl runs the shop", corrections=[{"dimension": "landmark", "note": "Carl sells things"}], project_id="demo_roles", place_id="role-lab")
    assert cli.run(project, HOOKS, ["export-skill", "--scope", "demo_roles", "--place", "role-lab"]) == 0
    text = _skill_text(json.loads(capsys.readouterr().out))
    assert "weight.vendor.has_interaction" in text and "3.03333" in text and "Carl sells things" in text
    assert cli.run(project, HOOKS, ["export-skill", "--scope", "demo_mine", "--place", "dive-and-mine"]) == 0
    other = _skill_text(json.loads(capsys.readouterr().out))
    assert "Carl sells things" not in other and "3.03333" not in other  # another project sees neither


def test_export_skill_refuses_an_unknown_scope(project, capsys):
    assert cli.run(project, HOOKS, ["export-skill", "--scope", "nope"]) == 2
    assert "unknown project_id" in capsys.readouterr().err


def test_a_label_leaves_observation_signals_for_the_improvement_loop(world, call, project):
    call("label_landmark", path="Workspace/Npcs/Bob", role="vendor", label="reject", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    call("label_landmark", path="Workspace/Npcs/Carl", role="vendor", label="confirm", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    rows = observe.RunLog(project.workspace / "learning" / "observations.jsonl").rows("run")
    kinds = sorted((s["kind"], s["target"], s["direction"]) for r in rows for s in r["signals"])
    assert kinds == [("false_positive", "param:band.vendor.confident", "up"), ("miss", "param:band.vendor.confident", "down")]
    assert all(r["project_id"] == "demo_roles" and r["place_id"] == "role-lab" for r in rows)


def test_proposals_come_only_from_repeated_signals_and_are_never_applied(world, call, project, capsys):
    for _ in range(3):
        call("label_landmark", path="Workspace/Npcs/Carl", role="vendor", label="confirm", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    assert cli.run(project, HOOKS, ["propose", "--project-id", "demo_roles", "--place-id", "role-lab"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["patterns"] >= 1 and out["proposals"], out
    prop = out["proposals"][0]
    assert prop["status"] == "proposed" and prop["change"]["param"] == "band.vendor.confident" and prop["change"]["after"] < prop["change"]["before"]
    assert LP.store(project, "demo_roles").value("band.vendor.confident", S.Scope("demo_roles")) == 0.75  # nothing was applied


def test_the_knowledge_version_stamps_every_change(world, call, project):
    ps = LP.store(project, "demo_roles")
    before = ps.knowledge_version()
    call("label_landmark", path="Workspace/Npcs/Carl", role="vendor", label="confirm", confirmed_by="tester", dry_run=False, project_id="demo_roles", place_id="role-lab")
    assert LP.store(project, "demo_roles").knowledge_version() == before + 3  # three weights moved, three versions


def test_eval_report_keeps_self_written_and_real_apart(project, capsys):
    assert cli.run(project, HOOKS, ["eval-report", "--label", "pytest-report"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["real"] == "0/0" and "agrees with itself" in out["note"]
    md = Path(out["markdown"]).read_text()
    assert "Self-written" in md and "0 cases" in md
    Path(out["markdown"]).unlink()
    (ROOT / "evals" / "reports" / "pytest-report.md").unlink(missing_ok=True)
