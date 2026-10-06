"""Feedback, curation, evals, safety, style, rubric and skills."""

import json

import pytest

from conceptai.core import agentfiles, evals, feedback as fb
from conceptai.core.manifest import LibraryStore
from conceptai.core.rubric import score_rubric, validate_rubric
from conceptai.core.safety import PathNotAllowed, Plan, Versioner, resolve_inside
from conceptai.core.style import check_ranges, load_style, style_brief, validate_style


# --- feedback -------------------------------------------------------------------
def _run(project):
    return fb.record_run(project, request="make a chunky stone wall", retrieved=["stone-wall-a"],
                         outputs=["out/wall.sbs"], preview="out/wall.png")


def test_decision_needs_reason_when_not_accepting(project):
    run_id = _run(project)
    with pytest.raises(ValueError):
        fb.record_decision(project, run_id, "reject")
    with pytest.raises(ValueError):
        fb.record_decision(project, "run-nope", "accept")


def test_decision_validates_dimensions(project):
    run_id = _run(project)
    with pytest.raises(ValueError, match="unknown correction dimension"):
        fb.record_decision(project, run_id, "revise", corrections=[{"dimension": "vibes", "note": "more"}],
                           allowed_dimensions=["edge_wear"])


def test_corrections_are_retrievable_for_similar_future_requests(project):
    run_id = _run(project)
    fb.record_decision(project, run_id, "revise", reason="bevels too sharp",
                       corrections=[{"dimension": "edge_wear", "note": "round the bevels more on stone walls"}])
    other = fb.record_run(project, request="wooden crate with metal bands")
    fb.record_decision(project, other, "revise", reason="too glossy",
                       corrections=[{"dimension": "palette", "note": "less saturated metal"}])
    found = fb.corrections_for(project, "new stone wall variation")
    assert found and found[0]["run_id"] == run_id
    assert fb.corrections_for(project, "completely unrelated zebra") == []


def test_promote_without_confirm_makes_candidate_not_searchable(project):
    run_id = _run(project)
    fb.record_decision(project, run_id, "accept", rating=5)
    asset = fb.promote_run(project, run_id, kind="material", title="Wall result", description="good wall",
                           tags=["stone"], path="out/wall.sbs")
    assert asset["status"] == "candidate"
    from conceptai.core import retrieval
    assert "wall-result" not in " ".join(h["id"] for h in retrieval.search(LibraryStore(project), "wall result")["positive"])


def test_confirmed_promotion_requires_accept_and_becomes_curated(project):
    run_id = _run(project)
    with pytest.raises(ValueError, match="no 'accept'"):
        fb.promote_run(project, run_id, kind="material", title="Wall result", description="d", tags=["stone"],
                       path="p", confirm=True)
    fb.record_decision(project, run_id, "accept", rating=4)
    asset = fb.promote_run(project, run_id, kind="material", title="Wall result", description="good wall",
                           tags=["stone"], path="out/wall.sbs", confirm=True)
    assert asset["status"] == "curated" and asset["rating"] == 4
    assert asset["provenance"]["origin"] == "ai-generated"


# --- evals ------------------------------------------------------------------------
def _tasks(tmp_path):
    d = tmp_path / "tasks"
    d.mkdir()
    (d / "t.yaml").write_text(
        "- id: t1\n  checks:\n    - {type: equals, path: a, value: 1}\n    - {type: in_range, path: b, min: 0, max: 5}\n"
        "- id: t2\n  checks:\n    - {type: no_findings}\n    - {type: nope}\n", encoding="utf-8")
    return evals.load_tasks(d)


def test_eval_run_compare_and_unknown_check(project, tmp_path):
    tasks = _tasks(tmp_path)
    good = evals.run_suite(tasks, lambda t: {"a": 1, "b": 3, "findings": []}, {}, label="good", root=tmp_path)
    assert good["passed"] == 1 and good["total"] == 2  # t2 fails: unknown check 'nope'
    assert any("unknown check" in c["message"] for t in good["tasks"] for c in t["checks"])
    worse = evals.run_suite(tasks, lambda t: {"a": 2, "b": 3, "findings": []}, {}, label="worse", root=tmp_path)
    cmp = evals.compare_reports(good, worse)
    assert cmp["regressions"] == ["t1"] and not cmp["improvements"]


def test_eval_solver_exception_is_a_failure_not_a_crash(tmp_path):
    tasks = _tasks(tmp_path)

    def boom(t):
        raise RuntimeError("kaput")

    rep = evals.run_suite(tasks, boom, {}, label="x", root=tmp_path)
    assert rep["passed"] == 0 and "kaput" in rep["tasks"][0]["checks"][0]["message"]


def test_check_agent_results_dir(tmp_path):
    tasks = _tasks(tmp_path)
    results = tmp_path / "res"
    results.mkdir()
    (results / "t1.json").write_text(json.dumps({"a": 1, "b": 1}))
    rep = evals.check_results_dir(tasks, {}, results, label="agentA", root=tmp_path)
    by_id = {t["id"]: t for t in rep["tasks"]}
    assert by_id["t1"]["passed"] and not by_id["t2"]["passed"]
    assert "missing" in by_id["t2"]["checks"][0]["message"]


def test_duplicate_task_ids_rejected(tmp_path):
    d = tmp_path / "dup"
    d.mkdir()
    (d / "a.yaml").write_text("- {id: x, checks: []}\n- {id: x, checks: []}\n")
    with pytest.raises(ValueError, match="duplicate"):
        evals.load_tasks(d)


# --- safety -------------------------------------------------------------------------
def test_path_allowlist(tmp_path):
    root = tmp_path / "ok"
    root.mkdir()
    assert resolve_inside(root / "a.txt", [root]) == (root / "a.txt").resolve()
    with pytest.raises(PathNotAllowed):
        resolve_inside(root / ".." / "evil.txt", [root])
    link = root / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(PathNotAllowed):
        resolve_inside(link / "x.txt", [root])


def test_versioner_dedupes_and_restores(project, tmp_path):
    v = Versioner(project)
    a = v.save("level one", '{"a": 1}', label="first")
    same = v.save("level one", '{"a": 1}')
    b = v.save("level one", '{"a": 2}')
    assert a["version"] == 1 and same["unchanged"] and b["version"] == 2
    assert json.loads(v.load("level one", 1)) == {"a": 1}
    out = v.restore("level one", 1, tmp_path / "restored.json")
    assert json.loads(out.read_text()) == {"a": 1}
    with pytest.raises(FileNotFoundError):
        v.load("level one", 9)


def test_plan_dict():
    p = Plan("do things").add("create", "x", "why")
    assert p.to_dict()["dry_run"] and p.to_dict()["steps"][0]["op"] == "create"


# --- style + rubric ---------------------------------------------------------------------
def test_style_brief_focus_and_truncation(project):
    style = load_style(project)
    brief = style_brief(style, focus=["palette"])
    assert brief.index("- palette_range:") < brief.index("- detail:")
    assert "[C1]" in brief and "AVOID" in brief
    assert len(style_brief(style, max_chars=120)) <= 120 + 1


def test_style_validation_catches_problems():
    assert validate_style({"version": 1}) != []
    bad = {"version": 1, "name": "n", "summary": "s", "traits": {}, "ranges": {"x": {"min": 2, "max": 1}},
           "constraints": [{"id": "A", "severity": "must", "rule": "r"}, {"id": "A", "severity": "bad", "rule": "r"}]}
    problems = " ".join(validate_style(bad))
    assert "duplicate" in problems and "min > max" in problems and "severity" in problems


def test_check_ranges_reports_missing_and_out_of_range():
    ranges = {"contrast": {"min": 0.2, "max": 0.9}, "seam": {"max": 1.5, "severity": "error"}}
    f = check_ranges({"contrast": 0.05}, ranges)
    assert {x["metric"] for x in f} == {"contrast", "seam"}
    assert next(x for x in f if x["metric"] == "seam")["message"] == "not measured"


def test_rubric_partial_scores_are_not_final():
    rubric = {"pass_at": 0.7, "criteria": [
        {"id": "valid", "method": "auto", "weight": 2, "required": True},
        {"id": "looks_right", "method": "manual", "weight": 1},
        {"id": "both", "method": "hybrid", "weight": 1}]}
    assert validate_rubric(rubric) == []
    partial = score_rubric(rubric, auto={"valid": True, "both": 0.9})
    assert not partial["complete"] and not partial["passed"] and set(partial["unscored"]) == {"looks_right", "both"}
    full = score_rubric(rubric, auto={"valid": True, "both": 0.9}, manual={"looks_right": 0.8, "both": 0.5})
    assert full["complete"] and full["criteria"][2]["score"] == 0.5 and full["passed"]
    failed = score_rubric(rubric, auto={"valid": False, "both": 1}, manual={"looks_right": 1, "both": 1})
    assert failed["required_failures"] == ["valid"] and not failed["passed"]


# --- skills + agent files --------------------------------------------------------------------
def test_skill_validation(project, tmp_path):
    assert agentfiles.validate_all_skills(project.root) == []
    bad = project.root / "skills" / "Bad_Name"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: Bad_Name\ndescription: x\n---\nbody\n")
    assert any("name" in p for p in agentfiles.validate_all_skills(project.root))
    (bad / "SKILL.md").write_text("no frontmatter")
    assert any("frontmatter" in p for p in agentfiles.validate_all_skills(project.root))


def test_skill_name_must_match_directory(project):
    d = project.root / "skills" / "other-dir"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: different\ndescription: ok\n---\nbody\n")
    assert any("must match the directory" in p for p in agentfiles.validate_skill(d))


def test_sync_agent_files_and_drift_detection(project):
    root = project.root
    assert agentfiles.sync(root, check=True)  # nothing generated yet
    agentfiles.sync(root)
    assert agentfiles.sync(root, check=True) == []
    for rel in agentfiles.CLIENT_FILES:
        assert "Follow the skill." in (root / rel).read_text()
    for mirror in agentfiles.SKILL_MIRRORS:
        assert (root / mirror / "toy-skill" / "SKILL.md").exists()
    (root / "AGENTS.md").write_text("# changed\n")
    assert any("CLAUDE.md" in d for d in agentfiles.sync(root, check=True))
    (root / "skills" / "toy-skill" / "SKILL.md").write_text(
        "---\nname: toy-skill\ndescription: changed\n---\nbody\n")
    assert any(".claude/skills/toy-skill" in d for d in agentfiles.sync(root, check=True))
