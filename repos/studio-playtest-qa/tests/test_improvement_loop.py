"""The repository on guide-core's improvement loop: observe -> propose -> gate (this repository's evals) -> approve -> promote -> params in force. Nothing applies itself."""

from pathlib import Path

import pytest

from playqa import hooks as hooks_mod
from playqa import learning_params as LP
from playqa.domain import places
from playqa.guide_adapter import evals, gate, observe, promote, propose, scope as S

ROOT = Path(__file__).resolve().parents[1]
SC = S.Scope("demo_mine", "main")


def solver_for(project):
    def make(prop):
        trial = None if prop is None else [(prop["change"]["param"], prop["change"]["after"], SC)]
        return hooks_mod.eval_solver(project, trial=trial)

    return make


def tasks(prefixes):
    return [t for t in evals.load_tasks(ROOT / "evals" / "tasks") if t["id"].startswith(prefixes)]


def log_overrides(project, target, direction, n=3):
    log = observe.RunLog(project.workspace / "learning" / "observations.jsonl")
    for i in range(n):
        log.log_run(request=f"playtest {i}", scope=SC, tools=["explain_failure"], signals=[{"kind": "override", "target": f"param:{target}", "direction": direction}], considered=[f"param:{target}"])
    return log


def test_a_repeated_override_becomes_a_proposal_that_passes_the_gate_and_is_promoted_by_a_person(project):
    log = log_overrides(project, "threshold.perf_part_count_increase_pct.max", "up")
    patterns = propose.find_patterns(log.rows(kind="run"), min_runs=3, min_rate=0.5)
    store = LP.store(project)
    made = propose.make_proposals(patterns, store, SC)
    assert made["skipped"] == [] and len(made["proposals"]) == 1
    prop = made["proposals"][0]
    assert (prop["change"]["before"], prop["change"]["after"], prop["change"]["step"]) == (10.0, 15.0, 5.0)  # one bounded step, from the registered range
    assert places.resolve(project, "demo_mine", "main").style["ranges"]["perf_part_count_increase_pct"]["max"] == 10.0  # proposing changes nothing
    ps = propose.ProposalStore(project.workspace / "learning" / "proposals.jsonl")
    ps.add(prop)
    suites = {"self_written": tasks(("perf-", "agg-", "economy-style"))}
    res = gate.gate_and_record(ps, prop["id"], suites, solver_for(project), hooks_mod.eval_checks(project), root=ROOT)
    assert res.verdict == "pass" and res.cases_run >= 15 and not res.regressions
    # nothing changed yet: only an explicit, confirmed approval by a person promotes it
    assert places.resolve(project, "demo_mine", "main").style["ranges"]["perf_part_count_increase_pct"]["max"] == 10.0
    with pytest.raises(promote.PromotionError):
        promote.promote_proposal(ps, prop["id"], params=store, knowledge=LP.knowledge(project), approved_by="model", confirm=True)
    with pytest.raises(promote.PromotionError):
        promote.promote_proposal(ps, prop["id"], params=store, knowledge=LP.knowledge(project), approved_by="amy", confirm=False)
    out = promote.promote_proposal(ps, prop["id"], params=store, knowledge=LP.knowledge(project), approved_by="amy", confirm=True)
    assert out["status"] == "promoted" and out["param_version"] == 1 and out["provenance"]["place_id"] == "main"
    assert places.resolve(project, "demo_mine", "main").style["ranges"]["perf_part_count_increase_pct"]["max"] == 15.0
    assert places.resolve(project, "demo_tycoon", "main").style["ranges"]["perf_part_count_increase_pct"]["max"] == 10.0
    assert LP.knowledge(project).changelog()  # the change is logged with who and why


def test_a_proposal_that_breaks_a_passing_eval_is_rejected_automatically(project):
    """Lowering N from 3 to 2 turns 'two failing runs of two with three planned' from INCONCLUSIVE into FAIL: an eval that passed now fails, so the gate rejects it."""
    log_overrides(project, "setting.flaky_repeats", "down")
    store = LP.store(project)
    made = propose.make_proposals(propose.find_patterns(log_rows(project), min_runs=3, min_rate=0.5), store, SC)
    prop = made["proposals"][0]
    assert (prop["change"]["before"], prop["change"]["after"]) == (3, 2)
    ps = propose.ProposalStore(project.workspace / "learning" / "proposals.jsonl")
    ps.add(prop)
    res = gate.gate_and_record(ps, prop["id"], {"self_written": tasks(("agg-", "remotes-"))}, solver_for(project), hooks_mod.eval_checks(project), root=ROOT)
    assert res.verdict == "reject"
    # every eval whose outcome or wording depends on N = 3 notices (the verdict flips, or the run count it states changes)
    assert {r["id"] for r in res.regressions} == {"agg-all-pass-with-fewer-runs-is-a-pass-with-a-note", "agg-two-of-two-with-three-planned-is-inconclusive", "remotes-clean-passes",
                                                  "remotes-erroring-handler-is-caught", "remotes-one-failed-run-of-one-is-inconclusive"}
    with pytest.raises(promote.PromotionError, match="only a proposal that passed the eval gate"):
        promote.promote_proposal(ps, prop["id"], params=store, knowledge=LP.knowledge(project), approved_by="amy", confirm=True)
    assert places.resolve(project, "demo_mine", "main").style["settings"]["flaky_repeats"]["value"] == 3


def log_rows(project):
    return observe.RunLog(project.workspace / "learning" / "observations.jsonl").rows(kind="run")


def test_a_locked_limit_never_becomes_a_proposal(project):
    log = log_overrides(project, "threshold.boot_error_lines.max", "up")
    made = propose.make_proposals(propose.find_patterns(log.rows(kind="run"), min_runs=3, min_rate=0.5), LP.store(project), SC)
    assert made["proposals"] == [] and made["skipped"][0]["reason"] == "parameter is locked"


def test_an_empty_real_suite_is_inconclusive_not_a_pass_for_the_gate(project):
    prop = {"id": "prop-x", "kind": "param", "scope": SC.to_dict(), "target": "param:setting.flaky_repeats", "change": {"op": "set", "param": "setting.flaky_repeats", "before": 3, "after": 2, "step": 1},
            "rationale": "t", "evidence": {"run_ids": [], "count": 0, "of": 0, "rate": 0, "projects": []}, "status": "proposed"}
    res = gate.run_gate(prop, {"real": []}, solver_for(project), hooks_mod.eval_checks(project), root=ROOT)
    assert res.verdict == "inconclusive"


def test_the_trial_values_never_leak_out_of_the_private_workspace(project):
    solve = hooks_mod.eval_solver(project, trial=[("setting.boot_window_seconds", 15.0, SC)])
    out = solve({"id": "t", "input": {"op": "learning", "show": ["setting.boot_window_seconds"], "steps": []}, "checks": [{"type": "equals", "path": "errors", "value": []}]})
    assert out["values"]["setting__boot_window_seconds"] == 15.0
    assert places.resolve(project, "demo_mine", "main").style["settings"]["boot_window_seconds"]["value"] == 10.0
    assert not (project.workspace / "learning" / "params.json").exists()
