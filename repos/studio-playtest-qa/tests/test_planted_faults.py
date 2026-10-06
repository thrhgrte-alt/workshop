"""The four planted faults the spec names (unreachable room, erroring remote, script error at boot, currency bug) plus the other faults of the mock world.

Each fault is run through the REAL tool path: generate_check_script -> execute on the mock world (lupa) -> explain_failure. The clean world must pass. Expected numbers are hand
computed from the mock world's layout (see the header of playqa/domain/mockworld.py and the comments here), not copied from output.
"""

import json
import math

import pytest

from conftest import MAIN, STUDIO
from playqa.guide_adapter import all_tools, mcpkit, mock
from playqa.hooks import HOOKS
from playqa.domain.mockworld import FAULTS, World

pytestmark = pytest.mark.skipif(not mock.available(), reason="lupa (the Luau mock) is not installed")


def tools(project):
    return all_tools(project, HOOKS)


def script(project, check, repeat=1, phase=None):
    res = mcpkit.call_local(tools(project), "generate_check_script", {**MAIN, "studios": STUDIO, "check_id": check, "repeat_no": repeat, "phase": phase, "dry_run": False})
    assert res["lint"] == []
    return res["luau"]


def explain(project, check, results, console=None, **kw):
    return mcpkit.call_local(tools(project), "explain_failure", {**MAIN, "check_id": check, "results": results, "console_log": console, **kw})


def run_check(project, check, faults=(), runs=1, context=None, coins=None):
    """Generate through the tool, execute on a fresh world per run, judge through the tool."""
    results = []
    for i in range(1, runs + 1):
        ctx = context or ("client" if check == "remotes" else "server")
        w = World(faults, context=ctx, start_coins=(50 if ctx == "client" else 0) if coins is None else coins)
        raw = w.run(script(project, check, i))
        results.append({"result": raw, "console_log": w.console_text()})
    return explain(project, check, results)


# --- the clean place passes everything --------------------------------------------------------------------------------------
@pytest.mark.parametrize("check,runs", [("boot", 1), ("spawn", 1), ("reachability", 1), ("remotes", 3), ("economy_smoke", 3), ("data_roundtrip", 1)])
def test_the_clean_world_passes_every_check(project, check, runs):
    rep = run_check(project, check, runs=runs)
    assert rep["verdict"] == "pass", rep["summary"]
    assert rep["summary"].startswith(f"PASS {check}")
    assert "failures" not in rep and rep["checked"] and rep["not_checked"]


def test_the_clean_performance_run_passes_against_its_own_baseline(project):
    w = World()
    start, end = w.run(script(project, "perf_snapshot", 1, "start")), None
    w.advance(300)
    end = w.run(script(project, "perf_snapshot", 1, "end"))
    mcpkit.call_local(tools(project), "save_baseline", {**MAIN, "results": [start, end], "dry_run": False})
    w2 = World()
    s2 = w2.run(script(project, "perf_snapshot", 1, "start"))
    w2.advance(300)
    e2 = w2.run(script(project, "perf_snapshot", 1, "end"))
    rep = explain(project, "perf_snapshot", [s2, e2], repeats=1)
    assert rep["verdict"] == "pass" and "8 of 8 comparable" in rep["checked"][2]


# --- the four named faults ----------------------------------------------------------------------------------------------------------
def test_unreachable_room_is_caught_and_the_clean_version_passes(project):
    bad = run_check(project, "reachability", ["unreachable_room"])
    assert bad["verdict"] == "fail"
    assert [(f["assertion"], f["subject"]) for f in bad["failures"]] == [("area_reachable", "Vault")]
    f = bad["failures"][0]
    assert "spawn->Vault: status NoPath" in f["evidence"]["detail"] and f["cause_rule"] == "reach-no-path"
    assert set(f["evidence"]) >= {"measured", "expected", "detail", "log_lines", "screenshots"}
    assert run_check(project, "reachability")["verdict"] == "pass"


def test_erroring_remote_is_caught_and_the_clean_version_passes(project):
    bad = run_check(project, "remotes", ["remote_errors"], runs=3)
    assert bad["verdict"] == "fail" and bad["repeats"]["failed"] == 3 and bad["repeats"]["conclusive"] == 3
    assert {f["assertion"] for f in bad["failures"]} == {"valid_call_no_error", "bad_input_no_error"} and {f["subject"] for f in bad["failures"]} == {"BuyUpgrade"}
    assert any("attempt to index nil with 'Cost'" in l for f in bad["failures"] for l in f["evidence"]["log_lines"])
    assert run_check(project, "remotes", runs=3)["verdict"] == "pass"


def test_script_error_at_boot_is_caught_and_the_clean_version_passes(project):
    bad = run_check(project, "boot", ["boot_error"])
    assert bad["verdict"] == "fail"
    assert {f["assertion"] for f in bad["failures"]} == {"console_no_errors", "log_service_clean"}
    assert all("attempt to index nil with 'Spawn'" in f["evidence"]["log_lines"][0] for f in bad["failures"])
    assert all(f["cause_rule"] == "boot-nil-index" for f in bad["failures"])
    assert run_check(project, "boot")["verdict"] == "pass"


def test_currency_bug_is_caught_and_the_clean_version_passes(project):
    bad = run_check(project, "economy_smoke", ["currency_bug"], runs=3)
    assert bad["verdict"] == "fail"
    (f,) = bad["failures"]
    # hand computation: mine x3 -> ore 3; sell -> coins 3 x 5 = 15; buying PickaxeII (price 10) should leave 5, but the bug leaves 15: change 0 against an expected -10
    assert (f["assertion"], f["subject"], f["evidence"]["measured"], f["ratio"]) == ("delta_as_expected", "buy_upgrade.coins", 0, 0.0)
    assert "coins went 15 -> 15" in f["evidence"]["detail"] and f["runs"] == [1, 2, 3]
    assert run_check(project, "economy_smoke", runs=3)["verdict"] == "pass"


@pytest.mark.parametrize("fault,check,runs,failed", [
    ("boot_warning", "boot", 1, {"console_no_warnings"}), ("missing_folder", "boot", 1, {"paths_exist"}),
    ("spawn_stuck", "spawn", 1, {"not_stuck"}), ("spawn_lava", "spawn", 1, {"floor_valid"}), ("spawn_void", "spawn", 1, {"above_void", "floor_found"}), ("no_character", "spawn", 1, {"character_spawned"}),
    ("path_stops_short", "reachability", 1, {"path_ends_at_target"}), ("remote_accepts_bad", "remotes", 3, {"bad_input_rejected"}), ("event_errors", "remotes", 3, {"bad_input_no_error"}),
    ("sell_doubles", "economy_smoke", 3, {"delta_as_expected"}), ("negative_balance", "economy_smoke", 3, {"delta_as_expected", "balance_not_negative"}), ("data_loses_value", "data_roundtrip", 1, {"values_roundtrip"}),
])
def test_every_other_planted_fault_is_caught(project, fault, check, runs, failed):
    rep = run_check(project, check, [fault], runs=runs)
    assert rep["verdict"] == "fail", rep["summary"]
    assert {f["assertion"] for f in rep["failures"]} == failed


def test_every_fault_in_the_mock_world_is_covered_by_a_test():
    covered = {"unreachable_room", "remote_errors", "boot_error", "currency_bug", "boot_warning", "missing_folder", "spawn_stuck", "spawn_lava", "spawn_void", "no_character", "path_stops_short",
               "remote_accepts_bad", "event_errors", "sell_doubles", "negative_balance", "data_loses_value"}
    assert covered == set(FAULTS)


# --- what the scripts measure (hand-computed against the mock world's layout) ----------------------------------------------------------
def test_reachability_script_measures_the_hand_computed_path_lengths(project):
    doc = World().run_json(script(project, "reachability"))
    paths = {p["to"]: p for p in doc["measures"]["paths"]}
    assert set(paths) == {"Shop", "MineEntrance", "Vault"} and all(p["from"] == "spawn" and p["status"] == "Success" and p["waypoints"] == 3 and p["end_gap"] == 0 for p in paths.values())
    assert paths["Shop"]["length"] == 20.0  # (0,2,0) -> (20,2,0): two segments of 10
    assert paths["MineEntrance"]["length"] == pytest.approx(round(math.sqrt(40 ** 2 + 10 ** 2), 2))  # sqrt(1700) = 41.23
    assert paths["Vault"]["length"] == pytest.approx(round(math.sqrt(60 ** 2 + 30 ** 2), 2))  # sqrt(4500) = 67.08
    assert doc["measures"]["spawn"]["position"] == [0, 2, 0]


def test_spawn_script_measures(project):
    m = World().run_json(script(project, "spawn"))["measures"]
    assert m["spawn_position"] == [0, 3, 0] and m["floor"]["name"] == "Ground" and m["probe"]["best_moved"] == 8.0 and m["probe"]["tried"] == [8.0]


def test_economy_script_records_before_and_after_of_every_step(project):
    steps = {s["id"]: s for s in World().run_json(script(project, "economy_smoke"))["measures"]["steps"]}
    assert steps["mine"]["calls"] == 3 and steps["mine"]["before"] == {"coins": 0, "ore": 0} and steps["mine"]["after"] == {"coins": 0, "ore": 3}
    assert steps["sell"]["after"] == {"coins": 15, "ore": 0}
    assert steps["buy_upgrade"]["after"]["coins"] == 5 and steps["buy_too_expensive"]["after"]["coins"] == 5


def test_remotes_script_sends_the_configured_and_the_generic_probes(project):
    rows = {r["id"]: r for r in World(context="client", start_coins=50).run_json(script(project, "remotes"))["measures"]["remotes"]}
    assert [b["label"] for b in rows["BuyUpgrade"]["bad"]] == ["unknown_item", "wrong_type_number", "wrong_type_table", "nan_number", "inf_number", "negative_number", "huge_string"]
    assert len(rows["Ping"]["bad"]) == 6 and len(rows["EquipTool"]["bad"]) == 6  # the first 6 generic probes (remote_max_probes = 6)
    assert rows["BuyUpgrade"]["valid"][0]["value"] == {"kind": "boolean", "value": True} and all(b["rejected"] for b in rows["BuyUpgrade"]["bad"])


def test_data_script_saves_resets_and_loads(project):
    doc = World().run_json(script(project, "data_roundtrip"))
    rows = {r["id"]: r for r in doc["measures"]["values"]}
    assert rows["coins"] == {"id": "coins", "found": True, "marker": 777, "reset": 0, "before": 0, "after": 777, "ok": True}
    assert rows["ore"]["marker"] == 12 and rows["ore"]["after"] == 12 and doc.get("refused") is None


def test_the_data_script_touches_nothing_when_the_switch_is_off(project):
    w = World(test_mode=False)
    doc = w.run_json(script(project, "data_roundtrip"))
    assert doc["refused"] and "no data hook was called" in doc["refused"] and "measures" in doc
    assert w.value("Coins") == 0 and w.value("Ore") == 0 and "values" not in doc["measures"]  # no marker was ever written
    assert "PLAYQA_PROBE data save" not in w.console_text()


def test_the_performance_script_counts_what_the_world_holds(project):
    w = World()
    m = w.run_json(script(project, "perf_snapshot", 1, "start"))["measures"]
    # parts: Ground, Spawn, three area targets, the character's HumanoidRootPart; scripts: Boot, Economy, Hud; one ModuleScript
    assert (m["part_count"], m["script_count"], m["module_count"], m["memory_mb"], m["frame_ms"]) == (6, 3, 1, 410.0, 16.6)
    w.add_parts(4)
    assert w.run_json(script(project, "perf_snapshot", 1, "end"))["measures"]["part_count"] == 10


# --- the place guard and the open-place match -----------------------------------------------------------------------------------------
def test_the_script_stops_itself_in_another_place(project):
    luau = script(project, "boot")
    with pytest.raises(RuntimeError, match="wrong place"):
        World(name="Some Other Game").run(luau)
    rep = explain(project, "boot", ["wrong place: this script is for demo_mine/main (PlaceId 0) but PlaceId 5 is open"], console="10:00:00.000 hi")
    assert rep["verdict"] == "refused" and rep["summary"].startswith("REFUSED boot")


def test_a_published_place_is_guarded_by_its_place_id(project):
    res = mcpkit.call_local(tools(project), "generate_check_script", {"project_id": "demo_tycoon", "place_id": "main", "check_id": "boot", "dry_run": False,
                                                                       "studios": [{"name": "x", "place_id": 9000000001, "studio_id": "s"}]})
    assert "local EXPECTED_PLACE_ID = 9000000001" in res["luau"]
    with pytest.raises(RuntimeError, match="wrong place"):
        World(name="Demo Tycoon (SYNTHETIC)", place_id=5).run(res["luau"])
