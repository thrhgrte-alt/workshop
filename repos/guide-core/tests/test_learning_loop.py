"""propose -> gate -> promote, and skill export. Expectations are hand-computed."""

import datetime as dt
import inspect
import json

import pytest

from guide_core import evals, feedback, gate, observe, promote, propose, skillgen
from guide_core.mcpkit import ToolSpec
from guide_core.params import ParamSpec, ParamStore
from guide_core.promote import KnowledgeStore, PromotionError, abstract_signature
from guide_core.propose import ProposalStore, find_patterns, make_proposals
from guide_core.scope import Scope

A, B, C, D = Scope("proj-a", "main"), Scope("proj-b", "main"), Scope("proj-c", "main"), Scope("proj-d", "main")
T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def rows(n_over, n_total, target="param:conf.SEC003", direction="down", project="proj-a"):
    out = []
    for i in range(n_total):
        sig = [{"kind": "override", "target": target, "direction": direction}] if i < n_over else []
        out.append({"run_id": f"r{i}", "project_id": project, "considered": [target], "signals": sig})
    return out


@pytest.fixture()
def store(tmp_path):
    return ParamStore(tmp_path / "p.json", [ParamSpec("conf.SEC003", 0.7, 0.0, 1.0, 0.1), ParamSpec("locked.x", 1.0, 0.0, 2.0, locked=True),
                                            ParamSpec("choice", "high", choices=("low", "medium", "high"))])


# --- propose ---------------------------------------------------------------------------------------------------------------
def test_pattern_needs_enough_runs_and_rate():
    assert find_patterns(rows(6, 8))[0]["description"] == "override of param:conf.SEC003 (down) in 6 of 8 runs"
    p = find_patterns(rows(6, 8))[0]
    assert (p["count"], p["of"], p["rate"], p["projects"]) == (6, 8, 0.75, ["proj-a"]) and p["run_ids"] == [f"r{i}" for i in range(6)]
    assert find_patterns(rows(2, 8)) == []  # fewer than min_runs
    assert find_patterns(rows(3, 10)) == []  # 3 of 10 = 0.3 < 0.5
    assert len(find_patterns(rows(3, 10), min_rate=0.3)) == 1


def test_denominator_counts_runs_where_the_target_was_in_play():
    r = rows(3, 3)
    r += [{"run_id": f"x{i}", "considered": [], "signals": []} for i in range(10)]  # runs that never involved this target
    assert find_patterns(r)[0]["of"] == 3 and find_patterns(r)[0]["rate"] == 1.0


def test_a_run_is_counted_once_per_pattern():
    r = [{"run_id": "r1", "considered": ["param:a"], "signals": [{"kind": "override", "target": "param:a", "direction": "up"}] * 5}]
    assert find_patterns(r, min_runs=1)[0]["count"] == 1


def test_opposite_directions_are_a_contradiction_not_a_proposal(store):
    mixed = [{"run_id": f"r{i}", "considered": ["param:conf.SEC003"],
              "signals": [{"kind": "override", "target": "param:conf.SEC003", "direction": "down" if i < 4 else "up"}]} for i in range(8)]
    pats = find_patterns(mixed)  # 4 of 8 pushed down, 4 of 8 pushed up: each side meets the 0.5 rate
    assert len(pats) == 2 and all(p["contradiction"] for p in pats)
    out = make_proposals(pats, store, A)
    assert out["proposals"] == [] and all("contradicting" in s["reason"] for s in out["skipped"])


def test_param_proposal_is_a_bounded_step_with_evidence_and_applies_nothing(store):
    out = make_proposals(find_patterns(rows(6, 8)), store, A)
    p = out["proposals"][0]
    assert p["status"] == "proposed" and p["kind"] == "param" and p["target"] == "param:conf.SEC003"
    assert p["change"] == {"op": "set", "param": "conf.SEC003", "before": 0.7, "after": 0.6, "step": 0.1}
    assert p["evidence"]["run_ids"] == [f"r{i}" for i in range(6)] and p["evidence"]["of"] == 8 and p["id"].startswith("prop-")
    assert store.value("conf.SEC003", A) == 0.7 and not store.path.exists()  # proposing changed nothing


def test_proposal_ids_are_deterministic(store):
    a = make_proposals(find_patterns(rows(6, 8)), store, A)["proposals"][0]["id"]
    assert a == make_proposals(find_patterns(rows(6, 8)), store, A)["proposals"][0]["id"]
    assert a != make_proposals(find_patterns(rows(6, 8)), store, B)["proposals"][0]["id"]


def test_locked_unknown_directionless_and_limit_targets_are_skipped(store):
    pats = [{"kind": "override", "target": t, "direction": d, "count": 5, "of": 5, "rate": 1.0, "run_ids": ["r"], "projects": [], "suggest": None, "contradiction": False, "description": "x"}
            for t, d in (("param:locked.x", "up"), ("param:nope", "up"), ("param:conf.SEC003", None), ("what:ever", "up"), ("rule:SEC001", "up"))]
    out = make_proposals(pats, store, A)
    reasons = {s["target"]: s["reason"] for s in out["skipped"]}
    assert out["proposals"] == [] and "locked" in reasons["param:locked.x"] and "unknown parameter" in reasons["param:nope"] and "no direction" in reasons["param:conf.SEC003"]
    assert "unknown target kind" in reasons["what:ever"] and "no structured diff" in reasons["rule:SEC001"]
    store.update("conf.SEC003", 1.0, A, approved_by="amy", reason="r") if False else None


def test_choice_param_proposal_moves_one_position(store):
    out = make_proposals(find_patterns(rows(5, 5, target="param:choice", direction="down")), store, A)
    assert out["proposals"][0]["change"]["before"] == "high" and out["proposals"][0]["change"]["after"] == "medium"


def test_rule_and_synonym_proposals_use_the_supplied_structured_diff(store):
    r = [{"run_id": f"r{i}", "considered": ["rule:SEC003"], "signals": [{"kind": "override", "target": "rule:SEC003",
          "suggest": {"file": "rules/sec.yaml", "path": "SEC003.severity", "before": "error", "after": "warning"}}]} for i in range(4)]
    r += [{"run_id": f"s{i}", "considered": ["synonym:rules/synonyms.yaml"], "signals": [{"kind": "miss", "target": "synonym:rules/synonyms.yaml",
           "suggest": {"file": "rules/synonyms.yaml", "path": "vendor", "add": ["merchant"]}}]} for i in range(4)]
    props = {p["kind"]: p for p in make_proposals(find_patterns(r), store, A)["proposals"]}
    assert props["rule"]["change"] == {"op": "set", "file": "rules/sec.yaml", "path": "SEC003.severity", "before": "error", "after": "warning"}
    assert props["synonym"]["change"] == {"op": "add", "file": "rules/synonyms.yaml", "path": "vendor", "add": ["merchant"]}


def test_propose_module_has_no_way_to_apply_anything():
    public = [n for n in dir(propose) if not n.startswith("_")]
    assert not [n for n in public if any(w in n.lower() for w in ("apply", "promote", "commit", "write_param", "update_param"))]
    assert not [n for n in dir(ProposalStore) if "apply" in n.lower()]
    src = inspect.getsource(propose)
    assert ".update(" not in src.replace("dict.update(", "") and "ParamStore.update" not in src and "rollback(" not in src


def test_proposal_store_is_append_only_and_idempotent(tmp_path, store):
    ps = ProposalStore(tmp_path / "prop.jsonl")
    prop = make_proposals(find_patterns(rows(6, 8)), store, A)["proposals"][0]
    ps.add(prop)
    ps.add(prop)  # same pattern, same id: not duplicated
    assert len(ps.all()) == 1 and len((tmp_path / "prop.jsonl").read_text().splitlines()) == 1
    before = (tmp_path / "prop.jsonl").read_bytes()
    ps.set_status(prop["id"], "gate_passed", gate={"verdict": "pass"})
    assert (tmp_path / "prop.jsonl").read_bytes().startswith(before)
    got = ps.get(prop["id"])
    assert got["status"] == "gate_passed" and got["history"][0]["gate"]["verdict"] == "pass" and ps.all("proposed") == [] and len(ps.all("gate_passed")) == 1
    with pytest.raises(ValueError):
        ps.set_status(prop["id"], "bogus")
    with pytest.raises(ValueError, match="unknown proposal"):
        ps.set_status("prop-nope", "approved")


# --- gate --------------------------------------------------------------------------------------------------------------------
CHECKS = {}


def suites():
    return {"self_written": [{"id": "high-conf-ok", "input": {"conf": 0.65}, "checks": [{"type": "equals", "path": "above_threshold", "value": True}]},
                             {"id": "low-conf-ok", "input": {"conf": 0.2}, "checks": [{"type": "equals", "path": "above_threshold", "value": False}]}],
            "real": []}


def solver_for_threshold(store):
    def solver_for(prop):
        view = store.view(A, {} if prop is None else {prop["change"]["param"]: prop["change"]["after"]})
        return lambda t: {"above_threshold": t["input"]["conf"] >= view.value("conf.SEC003")}
    return solver_for


def a_proposal(store, direction="down"):
    return make_proposals(find_patterns(rows(6, 8, direction=direction)), store, A)["proposals"][0]


def test_gate_rejects_a_proposal_that_breaks_a_passing_case_and_lists_it(store, tmp_path):
    prop = a_proposal(store, "up")  # 0.7 -> 0.8: the 0.65... wait, 0.65 < 0.7 so baseline expects True? see below
    # baseline threshold 0.7: conf 0.65 >= 0.7 is False, so 'high-conf-ok' FAILS at baseline; it cannot regress
    res = gate.run_gate(prop, suites(), solver_for_threshold(store), CHECKS, root=tmp_path)
    assert res.regressions == []  # a case that already failed is not a regression
    # make the baseline pass: raise the case's conf to 0.72 (>= 0.7) and ask for a step UP to 0.8: now it flips
    s = suites()
    s["self_written"][0]["input"]["conf"] = 0.72
    res = gate.run_gate(prop, s, solver_for_threshold(store), CHECKS, root=tmp_path)
    assert res.verdict == "reject" and [r["id"] for r in res.regressions] == ["high-conf-ok"] and res.regressions[0]["suite"] == "self_written"
    assert "above_threshold" in res.regressions[0]["messages"][0] and "rejected automatically" in res.notes[-1]
    assert res.suites["self_written"] == {"total": 2, "baseline_passed": 2, "candidate_passed": 1}


def test_gate_passes_when_nothing_that_passed_now_fails(store, tmp_path):
    prop = a_proposal(store, "down")  # 0.7 -> 0.6: conf 0.65 now crosses the line; set it to expect the new behaviour? it flips False->True
    s = suites()
    s["self_written"][0]["input"]["conf"] = 0.9  # >= both thresholds
    res = gate.run_gate(prop, s, solver_for_threshold(store), CHECKS, root=tmp_path)
    assert res.verdict == "pass" and res.passed and res.regressions == [] and res.cases_run == 2
    assert any("no real examples" in n for n in res.notes)  # the empty real suite is stated, not hidden


def test_gate_records_improvements(store, tmp_path):
    prop = a_proposal(store, "down")
    res = gate.run_gate(prop, suites(), solver_for_threshold(store), CHECKS, root=tmp_path)  # conf .65: baseline False (fails), candidate .6 -> True (passes)
    assert res.verdict == "pass" and res.improvements == [{"suite": "self_written", "id": "high-conf-ok"}]


def test_gate_with_nothing_to_run_is_inconclusive_not_a_pass(store, tmp_path):
    res = gate.run_gate(a_proposal(store), {"self_written": [], "real": [], "corrections": []}, solver_for_threshold(store), CHECKS, root=tmp_path)
    assert res.verdict == "inconclusive" and not res.passed and res.cases_run == 0 and "not a pass" in res.notes[-1]


def test_candidate_that_crashes_counts_as_a_regression(store, tmp_path):
    def solver_for(prop):
        def base(t):
            return {"above_threshold": t["input"]["conf"] >= 0.7}

        def cand(t):
            raise RuntimeError("candidate exploded")

        return base if prop is None else cand

    s = suites()
    s["self_written"][0]["input"]["conf"] = 0.9
    res = gate.run_gate(a_proposal(store), s, solver_for, CHECKS, root=tmp_path)
    assert res.verdict == "reject" and "exploded" in res.regressions[0]["messages"][0]


def test_gate_runs_real_and_correction_suites_too(store, tmp_path):
    prop = a_proposal(store, "up")
    s = suites()
    s["self_written"] = []
    s["real"] = [{"id": "real-1", "input": {"conf": 0.75}, "checks": [{"type": "equals", "path": "above_threshold", "value": True}]}]
    res = gate.run_gate(prop, s, solver_for_threshold(store), CHECKS, root=tmp_path)  # step up 0.7->0.8 flips 0.75
    assert res.verdict == "reject" and res.regressions[0]["suite"] == "real"


def test_every_past_accepted_correction_becomes_a_regression_case(project, store, tmp_path):
    rid = feedback.record_run(project, request="0.75 is fine", scope=A)
    feedback.record_decision(project, rid, "accept", corrections=[{"dimension": "conf", "note": "0.75 passes"}],
                             regression_case={"input": {"conf": 0.75}, "checks": [{"type": "equals", "path": "above_threshold", "value": True}]})
    rid2 = feedback.record_run(project, request="note only", scope=A)
    feedback.record_decision(project, rid2, "accept", corrections=[{"dimension": "x", "note": "no executable case"}])
    tasks, not_exec = gate.correction_tasks(project, A)
    assert [t["id"] for t in tasks] == [f"correction:{rid}:0"] and not_exec == 1
    res = gate.run_gate(a_proposal(store, "up"), {"corrections": tasks}, solver_for_threshold(store), CHECKS, root=tmp_path)
    assert res.verdict == "reject" and res.regressions[0]["id"].startswith("correction:")


def test_gate_and_record_sets_the_proposal_status(store, tmp_path):
    ps = ProposalStore(tmp_path / "prop.jsonl")
    good, bad = a_proposal(store, "down"), a_proposal(store, "up")
    ps.add(good)
    ps.add(bad)
    s = suites()
    s["self_written"][0]["input"]["conf"] = 0.9
    assert gate.gate_and_record(ps, good["id"], s, solver_for_threshold(store), CHECKS, root=tmp_path).verdict == "pass"
    s["self_written"][0]["input"]["conf"] = 0.72
    assert gate.gate_and_record(ps, bad["id"], s, solver_for_threshold(store), CHECKS, root=tmp_path).verdict == "reject"
    assert ps.get(good["id"])["status"] == "gate_passed" and ps.get(bad["id"])["status"] == "gate_rejected"
    assert ps.get(bad["id"])["history"][0]["gate"]["regressions"][0]["id"] == "high-conf-ok"
    with pytest.raises(ValueError):
        gate.gate_and_record(ps, "prop-nope", s, solver_for_threshold(store), CHECKS, root=tmp_path)


# --- promote_proposal -----------------------------------------------------------------------------------------------------------
@pytest.fixture()
def world(tmp_path, store):
    return {"ps": ProposalStore(tmp_path / "prop.jsonl"), "kb": KnowledgeStore(tmp_path / "kb"), "store": store}


def passed_proposal(world, direction="down"):
    prop = a_proposal(world["store"], direction)
    world["ps"].add(prop)
    world["ps"].set_status(prop["id"], "gate_passed", gate={"verdict": "pass"})
    return prop


def test_promotion_applies_one_bounded_step_with_provenance_and_changelog(world):
    prop = passed_proposal(world)
    res = promote.promote_proposal(world["ps"], prop["id"], params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)
    assert res["status"] == "promoted" and res["param_version"] == 1 and world["store"].value("conf.SEC003", A) == 0.6
    ver = world["store"].history("conf.SEC003", A)[0]
    assert ver["approved_by"] == "amy" and ver["evidence"] == [f"r{i}" for i in range(6)] and "override" in ver["reason"]
    log = world["kb"].changelog()[0]
    assert "0.7 -> 0.6" in log["line"] and log["by"] == "amy" and log["provenance"]["proposal_id"] == prop["id"] and log["provenance"]["project_id"] == "proj-a"
    assert world["ps"].get(prop["id"])["status"] == "promoted"
    assert world["store"].value("conf.SEC003", B) == 0.7  # other projects are untouched


def test_promotion_refuses_without_approval_confirm_or_a_passed_gate(world):
    prop = passed_proposal(world)
    for kw, msg in (({"approved_by": "auto", "confirm": True}, "approved_by"), ({"approved_by": "amy", "confirm": False}, "confirm=True"), ({"approved_by": "", "confirm": True}, "approved_by")):
        with pytest.raises(PromotionError, match=msg):
            promote.promote_proposal(world["ps"], prop["id"], params=world["store"], knowledge=world["kb"], **kw)
    assert world["store"].value("conf.SEC003", A) == 0.7 and world["kb"].changelog() == []
    other = a_proposal(world["store"], "up")
    world["ps"].add(other)
    with pytest.raises(PromotionError, match="'proposed'"):
        promote.promote_proposal(world["ps"], other["id"], params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)
    world["ps"].set_status(other["id"], "gate_rejected", gate={})
    with pytest.raises(PromotionError, match="rejected automatically"):
        promote.promote_proposal(world["ps"], other["id"], params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)
    with pytest.raises(PromotionError, match="unknown proposal"):
        promote.promote_proposal(world["ps"], "prop-x", params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)


def test_rule_diffs_are_recorded_for_a_person_to_apply_not_applied(world):
    prop = {"id": "prop-rule", "kind": "rule", "scope": A.to_dict(), "target": "rule:SEC003", "rationale": "overridden 4 of 4", "status": "proposed",
            "change": {"op": "set", "file": "rules/sec.yaml", "path": "SEC003.severity", "before": "error", "after": "warning"},
            "evidence": {"run_ids": ["r1"], "count": 4, "of": 4, "rate": 1.0, "projects": ["proj-a"]}}
    world["ps"].add(prop)
    world["ps"].set_status("prop-rule", "gate_passed", gate={})
    res = promote.promote_proposal(world["ps"], "prop-rule", params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)
    assert res["status"] == "approved" and res["applied"] is None and "apply this diff yourself" in res["note"]
    assert world["ps"].get("prop-rule")["status"] == "approved" and "not applied by guide-core" in world["kb"].changelog()[0]["line"]


def test_global_scope_proposals_cannot_be_promoted_here(world):
    prop = {"id": "prop-g", "kind": "param", "scope": Scope.make_global().to_dict(), "target": "param:conf.SEC003", "rationale": "r", "status": "gate_passed",
            "change": {"param": "conf.SEC003", "before": 0.7, "after": 0.6}, "evidence": {"run_ids": [], "count": 0, "of": 0, "rate": 0, "projects": []}}
    world["ps"].add({**prop, "status": "proposed"})
    world["ps"].set_status("prop-g", "gate_passed", gate={})
    with pytest.raises(PromotionError, match="promote_global"):
        promote.promote_proposal(world["ps"], "prop-g", params=world["store"], knowledge=world["kb"], approved_by="amy", confirm=True)


def test_reject_keeps_the_proposal_with_the_reason(world):
    prop = passed_proposal(world)
    promote.reject_proposal(world["ps"], prop["id"], rejected_by="amy", reason="no, at /home/amy/x the rule is right")
    got = world["ps"].get(prop["id"])
    assert got["status"] == "rejected" and "amy/x" not in json.dumps(got["history"]) and len(world["ps"].all()) == 1


# --- KnowledgeStore -----------------------------------------------------------------------------------------------------------------
SIG = abstract_signature("purchase remote", "Has_Interaction")


def learn(kb, pid, run="r1", now=T0):
    return kb.learn(SIG, text="interaction object plus purchase remote marks a vendor", project_id=pid, run_ids=[run], approved_by="amy", now=now)


def test_signature_is_canonical():
    assert SIG == "has_interaction+purchase_remote" and abstract_signature("b", "a", "A") == "a+b"
    with pytest.raises(PromotionError):
        abstract_signature("", " ")


def test_item_confirmed_in_two_projects_is_not_offered(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    learn(kb, "proj-a")
    learn(kb, "proj-b")
    assert kb.global_candidates() == []
    rep = kb.candidate_report()[0]
    assert rep["projects"] == ["proj-a", "proj-b"] and not rep["eligible"] and "confirmed in 2 distinct project(s), needs 3" in rep["blocked_by"][0]
    with pytest.raises(PromotionError, match="cannot be promoted to global"):
        kb.promote_global(SIG, approved_by="amy", confirm=True)  # even a person's explicit yes cannot skip the evidence rule


def test_repeated_confirmation_in_one_project_does_not_count_as_distinct(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    for i in range(5):
        learn(kb, "proj-a", run=f"r{i}")
    assert kb.get(SIG)["confirmed_in"]["proj-a"]["count"] == 5 and kb.global_candidates() == []


def test_item_confirmed_in_three_projects_is_offered_and_needs_a_person(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    for pid in ("proj-a", "proj-b", "proj-c"):
        learn(kb, pid)
    assert [c["signature"] for c in kb.global_candidates()] == [SIG] and kb.get(SIG)["scope"] == "project"  # offered, not promoted
    for bad in ("auto", "", "system"):
        with pytest.raises(PromotionError, match="approved_by"):
            kb.promote_global(SIG, approved_by=bad, confirm=True)
    with pytest.raises(PromotionError, match="confirm=True"):
        kb.promote_global(SIG, approved_by="amy")
    assert kb.get(SIG)["scope"] == "project"
    it = kb.promote_global(SIG, approved_by="amy", confirm=True, now=T0)
    assert it["scope"] == "global" and it["provenance"][-1]["by"] == "amy" and it["provenance"][-1]["projects"] == ["proj-a", "proj-b", "proj-c"]
    assert "promoted" in kb.changelog()[-1]["line"] and kb.global_candidates() == []


def test_k_is_configurable(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb", k=2)
    learn(kb, "proj-a")
    learn(kb, "proj-b")
    assert len(kb.global_candidates()) == 1
    with pytest.raises(ValueError):
        KnowledgeStore(tmp_path / "x", k=0)


def test_contradiction_blocks_promotion_and_is_surfaced(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    for pid in ("proj-a", "proj-b", "proj-c"):
        learn(kb, pid)
    kb.contradict(SIG, "proj-d", note="did not hold in /home/x/game", approved_by="amy")
    assert kb.global_candidates() == [] and "contradicting evidence in proj-d" in kb.candidate_report()[0]["blocked_by"][0]
    c = kb.contradictions()
    assert c[0]["signature"] == SIG and c[0]["contradicted_in"]["proj-d"][0]["note"] == "did not hold in <path>"
    assert len(c[0]["confirmed_in"]) == 3  # the confirmations are kept; the two sides are shown side by side, not merged


def test_non_transferable_items_are_never_offered_again(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    for pid in ("proj-a", "proj-b", "proj-c"):
        learn(kb, pid)
    kb.mark_non_transferable(SIG, reason="held in obbies, failed in tycoons", projects=["proj-d"], approved_by="amy")
    assert kb.global_candidates() == [] and "non-transferable" in kb.candidate_report()[0]["blocked_by"][0]
    assert kb.non_transferable()[0]["projects"] == ["proj-d"]
    with pytest.raises(PromotionError, match="reason"):
        kb.mark_non_transferable("x", reason=" ", approved_by="amy")


def test_global_promotion_can_be_rolled_back_with_history_kept(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    for pid in ("proj-a", "proj-b", "proj-c"):
        learn(kb, pid)
    kb.promote_global(SIG, approved_by="amy", confirm=True)
    with pytest.raises(PromotionError, match="confirm=True"):
        kb.demote_global(SIG, approved_by="amy", reason="r")
    it = kb.demote_global(SIG, approved_by="amy", reason="broke tycoons", confirm=True)
    assert it["scope"] == "project" and [p["what"] for p in it["provenance"]][-2:] == ["promoted to global", "global promotion rolled back"] and it["version"] == 5  # created, 2 confirmations, promotion, rollback
    with pytest.raises(PromotionError, match="not a global item"):
        kb.demote_global(SIG, approved_by="amy", reason="again", confirm=True)


def test_staleness_drops_from_retrieval_then_archives_but_never_deletes(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb", max_age_days=100)
    learn(kb, "proj-a", now=T0)
    fresh, stale = T0 + dt.timedelta(days=50), T0 + dt.timedelta(days=101)
    assert [i["signature"] for i in kb.retrievable(Scope("proj-a"), now=fresh)] == [SIG]
    assert kb.retrievable(Scope("proj-a"), now=fresh)[0]["effective_confidence"] == 0.25  # 0.5 * (1 - 50/100)
    assert kb.retrievable(Scope("proj-a"), now=stale) == []  # drops out of retrieval ...
    assert kb.get(SIG)["status"] == "active"  # ... without writing anything
    assert kb.archive_stale(now=stale) == [kb.get(SIG)["id"]] and kb.get(SIG)["status"] == "archived"
    assert len(kb.items()) == 1 and kb.items(include_archived=False) == []  # archived, not deleted
    assert kb.archive_stale(now=stale) == []
    assert "archived" in kb.changelog()[-1]["line"]


def test_confirmation_revives_an_archived_item_and_unarchive_needs_a_person(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb", max_age_days=10)
    learn(kb, "proj-a", now=T0)
    kb.archive_stale(now=T0 + dt.timedelta(days=30))
    with pytest.raises(PromotionError, match="approved_by"):
        kb.unarchive(SIG, approved_by="auto")
    kb.unarchive(SIG, approved_by="amy", now=T0 + dt.timedelta(days=31))
    assert kb.get(SIG)["status"] == "active"
    kb.archive_stale(now=T0 + dt.timedelta(days=60))
    learn(kb, "proj-b", now=T0 + dt.timedelta(days=61))
    assert kb.get(SIG)["status"] == "active" and kb.get(SIG)["last_confirmed"].startswith("2026-03-03")


def test_archived_items_are_not_candidates(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb", max_age_days=10)
    for pid in ("proj-a", "proj-b", "proj-c"):
        learn(kb, pid, now=T0)
    kb.archive_stale(now=T0 + dt.timedelta(days=30))
    assert kb.global_candidates() == [] and "archived" in kb.candidate_report()[0]["blocked_by"][0]


def test_retrievable_respects_scope_and_global(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    learn(kb, "proj-a", now=T0)
    now = T0 + dt.timedelta(days=1)
    assert len(kb.retrievable(Scope("proj-a"), now=now)) == 1 and kb.retrievable(Scope("proj-b"), now=now) == [] and kb.retrievable(Scope.make_global(), now=now) == []
    for pid in ("proj-b", "proj-c"):
        learn(kb, pid, now=T0)
    kb.promote_global(SIG, approved_by="amy", confirm=True, now=now)
    assert len(kb.retrievable(Scope("proj-z"), now=now)) == 1 and len(kb.retrievable(Scope.make_global(), now=now)) == 1


def test_knowledge_text_is_redacted_and_versions_grow(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    it = kb.learn("sig_a", text="found at /home/alice/game/Shop.luau", project_id="proj-a", approved_by="amy")
    assert "alice" not in it["text"] and it["version"] == 1 and kb.version() == 1
    kb.confirm("sig_a", "proj-b", approved_by="amy")
    assert kb.get("sig_a")["version"] == 2 and kb.version() == 2 and "- " in kb.render_changelog()
    with pytest.raises(PromotionError, match="unknown item"):
        kb.confirm("nope", "proj-b", approved_by="amy")
    with pytest.raises(ValueError):
        kb.learn("sig_b", text="t", project_id="p", approved_by="amy", confidence=2)


def test_knowledge_events_are_append_only(tmp_path):
    kb = KnowledgeStore(tmp_path / "kb")
    learn(kb, "proj-a")
    before = (tmp_path / "kb" / "knowledge.jsonl").read_bytes()
    learn(kb, "proj-b")
    assert (tmp_path / "kb" / "knowledge.jsonl").read_bytes().startswith(before)
    assert not [n for n in dir(KnowledgeStore) if n.startswith(("delete", "remove", "purge", "drop"))]


# --- monitor (step 6) ---------------------------------------------------------------------------------------------------------------
def _log_runs(log, n, action, chars, secs, day, scope=A):
    """Write run + action rows with explicit timestamps (the log stamps 'now', which a test cannot control)."""
    for i in range(n):
        rid = f"obs-{day}-{i}"
        at = f"2026-02-{day:02d}T10:00:{i:02d}+00:00"
        log._append({"schema": 1, "kind": "run", "run_id": rid, "at": at, "request": "r", "project_id": scope.project_id, "place_id": scope.place_id, "global": False,
                     "tools": [], "findings": {}, "signals": [], "considered": [], "output_chars": chars, "seconds": secs, "extra": {}})
        log._append({"schema": 1, "kind": "action", "run_id": rid, "at": at, "action": action, "note": "", "targets": []})


def test_monitor_flags_a_worse_version_but_changes_nothing(tmp_path):
    log = observe.RunLog(tmp_path / "o.jsonl")
    _log_runs(log, 6, "accept", 1000, 0.1, day=1)
    _log_runs(log, 6, "reject", 1000, 0.1, day=10)
    before = (tmp_path / "o.jsonl").read_bytes()
    res = promote.monitor_change(log, A, "2026-02-05T00:00:00+00:00", n=6)
    assert res["status"] == "ok" and res["worse"] and res["flags"] == ["override rate 0.00 -> 1.00"] and "offer a rollback" in res["advice"]
    assert (tmp_path / "o.jsonl").read_bytes() == before  # monitoring only reads


def test_monitor_flags_growth_in_output_size_and_time(tmp_path):
    log = observe.RunLog(tmp_path / "o.jsonl")
    _log_runs(log, 5, "accept", 1000, 0.1, day=1)
    _log_runs(log, 5, "accept", 1300, 0.2, day=10)  # +30% chars (> 25%), x2 time (> 1.5x), same override rate
    res = promote.monitor_change(log, A, "2026-02-05T00:00:00+00:00", n=5)
    assert res["worse"] and res["flags"] == ["output size 1000 -> 1300 chars", "time 0.100s -> 0.200s"]


def test_monitor_is_quiet_when_nothing_got_worse(tmp_path):
    log = observe.RunLog(tmp_path / "o.jsonl")
    _log_runs(log, 5, "reject", 1000, 0.1, day=1)
    _log_runs(log, 5, "accept", 900, 0.1, day=10)
    res = promote.monitor_change(log, A, "2026-02-05T00:00:00+00:00", n=5)
    assert res["worse"] is False and res["flags"] == [] and "no regression" in res["advice"]


def test_monitor_ignores_other_projects(tmp_path):
    log = observe.RunLog(tmp_path / "o.jsonl")
    _log_runs(log, 5, "accept", 1000, 0.1, day=1)
    _log_runs(log, 5, "accept", 1000, 0.1, day=10)
    _log_runs(log, 5, "reject", 1000, 0.1, day=11, scope=B)
    assert promote.monitor_change(log, A, "2026-02-05T00:00:00+00:00", n=5)["worse"] is False


def test_monitor_needs_enough_runs(tmp_path):
    log = observe.RunLog(tmp_path / "o.jsonl")
    _log_runs(log, 2, "accept", 100, 0.1, day=1)
    assert promote.monitor_change(log, A, "0000", n=10)["status"] == "insufficient_data"


# --- skill export ---------------------------------------------------------------------------------------------------------------------
def tools():
    def one() -> dict:
        return {}

    return [ToolSpec("check_x", one, "Check an X. Returns a verdict first."), ToolSpec("write_x", one, "[rare] Write an X (dry run by default).", read_only=False)]


def export(tmp_path, project, scope, store=None, kb=None, dry_run=True, **kw):
    return skillgen.export_skill(out_dir=tmp_path / "skills", name="demo-repo", description="Use when checking X in a Roblox place.", repo="demo-repo", repo_version="0.1.0", scope=scope,
                                 tools=tools(), workflow=["Resolve the project.", "Run check_x."], verified=["fixtures"], unverified=["live Studio"], params=store, knowledge=kb,
                                 project=project, now=T0 + dt.timedelta(days=3), dry_run=dry_run, **kw)


def test_skill_is_short_valid_and_states_version_and_date(tmp_path, project, store):
    kb = KnowledgeStore(tmp_path / "kb")
    res = export(tmp_path, project, Scope.make_global(), store, kb)
    md = res["files"]["demo-repo/SKILL.md"]
    assert res["problems"] == [] and res["skill_lines"] <= skillgen.MAX_SKILL_LINES and md.startswith("---\nname: demo-repo\ndescription: ")
    assert "Knowledge version 0" in md and "generated 2026-01-04" in md and "scope **global**" in md and "guide-core 0.1.0" in md and "demo-repo 0.1.0" in md
    assert "`check_x` [read-only]" in md and "`write_x` [write, rare]" in md and "NOT verified: live Studio" in md and "Nothing learned yet" in md
    assert res["dry_run"] is True and not (tmp_path / "skills").exists()  # dry run writes nothing


def test_skill_reflects_learned_state_per_scope(tmp_path, project, store):
    kb = KnowledgeStore(tmp_path / "kb")
    store.update("conf.SEC003", 0.6, A, approved_by="amy", reason="r")
    store.update("conf.SEC003", 0.8, Scope.make_global(), approved_by="amy", reason="r")
    rid = feedback.record_run(project, request="vendors need prices", scope=A)
    feedback.record_decision(project, rid, "revise", reason="missing", corrections=[{"dimension": "vendor", "note": "always show a price"}])
    rid_b = feedback.record_run(project, request="other game", scope=B)
    feedback.record_decision(project, rid_b, "revise", reason="x", corrections=[{"dimension": "secret", "note": "belongs to proj-b only"}])
    glob = export(tmp_path, project, Scope.make_global(), store, kb)["files"]
    proj = export(tmp_path, project, A, store, kb)["files"]
    assert "conf.SEC003=0.8 (v1, global)" in glob["demo-repo/SKILL.md"] and "conf.SEC003=0.6 (v1, project)" in proj["demo-repo/SKILL.md"]
    assert "always show a price" in proj["demo-repo/SKILL.md"] and "always show a price" not in glob["demo-repo/SKILL.md"]
    assert "belongs to proj-b only" not in json.dumps(proj) and "belongs to proj-b only" not in json.dumps(glob)
    assert "| conf.SEC003 | 0.6 | project | 1 |" in proj["demo-repo/references/parameters.md"] and "| locked.x | 1.0 | default | - | 1.0 | 0.0..2.0" in proj["demo-repo/references/parameters.md"]
    assert "Knowledge version 2" in proj["demo-repo/SKILL.md"]


def test_skill_lists_learned_items_and_changelog(tmp_path, project, store):
    kb = KnowledgeStore(tmp_path / "kb")
    learn(kb, "proj-a", now=T0 + dt.timedelta(days=2))
    res = export(tmp_path, project, A, store, kb)["files"]
    assert "has_interaction+purchase_remote" in res["demo-repo/references/knowledge.md"] and "learned 'has_interaction+purchase_remote'" in res["demo-repo/references/knowledge.md"]
    assert "Knowledge version 1" in res["demo-repo/SKILL.md"]


def test_skill_write_validates_and_refuses_to_clobber_a_hand_written_skill(tmp_path, project, store):
    from guide_core import agentfiles

    res = export(tmp_path, project, Scope.make_global(), store, None, dry_run=False)
    assert agentfiles.validate_skill(tmp_path / "skills" / "demo-repo") == [] and len(res["written"]) == 4 and res["problems"] == []
    export(tmp_path, project, Scope.make_global(), store, None, dry_run=False)  # regenerating over its own output is fine
    (tmp_path / "skills" / "demo-repo" / "SKILL.md").write_text("---\nname: demo-repo\ndescription: mine\n---\nhand written\n")
    with pytest.raises(ValueError, match="not generated by export_skill"):
        export(tmp_path, project, Scope.make_global(), store, None, dry_run=False)
    assert "hand written" in (tmp_path / "skills" / "demo-repo" / "SKILL.md").read_text()
    export(tmp_path, project, Scope.make_global(), store, None, dry_run=False, force=True)
    assert "hand written" not in (tmp_path / "skills" / "demo-repo" / "SKILL.md").read_text()


def test_skill_rejects_bad_names_and_overlong_descriptions(tmp_path, project):
    base = dict(out_dir=tmp_path / "s", repo="r", scope=Scope.make_global(), project=project)
    assert skillgen.export_skill(name="Bad Name", description="d", **base)["problems"]
    assert skillgen.export_skill(name="ok-name", description="d" * 1100, **base)["problems"]
    assert skillgen.export_skill(name="ok-name", description="", **base)["problems"]


def test_skill_stays_short_with_many_tools(tmp_path, project):
    def one() -> dict:
        return {}

    many = [ToolSpec(f"tool_{i}", one, f"Tool number {i}.") for i in range(60)]
    res = skillgen.export_skill(out_dir=tmp_path, name="big", description="d", repo="r", scope=Scope.make_global(), tools=many, project=project)
    assert res["problems"] == [] and res["skill_lines"] <= skillgen.MAX_SKILL_LINES and "and 35 more" in res["files"]["big/SKILL.md"]


def test_export_command_registers_on_a_cli(tmp_path, project, store, capsys):
    import argparse

    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    kb = KnowledgeStore(tmp_path / "kb")
    skillgen.add_export_skill_command(sub, project, lambda pr, sc: dict(name="demo-repo", description="Use when X.", repo="demo-repo", tools=tools(), params=store, knowledge=kb))
    args = p.parse_args(["export-skill", "--scope", "proj-a", "--place", "main", "--out", str(tmp_path / "out")])
    assert args.handler(args, project) == 0 and not (tmp_path / "out").exists()  # dry run by default
    assert '"scope": "proj-a/main"' in capsys.readouterr().out
    args = p.parse_args(["export-skill", "--out", str(tmp_path / "out"), "--write"])
    assert args.handler(args, project) == 0 and (tmp_path / "out" / "demo-repo" / "SKILL.md").exists()


def test_skill_can_take_a_callers_own_scoped_corrections_and_brief_shrinks_the_result(tmp_path, project, store):
    own = [{"decision": "revise", "request": "r1", "reason": "x", "corrections": [{"dimension": "d", "note": "own list note"}], "at": "2026-01-01T00:00:00+00:00"}] * 8
    res = export(tmp_path, project, A, store, None, corrections=own)
    md = res["files"]["demo-repo/SKILL.md"]
    assert "own list note" in md and res["files"]["demo-repo/references/corrections.md"].count("own list note") == 5  # capped at max_corrections
    short = skillgen.brief(res)
    assert short["files"] == sorted(res["files"]) and all(isinstance(f, str) for f in short["files"])
    assert short["skill_md"] == md and "files" in short and short["scope"] == "proj-a/main" and short["problems"] == []
    written = skillgen.brief(export(tmp_path, project, A, store, None, dry_run=False))
    assert "skill_md" not in written  # nothing to show for a real write: the files are on disk


def test_export_command_uses_the_repositorys_resolver_to_refuse_unknown_projects(tmp_path, project, store):
    import argparse

    from guide_core.scope import ProjectRegistry, ScopeError

    reg = ProjectRegistry.from_dict({"projects": [{"project_id": "proj-a", "places": [{"place_id": "main"}]}]})
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command")
    skillgen.add_export_skill_command(sub, project, lambda pr, sc: dict(name="demo-repo", description="Use when X.", repo="demo-repo", params=store),
                                      resolve=lambda pr, name, place: reg.resolve(name, place))
    ok = p.parse_args(["export-skill", "--scope", "proj-a", "--place", "main"])
    assert ok.handler(ok, project) == 0
    bad = p.parse_args(["export-skill", "--scope", "proj-zzz"])
    with pytest.raises(ScopeError, match="unknown project_id"):
        bad.handler(bad, project)
