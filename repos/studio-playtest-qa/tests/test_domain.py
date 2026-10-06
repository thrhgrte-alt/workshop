"""Units: console parsing, result parsing, flood fill, baselines, aggregation, config validation, generators. Expectations are hand computed."""

import json
from pathlib import Path

import pytest

from playqa.domain import baseline as B, checks as K, config as C, console as CON, evaluate as E, gen, reach as R, report as RP
from playqa.domain.schema import extract_result, validate_result
from playqa.guide_adapter import config as gc, luau_safety, scope as S

ROOT = Path(__file__).resolve().parents[1]
PAT = CON.load_patterns(ROOT)
LIB = K.load_library(ROOT)


@pytest.fixture(scope="module")
def style():
    from playqa import project

    return gc.load_style(project(ROOT))


@pytest.fixture(scope="module")
def cfg():
    return C.load(ROOT / "projects" / "demo_mine" / "main" / "playtest.yaml")


# --- console ----------------------------------------------------------------------------------------------------------------------------
def test_timestamp_forms_are_all_read():
    for line in ("10:00:01 x", "10:00:01.250 x", "[10:00:01.250] x", "2026-10-06T10:00:01Z x"):
        r = CON.parse(line, PAT)
        assert r["lines_read"] == 1
    assert CON._ts("10:00:01.5") == 36001.5 and CON._ts("[00:01:00]") == 60.0 and CON._ts(12) == 12.0 and CON._ts("nonsense") is None and CON._ts(True) is None


def test_level_tags_beat_patterns_and_patterns_beat_nothing():
    r = CON.parse("[Warning] attempt to index nil\n[Error] harmless words\nplain attempt to call nil", PAT)
    assert (r["errors"], r["warnings"]) == (2, 1)  # the tag wins on line 1; line 3 is a pattern
    assert {f["level_source"] for f in r["findings"]} == {"tag", "pattern"}


def test_window_edges_are_inclusive():
    text = "10:00:00.000 a\n10:00:10.000 ServerScriptService.Edge:1: attempt to call nil\n10:00:10.001 ServerScriptService.Late:1: attempt to call nil"
    r = CON.parse(text, PAT, window_seconds=10)
    assert r["errors"] == 1 and r["lines_in_window"] == 2


def test_probe_attribution_stops_at_the_next_probe_and_mark():
    text = "\n".join(["PLAYQA_PROBE R valid1", "ServerScriptService.A:1: attempt to call nil", "PLAYQA_PROBE R bad1", "PLAYQA_MARK remotes 1 end", "ServerScriptService.B:2: attempt to call nil"])
    got = CON.parse_all_probes(text, PAT)
    assert [e["text"] for e in got["R/valid1"]] == ["ServerScriptService.A:1: attempt to call nil"] and "R/bad1" not in got or got["R/bad1"] == []
    assert [e["text"] for e in got[""]] == ["ServerScriptService.B:2: attempt to call nil"]


def test_bad_console_inputs_are_refused_with_a_reason():
    for bad in (5, {"nothing": 1}, [3]):
        with pytest.raises(ValueError):
            CON.parse(bad, PAT)
    with pytest.raises(ValueError, match="no message text"):
        CON.parse([{"level": "error"}], PAT)


# --- hub answer shapes ----------------------------------------------------------------------------------------------------------------------
DOC = {"schema": "playqa.result/1", "check_id": "boot", "kind": "boot", "repeat_no": 1, "assertions": [], "measures": {}}


@pytest.mark.parametrize("raw", [DOC, json.dumps(DOC), {"result": json.dumps(DOC)}, [{"type": "text", "text": "x\nPLAYQA_RESULT " + json.dumps(DOC)}], {"output": {"content": [{"type": "text", "text": json.dumps(DOC)}]}}])
def test_every_documented_shape_yields_the_result(raw):
    doc, err, notes = extract_result(raw)
    assert doc == DOC and err is None and "schema_unverified" in notes


@pytest.mark.parametrize("raw", [None, 5, "", "plain text", {"isError": True}, {"error": "boom"}, [1, 2], {"result": {"result": {"result": {"result": {"result": {"result": DOC}}}}}}])
def test_unreadable_answers_give_no_result_and_never_raise(raw):
    doc, err, notes = extract_result(raw)
    assert doc is None and err and "schema_unverified" in notes


def test_validate_result_names_every_problem():
    assert validate_result(DOC) == []
    bad = {"schema": "x", "check_id": "", "kind": "teleport", "assertions": [{"id": 1}], "measures": 3, "refused": 4}
    assert len(validate_result(bad)) == 6


# --- flood fill -----------------------------------------------------------------------------------------------------------------------------
def edge(a, b, status="Success", wp=3, gap=0.0):
    return {"from": a, "to": b, "status": status, "waypoints": wp, "length": 1.0, "end_gap": gap}


def test_flood_fill_follows_chains_and_reports_the_route():
    fl = R.flood(["A", "B", "C", "D"], [edge("spawn", "A"), edge("A", "B"), edge("B", "C"), edge("spawn", "D", "NoPath", 0)], min_waypoints=2)
    assert sorted(fl["reached"]) == ["A", "B", "C"] and fl["unreached"] == ["D"]
    assert R.route(fl["reached"], "C") == ["spawn", "A", "B", "C"] and fl["reached"]["C"]["hops"] == 3


def test_flood_fill_is_directed():
    fl = R.flood(["A"], [edge("A", "spawn")], min_waypoints=2)
    assert fl["unreached"] == ["A"]


def test_flood_fill_handles_cycles_and_unusable_edges():
    fl = R.flood(["A", "B"], [edge("spawn", "A"), edge("A", "B"), edge("B", "A"), edge("B", "spawn", wp=1)], min_waypoints=2)
    assert sorted(fl["reached"]) == ["A", "B"]
    assert R.usable(edge("a", "b", wp=1), 2)[0] is False and R.usable(edge("a", "b", "ClosestNoPath"), 2)[1] == "status ClosestNoPath"


# --- baselines ---------------------------------------------------------------------------------------------------------------------------------
def test_percentages_and_limits_by_hand(style):
    base = B.make_baseline("d", {"part_count": 1000, "script_count": 40, "memory_mb": 400.0, "frame_ms": 16.0}, {"part_count": 1000, "script_count": 40, "memory_mb": 400.0, "frame_ms": 16.0}, 5, "t")
    cur = {"part_count": 1150, "script_count": 44, "memory_mb": 480.0, "frame_ms": 20.0}
    rows = {(r["metric"], r["phase"]): r for r in B.compare(base, cur, cur, style["ranges"])}
    assert rows[("part_count", "start")]["pct"] == 15.0 and rows[("part_count", "start")]["ok"] is False      # 15% > 10%
    assert rows[("script_count", "end")]["pct"] == 10.0 and rows[("script_count", "end")]["ok"] is True        # exactly the limit
    assert rows[("memory_mb", "start")]["pct"] == 20.0 and rows[("memory_mb", "start")]["ok"] is True         # 20% <= 25%
    assert rows[("frame_ms", "end")]["pct"] == 25.0 and rows[("frame_ms", "end")]["ok"] is True               # exactly 25%


def test_a_decrease_never_fails_and_a_zero_baseline_is_handled(style):
    base = B.make_baseline("d", {"part_count": 100, "script_count": 0, "memory_mb": 10.0, "frame_ms": 10.0}, {"part_count": 100, "script_count": 0, "memory_mb": 10.0, "frame_ms": 10.0}, 5, "t")
    cur = {"part_count": 50, "script_count": 3, "memory_mb": 10.0, "frame_ms": None}
    rows = {(r["metric"], r["phase"]): r for r in B.compare(base, cur, cur, style["ranges"])}
    assert rows[("part_count", "start")]["ok"] is True and rows[("part_count", "start")]["pct"] == -50.0
    assert rows[("script_count", "start")]["ok"] is False and rows[("script_count", "start")]["pct"] is None
    assert rows[("frame_ms", "start")]["ok"] is None and "not measured" in rows[("frame_ms", "start")]["note"]


def test_growth_over_a_run(style):
    assert B.growth({"memory_mb": 400.0}, {"memory_mb": 480.0}, style["ranges"])["pct"] == 20.0 and B.growth({"memory_mb": 400.0}, {"memory_mb": 480.0}, style["ranges"])["ok"] is True
    assert B.growth({"memory_mb": 400.0}, {"memory_mb": 481.0}, style["ranges"])["ok"] is False
    assert B.growth({"memory_mb": None}, {"memory_mb": 1.0}, style["ranges"])["ok"] is None


def test_baseline_documents_are_validated():
    assert B.validate_baseline({"schema": "nope"}) and B.validate_baseline("x")
    good = B.make_baseline("d", {"part_count": 1}, {"part_count": 1}, 5, "t")
    assert B.validate_baseline(good) == []
    good["start"]["part_count"] = -1
    assert B.validate_baseline(good)


# --- aggregation over repeats -------------------------------------------------------------------------------------------------------------------
def runs(passed, failed, kind="remotes"):
    out = [{"repeat": i + 1, "verdict": "pass", "assertions": [], "checked": [], "not_evaluated": [], "notes": []} for i in range(passed)]
    out += [{"repeat": passed + i + 1, "verdict": "fail", "assertions": [{"id": "x", "ok": False, "source": "tool", "detail": "d"}], "checked": [], "not_evaluated": [], "notes": []} for i in range(failed)]
    return out


@pytest.mark.parametrize("flaky,passed,failed,expected", [
    (True, 3, 0, "pass"), (True, 0, 3, "fail"), (True, 2, 1, "flaky"), (True, 1, 2, "flaky"), (True, 0, 2, "inconclusive"), (True, 1, 1, "inconclusive"), (True, 0, 1, "inconclusive"),
    (True, 1, 0, "pass"), (True, 0, 4, "fail"), (True, 1, 3, "flaky"), (False, 0, 1, "fail"), (False, 5, 1, "fail"), (False, 3, 0, "pass")])
def test_aggregation_table(style, cfg, flaky, passed, failed, expected):
    check = {**LIB["remotes"], "flaky": flaky}
    assert E.aggregate(check, {}, style, runs(passed, failed))["verdict"] == expected


def test_aggregation_with_a_lower_confirm_fraction(style):
    s = json.loads(json.dumps(style))
    s["settings"]["flaky_confirm_fail_fraction"]["value"] = 0.6
    assert E.aggregate(LIB["remotes"], {}, s, runs(1, 2))["verdict"] == "fail"      # 2/3 = 0.67 >= 0.6
    assert E.aggregate(LIB["remotes"], {}, s, runs(2, 1))["verdict"] == "flaky"     # 1/3 = 0.33 < 0.6


def test_inconclusive_and_refused_runs_do_not_count_as_failures_or_passes(style):
    base = {"assertions": [], "checked": [], "not_evaluated": [], "notes": []}
    assert E.aggregate(LIB["spawn"], {}, style, [{**base, "repeat": 1, "verdict": "pass"}, {**base, "repeat": 2, "verdict": "inconclusive"}])["verdict"] == "pass"
    assert E.aggregate(LIB["spawn"], {}, style, [{**base, "repeat": 1, "verdict": "inconclusive", "script_error": "boom"}])["verdict"] == "inconclusive"
    assert E.aggregate(LIB["spawn"], {}, style, [{**base, "repeat": 1, "verdict": "pass"}, {**base, "repeat": 2, "verdict": "refused", "refused": "r"}])["verdict"] == "refused"
    assert E.aggregate(LIB["spawn"], {}, style, [])["verdict"] == "inconclusive"


def test_a_place_can_mark_a_check_flaky_or_stable(style):
    assert K.is_flaky(LIB["spawn"], {"flaky": ["spawn"]}) and not K.is_flaky(LIB["remotes"], {"not_flaky": ["remotes"]})
    assert K.check_flags(LIB["remotes"], {"not_flaky": ["remotes"]})["why"] == "not_flaky in playtest.yaml"
    assert K.repeats_for(LIB["remotes"], {}, style) == 3 and K.repeats_for(LIB["spawn"], {}, style) == 1 and K.repeats_for(LIB["spawn"], {}, style, override=4) == 4


# --- report: causes ---------------------------------------------------------------------------------------------------------------------------------
def test_cause_rules_are_ordered_and_end_with_a_fallback():
    rules = RP.load_causes(ROOT)
    assert rules[-1]["id"] == "generic" and len({r["id"] for r in rules}) == len(rules)
    f = {"assertion": "delta_as_expected", "evidence": {"log_lines": [], "detail": ""}, "ratio": 2.0}
    assert RP.match_cause(rules, "economy", f)["id"] == "econ-doubled"
    f["ratio"] = 0.5
    assert RP.match_cause(rules, "economy", f)["id"] == "econ-too-small"
    f["ratio"] = 3.0
    assert RP.match_cause(rules, "economy", f)["id"] == "econ-too-big"
    f["ratio"] = -1.0
    assert RP.match_cause(rules, "economy", f)["id"] == "econ-wrong-direction"
    assert RP.match_cause(rules, "boot", {"assertion": "never_heard_of_it", "evidence": {"log_lines": [], "detail": ""}})["id"] == "generic"


def test_every_assertion_id_the_evaluators_can_emit_is_listed_in_the_library():
    listed = {c["id"]: {a["id"] for a in c["assertions"]} for c in LIB.values()}
    assert listed["boot"] >= {"services_exist", "paths_exist", "console_no_errors", "console_no_warnings", "log_service_clean"}
    assert all(set(c["assertions"][0]) >= {"id", "title", "source"} for c in LIB.values())
    kinds = {c["kind"] for c in LIB.values()}
    assert kinds == {"boot", "spawn", "reachability", "remotes", "economy", "data", "perf"}


def test_every_assertion_id_seen_in_runs_is_declared(project):
    """Run every check, clean and faulty, and require that every assertion id that comes out is declared in rules/*.yaml (and the clean runs emit most of them)."""
    from playqa.domain.harness import run_world_check

    seen: dict[str, set] = {}
    for cid, faults in [("boot", []), ("boot", ["boot_error"]), ("boot", ["boot_warning"]), ("spawn", []), ("spawn", ["spawn_stuck"]), ("reachability", []), ("reachability", ["unreachable_room"]),
                        ("remotes", []), ("remotes", ["remote_errors"]), ("remotes", ["remote_accepts_bad"]), ("economy_smoke", []), ("economy_smoke", ["negative_balance"]), ("data_roundtrip", []), ("data_roundtrip", ["data_loses_value"]),
                        ("perf_snapshot", [])]:
        out = run_world_check(project, cid, faults=faults, baseline="clean" if cid == "perf_snapshot" else None, repeats=1, detail=True)
        ids = {a["id"] for r in out["report"]["runs"] for a in r["assertions"]}
        seen.setdefault(cid, set()).update(ids)
    for cid, ids in seen.items():
        declared = {a["id"] for a in LIB[cid]["assertions"]}
        assert ids == declared, (cid, "undeclared", ids - declared, "never emitted", declared - ids)


# --- config -----------------------------------------------------------------------------------------------------------------------------------------
def test_the_synthetic_configs_are_valid_and_typos_are_refused(cfg):
    assert C.validate(cfg) == []
    bad = json.loads(json.dumps(cfg))
    bad["bootx"] = {}
    bad["economy"]["steps"][0]["expect"]["nope"] = {"delta": 1}
    bad["remotes"]["items"][0]["path"] = "ReplicatedStorage.Remo tes"
    bad["reachability"]["areas"][0]["name"] = "spawn"
    problems = C.validate(bad)
    assert any("bootx" in p for p in problems) or any("Additional properties" in p for p in problems)


@pytest.mark.parametrize("patch,needle", [
    ({"economy.steps.0.expect.ore": {"delta": 1, "sign": "positive"}}, "cannot be combined"),
    ({"economy.steps.0.expect.ore": {"delta_min": 5, "delta_max": 1}}, "above delta_max"),
    ({"economy.steps.0.expect.ghost": {"delta": 1}}, "not one of the player_values"),
    ({"remotes.items.2.reject_values": ["x"]}, "only apply to a RemoteFunction"),
    ({"remotes.items.0.valid_args": [[{"$repeat": "x", "times": 99999}]]}, "times"),
    ({"remotes.items.0.valid_args": [[{"$number": "pi"}]]}, "nan, inf or -inf"),
])
def test_config_semantics_are_checked(cfg, patch, needle):
    from playqa.domain.harness import patch_cfg

    c = patch_cfg(cfg, [{"set": k, "value": v} for k, v in patch.items()])
    assert any(needle in p for p in C.validate(c)), C.validate(c)


def test_explicit_overrides_must_name_real_settings_and_respect_ranges(style):
    ok = C.apply_overrides(style, {"overrides": {"settings": {"boot_window_seconds": 20.0}, "ranges": {"perf_memory_increase_pct": {"max": 30.0}}}})
    assert ok["settings"]["boot_window_seconds"]["value"] == 20.0 and ok["ranges"]["perf_memory_increase_pct"]["max"] == 30.0 and style["settings"]["boot_window_seconds"]["value"] == 10.0
    assert C.apply_overrides(style, {}) is style
    for ov, needle in (({"settings": {"nope": 1}}, "unknown setting"), ({"settings": {"boot_window_seconds": 999}}, "outside its range"), ({"ranges": {"boot_error_lines": {"max": 1}}}, "locked"),
                       ({"ranges": {"ghost": {"max": 1}}}, "unknown range"), ({"settings": {"boot_window_seconds": True}}, "must be a number")):
        with pytest.raises(ValueError, match=needle):
            C.apply_overrides(style, {"overrides": ov})


# --- generators -------------------------------------------------------------------------------------------------------------------------------------
def test_literal_embedding_is_strict():
    assert gen.lv("a\"b") == '"a\\"b"' and gen.lv([1, "x", None, True]) == '{1, "x", nil, true}' and gen.lv({"$number": "nan"}) == "(0/0)" and gen.lv({"$repeat": "x", "times": 3}) == 'string.rep("x", 3)'
    assert gen.num(3.0) == "3" and gen.num(2.5) == "2.5"
    for bad in (float("nan"), float("inf"), object(), "a\nb", {"a b": 1}, {"$weird": 1} if False else b"x"):
        with pytest.raises((ValueError, TypeError)):
            gen.lv(bad)
    with pytest.raises(ValueError):
        gen.lv([[[[[[[[1]]]]]]]])


def test_template_fill_never_rescans_inserted_text():
    assert gen.fill("a @X@ b @Y@", X="@Y@", Y="2") == "a @Y@ b 2"
    with pytest.raises(ValueError, match="unfilled"):
        gen.fill("@NOPE@", X="1")


def test_args_literal_keeps_nil_holes_countable():
    assert gen.args_lit([]) == "{n = 0}" and gen.args_lit([None, "a"]) == '{n = 2, nil, "a"}'


def test_every_script_is_lint_clean_deterministic_and_marker_framed(style, cfg):
    reg = S.ProjectRegistry.load(ROOT / "projects.yaml")
    place = reg.place(reg.resolve("demo_mine", "main"))
    probes = gen.load_probes(ROOT)
    for c in LIB.values():
        for phase in (["start", "end"] if c["kind"] == "perf" else [None]):
            a = gen.generate(c, cfg, style, place, "demo_mine/main", phase=phase, probes=probes)
            b = gen.generate(c, cfg, style, place, "demo_mine/main", phase=phase, probes=probes)
            assert a.luau == b.luau and a.sha256 == b.sha256 and a.lint == [] and luau_safety.lint(a.luau) == []
            assert a.luau.count("JSONEncode") == 1 and "PLAYQA_MARK" in a.luau and "wrong place" in a.luau
            for token in ("DataStore", "HttpGet", "RequestAsync", "require(", "loadstring", ":Destroy(", "Publish"):
                assert token not in a.luau, (c["id"], token)
            assert "+=" not in a.luau and "continue" not in a.luau and ": number" not in a.luau  # stays in the subset the mock (Lua 5.4) runs


def test_generation_depends_on_the_repeat_number_only_in_the_header_and_constants(style, cfg):
    reg = S.ProjectRegistry.load(ROOT / "projects.yaml")
    place = reg.place(reg.resolve("demo_mine", "main"))
    a = gen.generate(LIB["boot"], cfg, style, place, "demo_mine/main", repeat_no=1)
    b = gen.generate(LIB["boot"], cfg, style, place, "demo_mine/main", repeat_no=2)
    assert a.sha256 != b.sha256 and a.luau.replace("repeat: 1", "repeat: 2").replace("CHECK_ID, KIND, REPEAT_NO = \"boot\", \"boot\", 1", "CHECK_ID, KIND, REPEAT_NO = \"boot\", \"boot\", 2") == b.luau


def test_a_place_without_a_studio_name_cannot_be_guarded(style, cfg):
    class Bare:
        studio_name = None
        roblox_place_id = None

    with pytest.raises(ValueError, match="place guard"):
        gen.generate(LIB["boot"], cfg, style, Bare(), "demo_mine/main")
