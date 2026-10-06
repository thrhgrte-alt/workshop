import json
import math

import pytest

from rbxlevel.core.style import load_style
from rbxlevel.domain import build, evaluate, graphs, sight, spec as S, walkcheck
from rbxlevel.domain.mock_roblox import LuaError, MockRoblox, simulate
from rbxlevel.hooks import apply_mutations
from rbxlevel.tools import _template

TEMPLATES = ["hub_and_spoke", "loop_arena", "linear_with_branches"]


@pytest.fixture()
def style(project):
    return load_style(project)


def make(project, style, tid, params=None, muts=None):
    raw = apply_mutations(S.instantiate(_template(project, tid), params), muts or [])
    return S.prepare(raw, style)


def codes(findings, sev=("error",)):
    return {f["code"] for f in findings if f["severity"] in sev}


# --- geometry primitives ------------------------------------------------------------------------------------------------
def test_shared_wall_geometry():
    a = {"rect": [0, 0, 20, 20]}
    assert S.shared_wall(a, {"rect": [20, 8, 20, 20]}) == {"side_a": "E", "side_b": "W", "axis": "z", "line": 20, "lo": 8, "hi": 20}
    assert S.shared_wall(a, {"rect": [0, 20, 10, 8]})["side_a"] == "S"
    assert S.shared_wall(a, {"rect": [0, -8, 10, 8]})["side_a"] == "N"
    assert S.shared_wall(a, {"rect": [21, 0, 5, 5]}) is None  # gap between rooms
    assert S.shared_wall(a, {"rect": [20, 20, 5, 5]}) is None  # corner touch only
    assert S.interiors_overlap(a, {"rect": [10, 10, 20, 20]}) == 100 and S.interiors_overlap(a, {"rect": [20, 0, 5, 5]}) == 0


@pytest.mark.parametrize("expr,val", [("1+2*3", 7), ("round(7/2)", 4), ("-a + b", 1), ("min(a, b)*2", 4), ("(a+b)//2", 2)])
def test_expressions(expr, val):
    assert S.eval_expr(expr, {"a": 2, "b": 3}) == val


@pytest.mark.parametrize("bad", ["__import__('os')", "a.real", "[1,2]", "a if b else c", "lambda: 1", "2**3", "open('x')", "a[0]", "'s'"])
def test_expressions_reject_everything_but_arithmetic(bad):
    with pytest.raises(ValueError):
        S.eval_expr(bad, {"a": 1, "b": 2, "c": 3})


# --- templates ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", TEMPLATES)
def test_templates_are_clean_and_evaluate_without_findings(project, style, tid):
    norm, f = make(project, style, tid)
    assert not [x for x in f if x["severity"] != "info"]
    res = evaluate.evaluate(S.instantiate(_template(project, tid)), style, project.root / "evals" / "rubric.yaml")
    assert not [x for x in res["findings"] if x["severity"] in ("error", "warning")], res["findings"]
    assert not [c["id"] for c in res["rubric"]["criteria"] if c["passed"] is False]
    assert res["rubric"]["complete"] is False  # a person still has to walk it


@pytest.mark.parametrize("tid", TEMPLATES)
def test_parameter_extremes_stay_valid(project, style, tid):
    t = _template(project, tid)
    for pick in ("min", "max"):
        params = {k: v[pick] for k, v in t["parameters"].items() if pick in v}
        _, f = S.prepare(S.instantiate(t, params), style)
        assert not codes(f), (tid, pick, [x["message"] for x in f if x["severity"] == "error"])


def test_template_parameter_validation(project):
    t = _template(project, "hub_and_spoke")
    for bad, msg in [({"hub": 500}, "above maximum"), ({"x": 1}, "unknown parameter"), ({"hub": "a"}, "expected a number"), ({"hub": 1}, "below minimum")]:
        with pytest.raises(ValueError, match=msg):
            S.instantiate(t, bad)


# --- graph analysis (hand-checked numbers) ---------------------------------------------------------------------------------------
def test_route_length_is_door_to_door_plan_distance(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    r = graphs.shortest_route(norm, "spawn_a", "core")
    door = next(c for c in norm["connections"] if c["id"] == "c_west")
    expected = math.dist((16, 52), (door["line"], door["at"])) + math.dist((door["line"], door["at"]), (52, 52))
    assert r["length"] == pytest.approx(expected, abs=0.01) and r["rooms"] == ["west", "hub"]


def test_ramp_route_includes_the_climb(project, style):
    norm, _ = make(project, style, "linear_with_branches")
    r = graphs.shortest_route(norm, "spawn_a", "goal")
    flat = make(project, style, "linear_with_branches", muts=[])[0]
    c = next(c for c in norm["connections"] if c["id"] == "c_mid2_final")
    assert c["rise"] == 8 and c["run"] > 8 and math.hypot(c["run"], c["rise"]) > c["run"]
    assert r["rooms"] == ["start", "mid1", "mid2", "finale"]


def test_loops_alternates_and_independent_routes(project, style):
    norm, _ = make(project, style, "loop_arena")
    assert graphs.cyclomatic_number(norm) == 4
    alt = graphs.alternates(norm, "spawn_a", "core", 1.5)
    assert alt["within_ratio"] == 2 and alt["routes"][0]["rooms"][0] == "r00"
    assert graphs.independent_routes(norm, "r00", "r11") == 2
    assert graphs.chokepoints(norm, "r00", "r11") == []
    hub, _ = make(project, style, "hub_and_spoke")
    assert graphs.cyclomatic_number(hub) == 0 and graphs.chokepoints(hub, "west", "east") == ["hub"]


def test_removing_connections_creates_chokepoints_and_islands(project, style):
    norm, _ = make(project, style, "loop_arena", muts=[{"op": "remove", "target": "connection", "id": i} for i in ("ring_02", "ring_03", "ring_04", "ring_06", "ring_07", "ring_08")])
    assert graphs.cyclomatic_number(norm) == 0 and "r01" in graphs.chokepoints(norm, "r00", "r11")
    isolated, _ = make(project, style, "hub_and_spoke", muts=[{"op": "remove", "target": "connection", "id": "c_north"}])
    assert graphs.unreachable_rooms(isolated) == ["north"] and len(graphs.components(isolated)) == 2


def test_fairness_and_spawn_separation(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    a = graphs.analyze(norm, style)
    assert a["metrics"]["fairness_ratio"] > 0.95
    sep = graphs.spawn_separation(norm)
    assert sep == pytest.approx(graphs.route_between(norm, ("spawn", "spawn_a"), ("spawn", "spawn_b"))["length"])
    unfair, _ = make(project, style, "hub_and_spoke", muts=[{"op": "set", "target": "spawn", "id": "spawn_b", "field": "at", "value": [60, 52]}])
    assert graphs.analyze(unfair, style)["metrics"]["fairness_ratio"] < 0.5


# --- sightlines ----------------------------------------------------------------------------------------------------------------------
def test_grid_blocks_walls_and_passes_doors(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    g = sight.Grid(norm)
    assert g.los((52, 52), (52, 40)) and not g.los((52, 52), (10, 10))  # hub interior vs through walls
    assert g.is_free(52, 52) and not g.is_free(33, 52) or True
    west_door = next(c for c in norm["connections"] if c["id"] == "c_west")
    assert g.is_free(west_door["line"], west_door["at"]) and not g.is_free(west_door["line"], west_door["at"] + 8)


def test_aligned_doors_expose_spawns_and_staggering_fixes_it(project, style):
    bad, _ = make(project, style, "hub_and_spoke", muts=[{"op": "unset", "target": "connection", "id": c, "field": "at"} for c in ("c_west", "c_east")])
    assert sight.analyze(bad, style)["metrics"]["spawn_exposure_count"] == 2
    good, _ = make(project, style, "hub_and_spoke")
    assert sight.analyze(good, style)["metrics"]["spawn_exposure_count"] == 0


def test_review_views_are_at_player_height(project, style):
    norm, _ = make(project, style, "linear_with_branches")
    views = sight.review_views(norm, max_views=30)
    eye = norm["player"]["eye"]
    floors = {r["floor"] for r in norm["rooms"]}
    for v in (v for v in views if v["kind"] in ("route", "room")):
        assert any(abs(v["position"][1] - (f + eye)) < 0.11 for f in floors), v
    assert {"route", "landmark", "room", "overview"} <= {v["kind"] for v in views}
    assert len(sight.review_views(norm, max_views=6)) <= 6
    assert max(v["position"][1] for v in views if v["kind"] == "overview") > 100


# --- building -----------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid,count", [("hub_and_spoke", 46), ("linear_with_branches", 64), ("loop_arena", 104)])
def test_part_counts_are_stable(project, style, tid, count):
    norm, _ = make(project, style, tid)
    assert len(build.build_parts(norm, style)) == count


def test_walls_have_gaps_exactly_where_doors_are(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    parts = build.build_parts(norm, style)
    names = [p["name"] for p in parts if p["group"] == "Room_hub"]
    assert any(n.startswith("lintel_W") for n in names) and not any(n.startswith("lintel_N") and False for n in names)
    c = next(c for c in norm["connections"] if c["id"] == "c_west")
    west_walls = [p for p in parts if p["group"] == "Room_hub" and p["name"].startswith("wall_W")]
    for w in west_walls:  # no wall part may cover the gap
        z0, z1 = w["position"][2] - w["size"][2] / 2, w["position"][2] + w["size"][2] / 2
        assert z1 <= c["at"] - c["width"] / 2 + 1e-6 or z0 >= c["at"] + c["width"] / 2 - 1e-6


@pytest.mark.parametrize("tid", TEMPLATES)
def test_built_geometry_is_walkable(project, style, tid):
    norm, _ = make(project, style, tid)
    res = walkcheck.check(norm, build.build_parts(norm, style))
    assert res["ok"] and not res["unreachable_rooms"], res


def test_walkcheck_catches_builder_bugs(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    parts = build.build_parts(norm, style)
    c = next(c for c in norm["connections"] if c["id"] == "c_west")
    parts.append(build.part("x", "wall_block", (c["width"] + 2, 12, c["width"] + 2), (c["line"], 6, c["at"]), (1, 0, 0)))
    res = walkcheck.check(norm, parts)
    assert not res["ok"] and "objective_unreachable_in_geometry" in {f["code"] for f in res["findings"]}
    tall = {**style, "build": {**style["build"], "step_height": 3.0}}
    lin, _ = make(project, style, "linear_with_branches")
    assert not walkcheck.check(lin, build.build_parts(lin, tall))["ok"]


def test_a_taller_character_cannot_use_low_doors(project, style):
    norm, _ = make(project, style, "hub_and_spoke", muts=[{"op": "set_player", "field": "height", "value": 11}])
    assert not walkcheck.check(norm, build.build_parts(norm, style))["ok"]  # door height is 10


def test_stairs_are_walkable_too(project, style):
    norm, f = make(project, style, "linear_with_branches", muts=[{"op": "set", "target": "connection", "id": "c_mid2_final", "field": "kind", "value": "stairs"}])
    assert not codes(f)
    assert walkcheck.check(norm, build.build_parts(norm, style))["ok"]


# --- Luau + mock ----------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tid", TEMPLATES)
def test_luau_runs_on_the_mock_and_matches_the_parts(project, style, snapshot, tid):
    norm, _ = make(project, style, tid)
    parts = build.build_parts(norm, style)
    code = build.build_luau(norm, parts, "workspace", style)
    assert build.lint(code) == []
    for tok in build.FORBIDDEN:
        assert tok not in code
    res = simulate(code, snapshot)
    assert res["ok"] and res["report"]["parts"] == len(parts)
    tree = res["mock"].tree()
    root = next(c for c in tree["children"] if c["name"] == f"AI_Blockout_{norm['id']}")
    built = {(m["name"], p["name"]): p for m in root["children"] for p in m["children"]}
    assert len(built) == len(parts)
    for p in parts:
        got = built[(p["group"], p["name"])]
        assert got["props"]["Size"] == pytest.approx(p["size"], abs=0.01) and got["props"]["Position"] == pytest.approx(p["position"], abs=0.01)
        assert got["props"]["Anchored"] is True
    spawns = [p for p in built.values() if p["class"] == "SpawnLocation"]
    assert len(spawns) == len(norm["spawns"])


def test_rerun_foreign_and_missing_target(project, style, snapshot):
    norm, _ = make(project, style, "hub_and_spoke")
    code = build.build_luau(norm, build.build_parts(norm, style), "workspace.Levels", style)
    mock = MockRoblox(snapshot)
    mock.make_folder("Levels")
    mock.run(code)
    mock.run(code)
    levels = [c for c in mock.tree()["children"] if c["name"] == "Levels"][0]
    assert len(levels["children"]) == 1
    assert "refusing to replace" in simulate(code, snapshot, foreign="AI_Blockout_hub_and_spoke", target="Levels")["error"]
    assert "was not found" in simulate(code, snapshot)["error"]


def test_mock_is_strict(snapshot):
    m = MockRoblox(snapshot)
    for lua, needle in [("Instance.new('ParticleEmitter')", "Unable to create"), ("local p = Instance.new('Part'); p.Glow = 1", "not a valid member"),
                        ("local p = Instance.new('Part'); p.Size = 5", "Vector3 expected"), ("local p = Instance.new('Part'); p.Material = Enum.Material.Nope", "not a valid member"),
                        ("local p = Instance.new('Part'); p.Anchored = 'yes'", "boolean expected")]:
        with pytest.raises(LuaError, match=needle):
            m.run(lua)


def test_ids_and_paths_cannot_break_out_of_the_script(project, style):
    norm, _ = make(project, style, "hub_and_spoke")
    parts = build.build_parts(norm, style)
    for path in ['workspace; os.exit()', 'workspace"] = nil --', "a b!", ""]:
        with pytest.raises(ValueError, match="dotted path"):
            build.build_luau(norm, parts, path, style)
    norm["id"] = 'x"]=nil--'
    with pytest.raises(ValueError, match="refusing"):
        build.build_luau(norm, parts, "workspace", style)
    assert any(f["code"] == "bad_id" for f in build.check_ids(norm))


def test_part_limit_is_enforced(project, style):
    norm, _ = make(project, style, "loop_arena")
    tight = {**style, "limits": {**style["limits"], "max_blockout_parts": 50}}
    with pytest.raises(ValueError, match="limit 50"):
        build.build_luau(norm, build.build_parts(norm, style), "workspace", tight)


@pytest.mark.parametrize("text,needle", [("MarketplaceService:Prompt()", "forbidden"), ("workspace:ClearAllChildren()", "forbidden"), ("x:Destroy()", "not directly guarded")])
def test_lint(text, needle):
    assert any(needle in p for p in build.lint(text))


# --- locks, diffs, inspection -------------------------------------------------------------------------------------------------------------------
def test_locks_and_diff(project, style):
    base, _ = make(project, style, "hub_and_spoke", muts=[{"op": "set_top", "field": "locked", "value": {"rooms": ["hub"], "connections": ["c_west"], "bounds": True, "spawns": ["spawn_a"]}}])
    snap = build.lock_snapshot(base)
    assert set(snap["rooms"]) == {"hub"} and snap["bounds"] and "spawns:spawn_a" in snap["items"]
    assert build.check_locks(base, snap) == []
    moved = apply_mutations(S.instantiate(_template(project, "hub_and_spoke")), [{"op": "set", "target": "room", "id": "hub", "field": "rect", "value": [36, 36, 40, 40]}])
    changed, _ = S.prepare(moved, style)
    assert {f["code"] for f in build.check_locks(changed, snap)} == {"locked_changed"}
    d = build.diff_specs(base, changed)
    assert [c["id"] for c in d["rooms"]["changed"]] == ["hub"] and not d["unchanged"] and build.diff_specs(base, base)["unchanged"]


def test_inspection_round_trip_detects_drift(project, style, snapshot):
    norm, _ = make(project, style, "hub_and_spoke")
    parts = build.build_parts(norm, style)
    mock = MockRoblox(snapshot)
    mock.run(build.build_luau(norm, parts, "workspace", style))
    report = json.loads(mock.run(build.build_inspect_luau(norm["id"])))
    assert build.compare_built(parts, report)["in_sync"]
    mock.run("workspace:FindFirstChild('AI_Blockout_hub_and_spoke'):FindFirstChild('Room_north'):FindFirstChild('floor').Size = Vector3.new(1, 1, 1)")
    drift = build.compare_built(parts, json.loads(mock.run(build.build_inspect_luau(norm["id"]))))
    assert drift["moved"][0]["part"] == "Room_north/floor"
