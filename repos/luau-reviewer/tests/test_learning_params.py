"""The reviewer's tunable parameters on guide-core's learning layer: same defaults as the config files, no change to results, export-skill."""

import json
from pathlib import Path

import pytest
import yaml

from guide_core import agentfiles
from guide_core.params import LockedParameter, StepTooLarge
from guide_core.scope import Scope
from luaurev import hooks as hooks_mod
from luaurev import learning_params as LP
from luaurev.domain.review import get_rules, review_target
from luaurev.guide_adapter import config, feedback as fb

ROOT = Path(__file__).resolve().parents[1]
SBX, OBBY = "example-sandbox", "example-obby"

DAT005_FILE = """--!strict
local DataStoreService = game:GetService("DataStoreService")
local store = DataStoreService:GetDataStore("PlayerData")
while true do
	task.wait(31)
	pcall(function()
		store:UpdateAsync("k", function(v)
			return v
		end)
	end)
end
"""


def ids(rep):
    return sorted(f["rule_id"] for f in rep["findings"] + rep["questions"])


def test_registered_defaults_are_the_numbers_already_in_the_config_files(project):
    specs = {s.name: s for s in LP.param_specs(project.root)}
    assert len(specs) == 51  # 41 rule confidences + 2 numeric rule params + 3 learning settings + 5 locked limits (counted by hand from the YAML)
    assert specs["rule.DAT005.params.min_seconds"].default == 30 and specs["rule.DAT005.params.min_seconds"].verify_against_current_docs
    assert specs["rule.API007.params.max_per_file"].default == 5
    assert (specs["learning.similarity_threshold"].default, specs["learning.max_confidence_steps"].default, specs["learning.ngram"].default) == (0.6, 2, 3)
    assert specs["rule.SEC001.confidence"].default == "medium" and specs["rule.PERF001.confidence"].default == "high"
    raw = {}
    for f in (ROOT / "rules").glob("[!_]*.yaml"):
        for r in yaml.safe_load(f.read_text())["rules"]:
            raw[r["id"]] = r["confidence"]
    assert len(raw) == 41 and all(specs[f"rule.{i}.confidence"].default == c for i, c in raw.items())  # every rule confidence matches its YAML value


def test_safety_limits_are_registered_as_locked(project):
    specs = {s.name: s for s in LP.param_specs(project.root)}
    locked = sorted(n for n, s in specs.items() if s.locked)
    assert locked == ["limits.backend_batch_size", "limits.backend_timeout_seconds", "limits.max_file_bytes", "limits.max_files", "limits.max_findings_returned"]
    assert specs["limits.max_file_bytes"].default == 1048576 and specs["limits.max_files"].default == 300
    ps = LP.store(project)
    with pytest.raises(LockedParameter):
        ps.update("limits.max_files", 310, Scope(SBX), approved_by="amy", reason="r")


def test_nothing_learned_means_identical_objects(project):
    rules, settings = get_rules(project)
    r2, s2 = LP.apply_params(rules, settings, LP.store(project), Scope(SBX))
    assert r2 is rules and s2 is settings
    ps = LP.store(project)
    ps.update("rule.SEC001.confidence", "high", Scope(OBBY), approved_by="amy", reason="r")  # a value for ANOTHER project
    r3, s3 = LP.apply_params(rules, settings, ps, Scope(SBX))
    assert r3 is rules and s3 is settings


def test_a_learned_value_changes_only_its_own_project_and_not_the_cached_rules(project, tmp_path):
    rules, settings = get_rules(project)
    f = tmp_path / "dat.server.luau"
    f.write_text(DAT005_FILE)
    assert "DAT005" not in ids(review_target(project, str(f), project_id=SBX, backends="none"))  # waits 31 s: above the shipped 30 s threshold
    ps = LP.store(project)
    v = ps.update("rule.DAT005.params.min_seconds", 33, Scope(SBX), approved_by="amy", reason="saves every 31 s were too frequent for this game", evidence=["run-1"])
    assert v["version"] == 1
    assert "DAT005" in ids(review_target(project, str(f), project_id=SBX, backends="none"))
    assert "DAT005" not in ids(review_target(project, str(f), project_id="example-tycoon", backends="none"))
    assert rules["DAT005"].params["min_seconds"] == 30 and get_rules(project)[0]["DAT005"].params["min_seconds"] == 30  # the shared cached objects were not mutated
    ps.rollback("rule.DAT005.params.min_seconds", Scope(SBX), approved_by="amy", reason="back")
    assert "DAT005" not in ids(review_target(project, str(f), project_id=SBX, backends="none"))  # exactly the old behaviour again


def test_learned_confidence_and_learning_settings_are_applied(project):
    rules, settings = get_rules(project)
    ps = LP.store(project)
    with pytest.raises(StepTooLarge):
        ps.update("rule.PERF001.confidence", "low", Scope(SBX), approved_by="amy", reason="r")  # high -> low is two positions
    ps.update("rule.PERF001.confidence", "medium", Scope(SBX), approved_by="amy", reason="r")
    ps.update("learning.similarity_threshold", 0.65, Scope(SBX), approved_by="amy", reason="r")
    r2, s2 = LP.apply_params(rules, settings, ps, Scope(SBX))
    assert r2["PERF001"].confidence == "medium" and rules["PERF001"].confidence == "high" and r2["SEC001"] is rules["SEC001"]
    assert s2["learning"]["similarity_threshold"] == 0.65 and settings["learning"]["similarity_threshold"] == 0.6 and s2["limits"] == settings["limits"]


def run_cli(project, capsys, *argv):
    code = config.run(project, hooks_mod.HOOKS, list(argv))
    return code, capsys.readouterr().out


def test_export_skill_dry_run_states_version_date_scope_and_writes_nothing(project, capsys, tmp_path):
    out = tmp_path / "skills"
    code, text = run_cli(project, capsys, "export-skill", "--out", str(out))
    res = json.loads(text)
    md = res["files"]["luau-reviewer/SKILL.md"]
    assert code == 0 and res["problems"] == [] and res["dry_run"] is True and not out.exists()
    assert "Knowledge version 0" in md and "scope **global**" in md and "`review_file`" in md and "NOT verified: never run against the real luau-analyze" in md
    assert "Nothing learned yet" in md and res["skill_lines"] <= 70


def test_export_skill_reports_learned_values_and_only_this_projects_corrections(project, capsys, tmp_path):
    from luaurev.tools import make_tools

    tools = {t.name: t for t in make_tools(project)}
    f = tmp_path / "x.server.luau"
    f.write_text("wait(1)\n")
    tools["mark_false_positive"].fn(rule_id="API001", project_id=OBBY, reason="intentional legacy wait in this prop script", code="wait(1)\nwait(2)", dry_run=False)
    tools["mark_false_positive"].fn(rule_id="API001", project_id=SBX, reason="other project's private reason here", code="wait(3)\nwait(4)", dry_run=False)
    LP.store(project).update("rule.DAT005.params.min_seconds", 33, Scope(OBBY), approved_by="amy", reason="r")
    obby = json.loads(run_cli(project, capsys, "export-skill", "--scope", OBBY)[1])
    glob = json.loads(run_cli(project, capsys, "export-skill")[1])
    sbx = json.loads(run_cli(project, capsys, "export-skill", "--scope", SBX)[1])
    assert "intentional legacy wait" in obby["files"]["luau-reviewer/SKILL.md"] and "private reason" not in obby["files"]["luau-reviewer/SKILL.md"]
    assert "private reason" in sbx["files"]["luau-reviewer/SKILL.md"] and "intentional legacy" not in sbx["files"]["luau-reviewer/SKILL.md"]
    assert "intentional legacy" not in json.dumps(glob) and "private reason" not in json.dumps(glob)
    assert "rule.DAT005.params.min_seconds=33 (v1, project)" in obby["files"]["luau-reviewer/SKILL.md"]
    assert "| rule.DAT005.params.min_seconds | 33 | project | 1 |" in obby["files"]["luau-reviewer/references/parameters.md"]
    assert "| rule.DAT005.params.min_seconds | 30 | default |" in sbx["files"]["luau-reviewer/references/parameters.md"]  # the other project still has the default


def test_export_skill_write_produces_a_valid_skill_folder(project, capsys, tmp_path):
    out = tmp_path / "skills"
    code, text = run_cli(project, capsys, "export-skill", "--scope", SBX, "--out", str(out), "--write")
    assert code == 0 and agentfiles.validate_skill(out / "luau-reviewer") == []
    assert sorted(p.name for p in (out / "luau-reviewer" / "references").iterdir()) == ["corrections.md", "knowledge.md", "parameters.md"]
    code, _ = run_cli(project, capsys, "export-skill", "--scope", "no-such-project")
    assert code in (1, 2)  # an unregistered project is refused, not guessed
