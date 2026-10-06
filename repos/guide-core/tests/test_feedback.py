import json
import sqlite3

import pytest

from guide_core import feedback as fb
from guide_core.scope import Scope, ScopeError

A, A_ALT, B = Scope("proj-a", "main"), Scope("proj-a", "alt"), Scope("proj-b", "main")


def _correct(project, request, note, scope, **kw):
    rid = fb.record_run(project, request=request, scope=scope)
    fb.record_decision(project, rid, "revise", reason="not right", corrections=[{"dimension": "bevel", "note": note}], **kw)
    return rid


def test_rows_carry_schema_and_scope(project):
    rid = fb.record_run(project, request="door", scope=A)
    row = fb.get_run(project, rid)
    assert row["schema"] == fb.SCHEMA == 1 and row["scope"] == {"project_id": "proj-a", "place_id": "main", "global": False}
    d = fb.record_decision(project, rid, "accept")
    assert d["schema"] == 1 and d["scope"]["project_id"] == "proj-a"  # inherited from the run


def test_legacy_rows_without_scope_still_work(project):
    rid = fb.record_run(project, request="old style request")
    assert "scope" not in fb.get_run(project, rid)
    fb.record_decision(project, rid, "revise", reason="old style reason")
    assert fb.find_past_corrections(project, "old style")[0]["run_id"] == rid
    assert fb.find_past_corrections(project, "old style", scope=A) == []  # an unscoped row is never shown to a scoped query


def test_strict_scope_refuses_missing_scope(project):
    with pytest.raises(ScopeError, match="resolved scope"):
        fb.record_run(project, request="x", scope=None, strict_scope=True)
    with pytest.raises(ScopeError):
        fb.find_past_corrections(project, "x", strict_scope=True)
    rid = fb.record_run(project, request="x")
    with pytest.raises(ScopeError):
        fb.record_decision(project, rid, "accept", strict_scope=True)


def test_corrections_never_cross_projects(project):
    _correct(project, "round door bevels", "round the bevels", A)
    _correct(project, "sharp door bevels", "sharper bevels", B)
    assert [h["request"] for h in fb.find_past_corrections(project, "door bevels", scope=A)] == ["round door bevels"]
    assert [h["request"] for h in fb.find_past_corrections(project, "door bevels", scope=B)] == ["sharp door bevels"]
    assert len(fb.find_past_corrections(project, "door bevels")) == 2  # legacy unscoped query: the whole workspace


def test_place_level_and_project_level_visibility(project):
    _correct(project, "alt place bevels", "n1", A_ALT)
    _correct(project, "project wide bevels", "n2", Scope("proj-a"))
    got = [h["request"] for h in fb.find_past_corrections(project, "bevels", scope=A)]
    assert got == ["project wide bevels"]  # the alt-place correction stays in alt; the project-wide one reaches every place


def test_global_marking_and_exclusion(project):
    rid = fb.record_run(project, request="every vendor needs a price", scope=B)
    row = fb.record_decision(project, rid, "revise", reason="always", corrections=[{"dimension": "vendor", "note": "price"}], mark_global=True)
    assert row["scope"]["global"] is True and row["origin_scope"]["project_id"] == "proj-b"
    assert fb.find_past_corrections(project, "vendor price", scope=A)[0]["run_id"] == rid
    assert fb.find_past_corrections(project, "vendor price", scope=A, include_global=False) == []


def test_tags_blend_with_text(project):
    _correct(project, "bevel on stone door", "round", A, tags=["stone"])
    _correct(project, "bevel on wood door", "round", A, tags=["wood"])
    hits = fb.find_past_corrections(project, "bevel door", scope=A, tags=["wood"])
    assert hits[0]["request"] == "bevel on wood door"


def test_zero_score_rows_are_not_returned(project):
    _correct(project, "round door bevels", "round", A)
    assert fb.find_past_corrections(project, "zebra", scope=A) == []


def test_index_is_rebuilt_when_missing_or_damaged(project):
    rid = _correct(project, "round door bevels", "round the bevels", A)
    idx = project.feedback_dir / "index.sqlite"
    assert idx.exists()
    idx.unlink()
    assert fb.find_past_corrections(project, "door bevels", scope=A)[0]["run_id"] == rid
    idx.write_bytes(b"this is not a sqlite database")
    assert fb.get_run(project, rid)["request"] == "round door bevels"
    assert fb.rebuild_index(project) == {"runs": 1, "decisions": 1, "schema_version": 1}


def test_index_picks_up_rows_appended_outside_the_api(project):
    fb.record_run(project, request="first", scope=A)
    with (project.feedback_dir / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps({"run_id": "run-external", "at": "2026-01-01T00:00:00+00:00", "request": "external"}) + "\n")
    assert fb.get_run(project, "run-external")["request"] == "external"
    assert [r["request"] for r in fb.list_runs(project, 5)] == ["first", "external"]


def test_half_written_last_line_is_ignored_until_complete(project):
    fb.record_run(project, request="first", scope=A)
    f = project.feedback_dir / "runs.jsonl"
    with f.open("a") as fh:
        fh.write('{"run_id": "run-torn", "request": "to')  # no newline: a write in progress
    assert fb.get_run(project, "run-torn") is None and len(fb.list_runs(project)) == 1


def test_index_with_other_schema_is_rebuilt(project):
    rid = fb.record_run(project, request="first", scope=A)
    assert fb.get_run(project, rid) is not None  # reading builds the index
    con = sqlite3.connect(project.feedback_dir / "index.sqlite")
    con.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
    con.commit()
    con.close()
    assert fb.get_run(project, rid) is not None and fb.index_info(project)["schema_version"] == 1


def test_jsonl_is_the_source_of_truth_and_append_only(project):
    r1 = fb.record_run(project, request="one", scope=A)
    before = (project.feedback_dir / "runs.jsonl").read_bytes()
    fb.record_run(project, request="two", scope=A)
    after = (project.feedback_dir / "runs.jsonl").read_bytes()
    assert after.startswith(before) and len(after.splitlines()) == 2 and fb.get_run(project, r1)["request"] == "one"


def test_regression_case_validation_and_listing(project):
    rid = fb.record_run(project, request="35 line scripts are fine", scope=A)
    with pytest.raises(ValueError, match="regression_case"):
        fb.record_decision(project, rid, "accept", corrections=[{"dimension": "d", "note": "n"}], regression_case={"input": {}})
    fb.record_decision(project, rid, "accept", corrections=[{"dimension": "d", "note": "n"}], regression_case={"input": {"x": 1}, "checks": [{"type": "equals", "path": "a", "value": 1}]})
    other = fb.record_run(project, request="b", scope=B)
    fb.record_decision(project, other, "accept", corrections=[{"dimension": "d", "note": "n"}], regression_case={"input": {}, "checks": [{"type": "has_keys", "keys": ["a"]}]})
    cases = fb.accepted_regression_cases(project, A)
    assert len(cases) == 1 and cases[0]["input"] == {"x": 1} and cases[0]["run_id"] == rid
    assert len(fb.accepted_decisions(project, A)) == 1 and len(fb.accepted_regression_cases(project, None)) == 2


def test_recent_corrections_newest_first_and_scoped(project):
    _correct(project, "first", "n1", A)
    _correct(project, "second", "n2", A)
    _correct(project, "third", "n3", B)
    got = fb.recent_corrections(project, A, 5)
    assert [c["request"] for c in got] == ["second", "first"]
    assert fb.recent_corrections(project, Scope.make_global()) == []


def test_promote_run_records_project(project):
    rid = fb.record_run(project, request="wall", scope=A)
    fb.record_decision(project, rid, "accept")
    asset = fb.promote_run(project, rid, kind="material", title="Wall", description="d", tags=["stone"], path="p", scope=A)
    assert asset["project"] == "proj-a" and asset["status"] == "candidate"


def test_reading_never_creates_files_where_no_feedback_exists(project):
    assert fb.find_past_corrections(project, "anything", scope=A) == [] and fb.list_runs(project) == [] and fb.get_run(project, "run-x") is None
    assert fb.decisions_for(project, "run-x") == [] and fb.accepted_regression_cases(project, A) == [] and fb.recent_corrections(project, A) == []
    assert fb.index_info(project)["schema_version"] == 1
    assert not project.feedback_dir.exists()  # not even the directory
    fb.record_run(project, request="x", scope=A)
    fb.list_runs(project)
    assert (project.feedback_dir / "index.sqlite").exists()  # the index appears once there is something to index
