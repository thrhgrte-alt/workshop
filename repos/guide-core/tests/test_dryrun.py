import json

import pytest

from guide_core.dryrun import Plan, Versioner, run_plan, text_diff


def test_plan_to_dict_and_add():
    p = Plan("two changes").add("rename", "A", "-> B").add("delete", "C")
    d = p.to_dict()
    assert d["dry_run"] is True and d["summary"] == "two changes" and [s["op"] for s in d["steps"]] == ["rename", "delete"]
    assert p.to_dict(dry_run=False)["dry_run"] is False


def test_fingerprint_is_stable_and_changes_with_steps():
    a = Plan("x").add("rename", "A", "-> B")
    b = Plan("x").add("rename", "A", "-> B")
    c = Plan("x").add("rename", "A", "-> C")
    assert a.fingerprint() == b.fingerprint() != c.fingerprint() and len(a.fingerprint()) == 12


def test_dry_run_never_calls_the_applier():
    calls = []
    res = run_plan(Plan("p").add("op", "t"), lambda p: calls.append(1))
    assert calls == [] and res["dry_run"] is True and "fingerprint" in res


def test_apply_runs_applier_and_journals(tmp_path):
    journal = tmp_path / "j" / "journal.jsonl"
    res = run_plan(Plan("p").add("rename", "A"), lambda p: ["A"], apply=True, journal=journal)
    assert res["dry_run"] is False and res["changed"] == ["A"]
    rows = [json.loads(x) for x in journal.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["changed"] == ["A"] and rows[0]["summary"] == "p"


def test_apply_defaults_changed_to_step_targets(tmp_path):
    res = run_plan(Plan("p").add("rename", "A").add("rename", "B"), lambda p: None, apply=True)
    assert res["changed"] == ["A", "B"]


def test_apply_needs_an_applier():
    with pytest.raises(ValueError, match="applier"):
        run_plan(Plan("p"), None, apply=True)


def test_apply_refuses_a_plan_that_differs_from_the_reviewed_one():
    reviewed = Plan("p").add("rename", "A").fingerprint()
    with pytest.raises(ValueError, match="plan changed"):
        run_plan(Plan("p").add("rename", "B"), lambda p: None, apply=True, expect=reviewed)


def test_failed_apply_is_journaled_and_reraised(tmp_path):
    j = tmp_path / "j.jsonl"

    def boom(p):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        run_plan(Plan("p").add("x", "y"), boom, apply=True, journal=j)
    row = json.loads(j.read_text().splitlines()[0])
    assert "disk full" in row["error"] and "changed" not in row


def test_text_diff():
    d = text_diff("a\nb\n", "a\nc\n", "f.txt")
    assert "-b" in d and "+c" in d and "a/f.txt" in d


def test_versioner_roundtrip(project):
    v = Versioner(project)
    one = v.save("spec", "one", suffix=".txt")
    same = v.save("spec", "one", suffix=".txt")
    two = v.save("spec", "two", suffix=".txt")
    assert one["version"] == 1 and same["unchanged"] and two["version"] == 2
    assert v.load("spec") == "two" and v.load("spec", 1) == "one"
    with pytest.raises(FileNotFoundError):
        v.load("spec", 9)
