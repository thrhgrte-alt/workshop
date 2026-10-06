import json

import pytest

from guide_core.params import LockedParameter, OutOfRange, ParamError, ParamSpec, ParamStore, StepTooLarge
from guide_core.scope import Scope

P, PL, OTHER, G = Scope("proj-a"), Scope("proj-a", "main"), Scope("proj-b", "main"), Scope.make_global()


def specs():
    return [ParamSpec("w.vendor", 3.0, 0.0, 6.0, 0.5, description="weight"),
            ParamSpec("band.low", 5, 0, 60),  # int; default step = max(1, round(60*0.1)) = 6
            ParamSpec("conf", "high", choices=("low", "medium", "high")),
            ParamSpec("fixed", 1.0, 0.0, 2.0, locked=True)]


@pytest.fixture()
def store(tmp_path):
    return ParamStore(tmp_path / "ws" / "params.json", specs())


def up(store, name, value, scope=PL, **kw):
    return store.update(name, value, scope, approved_by=kw.pop("by", "amy"), reason=kw.pop("reason", "because"), **kw)


# --- specs ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw", [dict(name="x", default=5, min=0, max=3), dict(name="x", default=1, min=None, max=3), dict(name="x", default="a", min=0, max=1),
                                dict(name="x", default=True, min=0, max=1), dict(name="", default=1, min=0, max=2), dict(name="a b", default=1, min=0, max=2),
                                dict(name="x", default=1, min=0, max=2, max_step=0), dict(name="x", default="z", choices=("a", "b"))])
def test_invalid_specs_are_rejected(kw):
    with pytest.raises(ParamError):
        ParamSpec(**kw)


def test_kinds_and_default_steps():
    s = specs()
    assert (s[0].kind, s[1].kind, s[2].kind) == ("number", "int", "choice")
    assert s[0].step == 0.5 and s[1].step == 6 and s[2].step == 1
    assert ParamSpec("f", 1.0, 0.0, 2.0).step == pytest.approx(0.2)  # 10% of the range
    assert ParamSpec("i", 5, 0, 20).step == 2


def test_register_conflict(store):
    store.register(ParamSpec("w.vendor", 3.0, 0.0, 6.0, 0.5, description="weight"))  # identical: fine
    with pytest.raises(ParamError, match="already registered"):
        store.register(ParamSpec("w.vendor", 3.0, 0.0, 6.0, 1.0))
    with pytest.raises(ParamError, match="unknown parameter"):
        store.spec("nope")


# --- resolution order -----------------------------------------------------------------------------------------------------
def test_default_when_nothing_is_set(store):
    r = store.resolve("w.vendor", PL)
    assert (r.value, r.source, r.version) == (3.0, "default", None) and r.overridden == []
    assert not store.path.exists()  # reading never creates state


def test_resolution_order_user_label_project_global_default(store):
    up(store, "w.vendor", 3.5, G)
    assert store.resolve("w.vendor", PL).source == "global" and store.value("w.vendor", PL) == 3.5
    up(store, "w.vendor", 4.0, P)  # project 3.5 -> 4.0 (step 0.5)
    r = store.resolve("w.vendor", PL)
    assert (r.value, r.source, r.scope_key) == (4.0, "project", "proj-a")
    up(store, "w.vendor", 4.5, PL)
    r = store.resolve("w.vendor", PL)
    assert (r.value, r.scope_key) == (4.5, "proj-a/main") and r.source == "project"
    assert [(o["source"], o["value"]) for o in r.overridden] == [("project", 4.0), ("global", 3.5), ("default", 3.0)]
    ex = store.resolve("w.vendor", PL, explicit=5.5)
    assert (ex.value, ex.source) == (5.5, "user_label") and [o["source"] for o in ex.overridden] == ["project", "project", "global", "default"]
    assert "over:" in ex.explain() and "user_label" in ex.explain()


def test_other_projects_do_not_see_a_project_value(store):
    up(store, "w.vendor", 3.5, PL)
    assert store.value("w.vendor", OTHER) == 3.0 and store.value("w.vendor", Scope("proj-a", "alt")) == 3.0 and store.value("w.vendor", P) == 3.0
    assert store.value("w.vendor", None) == 3.0


def test_explicit_label_is_range_checked(store):
    with pytest.raises(OutOfRange):
        store.resolve("w.vendor", PL, explicit=99)


# --- bounded updates -------------------------------------------------------------------------------------------------------
def test_step_bound_range_and_type(store):
    assert up(store, "w.vendor", 3.5)["version"] == 1
    with pytest.raises(StepTooLarge, match="allowed step 0.5"):
        up(store, "w.vendor", 5.0)
    with pytest.raises(OutOfRange, match="outside the range"):
        up(store, "w.vendor", 6.5)
    with pytest.raises(OutOfRange, match="not a number"):
        up(store, "w.vendor", "big")
    with pytest.raises(OutOfRange, match="not a number"):
        up(store, "w.vendor", True)
    with pytest.raises(ParamError, match="already the value"):
        up(store, "w.vendor", 3.5)
    assert store.value("w.vendor", PL) == 3.5  # nothing above changed it


def test_step_is_measured_from_the_value_in_force_in_that_scope(store):
    up(store, "w.vendor", 3.5, G)
    with pytest.raises(StepTooLarge):
        up(store, "w.vendor", 5.0, PL)  # global says 3.5, so 5.0 is a 1.5 jump for the project too
    assert up(store, "w.vendor", 4.0, PL)["previous_value"] == 3.5


def test_int_param_whole_numbers_only(store):
    with pytest.raises(OutOfRange, match="whole number"):
        up(store, "band.low", 6.5)
    assert up(store, "band.low", 11)["value"] == 11
    with pytest.raises(StepTooLarge):
        up(store, "band.low", 30)


def test_choice_param_moves_one_position(store):
    with pytest.raises(StepTooLarge):
        up(store, "conf", "low")
    assert up(store, "conf", "medium")["value"] == "medium"
    with pytest.raises(OutOfRange, match="not one of"):
        up(store, "conf", "huge")


def test_nudge_clamps_and_reports_limits(store):
    assert store.nudge("w.vendor", PL, +1, approved_by="amy", reason="r")["value"] == 3.5
    for _ in range(5):
        store.nudge("w.vendor", PL, +1, approved_by="amy", reason="r")
    assert store.value("w.vendor", PL) == 6.0  # clamped at max, never beyond
    with pytest.raises(OutOfRange, match="limit"):
        store.nudge("w.vendor", PL, +1, approved_by="amy", reason="r")
    assert store.nudge("conf", PL, -1, approved_by="amy", reason="r")["value"] == "medium"
    store.nudge("conf", PL, -1, approved_by="amy", reason="r")
    with pytest.raises(OutOfRange, match="end"):
        store.nudge("conf", PL, -1, approved_by="amy", reason="r")


def test_nudge_int_param(store):
    assert store.nudge("band.low", PL, +1, approved_by="amy", reason="r")["value"] == 11


# --- approval and locked ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("who", ["", "  ", "auto", "AUTO", "system", "model", "claude", "llm", None])
def test_automatic_approval_is_refused(store, who):
    with pytest.raises(ParamError, match="approved_by"):
        store.update("w.vendor", 3.5, PL, approved_by=who, reason="r")  # type: ignore[arg-type]
    with pytest.raises(ParamError, match="approved_by"):
        store.rollback("w.vendor", PL, approved_by=who, reason="r")  # type: ignore[arg-type]
    assert not store.path.exists()


def test_reason_is_required(store):
    with pytest.raises(ParamError, match="reason"):
        store.update("w.vendor", 3.5, PL, approved_by="amy", reason=" ")


def test_locked_parameter_never_changes(store):
    with pytest.raises(LockedParameter):
        up(store, "fixed", 1.5)
    with pytest.raises(LockedParameter):
        store.rollback("fixed", PL, approved_by="amy", reason="r")
    r = store.resolve("fixed", PL, explicit=2.0)
    assert (r.value, r.source, r.locked) == (1.0, "default", True) and r.ignored[0]["reason"] == "locked"
    assert "[locked]" in r.explain()


def test_locked_parameter_ignores_a_stored_value_even_if_the_file_was_edited(store):
    up(store, "w.vendor", 3.5)
    state = json.loads(store.path.read_text())
    state["params"]["fixed"] = {"proj-a/main": {"current": 1, "versions": [{"version": 1, "value": 2.0}]}}
    store.path.write_text(json.dumps(state))
    assert store.value("fixed", PL) == 1.0


# --- rollback ---------------------------------------------------------------------------------------------------------------
def test_rollback_restores_exactly_the_previous_version(store):
    v1 = up(store, "w.vendor", 3.5)
    v2 = up(store, "w.vendor", 4.0)
    before_hist = store.history("w.vendor", PL)
    res = store.rollback("w.vendor", PL, approved_by="amy", reason="worse")
    assert (res["from_version"], res["to_version"], res["value_now"]) == (2, 1, 3.5)
    r = store.resolve("w.vendor", PL)
    assert (r.value, r.version, r.source) == (v1["value"], v1["version"], "project")  # exactly version 1, not a copy of it
    hist = store.history("w.vendor", PL)
    assert [(h["version"], h["value"], h["current"]) for h in hist] == [(1, 3.5, True), (2, 4.0, False)]
    assert [{k: v for k, v in h.items() if k != "current"} for h in hist] == [{k: v for k, v in h.items() if k != "current"} for h in before_hist]  # nothing erased
    assert v2["parent"] == 1


def test_rollback_of_first_change_returns_to_no_override(store):
    up(store, "w.vendor", 3.5)
    store.rollback("w.vendor", PL, approved_by="amy", reason="r")
    r = store.resolve("w.vendor", PL)
    assert (r.value, r.source, r.version) == (3.0, "default", None)
    with pytest.raises(ParamError, match="nothing to roll back"):
        store.rollback("w.vendor", PL, approved_by="amy", reason="r")


def test_rollback_to_a_named_version_and_version_numbers_are_never_reused(store):
    up(store, "w.vendor", 3.5)
    up(store, "w.vendor", 4.0)
    up(store, "w.vendor", 4.5)
    store.rollback("w.vendor", PL, approved_by="amy", reason="r", to_version=1)
    assert store.value("w.vendor", PL) == 3.5
    assert up(store, "w.vendor", 4.0)["version"] == 4  # not 2: history only grows
    assert store.history("w.vendor", PL)[-1]["parent"] == 1
    with pytest.raises(ParamError, match="no version 9"):
        store.rollback("w.vendor", PL, approved_by="amy", reason="r", to_version=9)


def test_two_rollbacks_walk_back_the_chain(store):
    up(store, "w.vendor", 3.5)
    up(store, "w.vendor", 4.0)
    up(store, "w.vendor", 4.5)
    store.rollback("w.vendor", PL, approved_by="amy", reason="r")
    store.rollback("w.vendor", PL, approved_by="amy", reason="r")
    assert store.value("w.vendor", PL) == 3.5 and store.resolve("w.vendor", PL).version == 1


def test_rollback_in_one_scope_leaves_others_alone(store):
    up(store, "w.vendor", 3.5, PL)
    up(store, "w.vendor", 3.5, OTHER)
    store.rollback("w.vendor", PL, approved_by="amy", reason="r")
    assert store.value("w.vendor", OTHER) == 3.5 and store.value("w.vendor", PL) == 3.0


# --- views, snapshot, persistence, events ------------------------------------------------------------------------------------
def test_view_with_overrides_stores_nothing(store):
    up(store, "w.vendor", 3.5)
    v = store.view(PL, {"w.vendor": 9 - 4})
    assert v.value("w.vendor") == 5 and v("band.low") == 5 and store.value("w.vendor", PL) == 3.5
    with pytest.raises(OutOfRange):
        store.view(PL, {"w.vendor": 99})
    assert store.view(PL, {"fixed": 2.0}).value("fixed") == 1.0  # a locked parameter cannot be overridden even hypothetically


def test_snapshot_and_knowledge_version(store):
    assert store.knowledge_version() == 0
    up(store, "w.vendor", 3.5)
    store.rollback("w.vendor", PL, approved_by="amy", reason="r")
    snap = store.snapshot(PL)
    assert set(snap) == {"w.vendor", "band.low", "conf", "fixed"} and snap["w.vendor"]["source"] == "default" and snap["fixed"]["locked"]
    assert store.knowledge_version() == 2 and [e["event"] for e in store.events()] == ["update", "rollback"]
    assert store.events()[0]["approved_by"] == "amy"


def test_state_survives_a_new_store_instance_and_rejects_other_schema(store, tmp_path):
    up(store, "w.vendor", 3.5)
    again = ParamStore(store.path, specs())
    assert again.value("w.vendor", PL) == 3.5
    state = json.loads(store.path.read_text())
    state["schema"] = 99
    store.path.write_text(json.dumps(state))
    with pytest.raises(ParamError, match="schema 99"):
        again.value("w.vendor", PL)


def test_provenance_is_recorded(store):
    v = up(store, "w.vendor", 3.5, evidence=["run-2", "run-1", "run-2"], reason="over-confident in 6 of 8 runs")
    assert v["evidence"] == ["run-1", "run-2"] and v["approved_by"] == "amy" and v["reason"].startswith("over-confident") and v["at"]
