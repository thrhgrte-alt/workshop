import json
import math
import random

import pytest

from setdress.core.style import load_style
from setdress.domain import compose, dress, export, geom, kit as K, scene as SC
from setdress.domain.mock_roblox import LuaError, MockRoblox
from setdress.hooks import mutate_kit, mutate_scene
from setdress.tools import load_kit


@pytest.fixture()
def kit(project):
    k, f = load_kit(project)
    assert not [x for x in f if x["severity"] == "error"]
    return k


def scene_file(project, name, muts=None):
    raw = json.loads((project.root / "examples" / "scenes" / f"{name}.json").read_text())
    return SC.normalize(mutate_scene(raw, muts or []))


def errs(findings):
    return {f["code"] for f in findings if f["severity"] == "error"}


# --- geometry ----------------------------------------------------------------------------------------------------------------------
def test_rotation_convention_matches_cframe_angles():
    assert geom.forward(0) == pytest.approx((0, 1)) and geom.forward(90) == pytest.approx((1, 0)) and geom.forward(-90) == pytest.approx((-1, 0))
    # CFrame.Angles(0, rad(yaw), 0) rotates (lx, lz) by x' = lx cos + lz sin, z' = -lx sin + lz cos
    for yaw in (0, 30, 90, 135, -60):
        t = math.radians(yaw)
        assert geom.rot(1, 2, yaw) == pytest.approx((math.cos(t) + 2 * math.sin(t), -math.sin(t) + 2 * math.cos(t)))
    assert geom.norm_yaw(270) == -90 and geom.norm_yaw(-180) == 180 and geom.norm_yaw(360) == 0
    assert geom.angle_diff(350, 10) == -20


def test_sat_overlap_matches_brute_force_sampling():
    """Randomised check of the oriented-box overlap test against dense point sampling."""
    rng = random.Random(7)

    def contains(poly, p):
        sign = 0
        for i in range(4):
            (x1, z1), (x2, z2) = poly[i], poly[(i + 1) % 4]
            cross = (x2 - x1) * (p[1] - z1) - (z2 - z1) * (p[0] - x1)
            s = 1 if cross > 0 else -1
            if sign and s != sign:
                return False
            sign = s
        return True

    for _ in range(120):
        a = geom.footprint_corners((0, 0), rng.uniform(-180, 180), (rng.uniform(1, 6), rng.uniform(1, 6)))
        b = geom.footprint_corners((rng.uniform(-5, 5), rng.uniform(-5, 5)), rng.uniform(-180, 180), (rng.uniform(1, 6), rng.uniform(1, 6)))
        brute = any(contains(a, (x / 4, z / 4)) and contains(b, (x / 4, z / 4)) for x in range(-48, 49) for z in range(-48, 49))
        sat = geom.overlap_depth(a, b) > 0
        if brute:
            assert sat  # sampling found a shared point: SAT must agree
        elif sat:
            assert geom.overlap_depth(a, b) < 0.5  # SAT-only overlaps are slivers thinner than the sampling pitch


def test_corner_pivot_offsets_the_footprint():
    centre = geom.footprint_corners((10, 10), 0, (4, 2), offset=(2, 1))
    assert min(p[0] for p in centre) == 10 and min(p[1] for p in centre) == 10
    rotated = geom.footprint_corners((10, 10), 90, (4, 2), offset=(2, 1))
    assert min(p[0] for p in rotated) == pytest.approx(10) and max(p[1] for p in rotated) == pytest.approx(10)


def test_segment_rect_and_intersections():
    r = geom.segment_rect((0, 0), (10, 0), 4)
    assert geom.polygon_area(r) == pytest.approx(14 * 4)
    box = geom.footprint_corners((5, 0), 0, (2, 2))
    assert geom.segment_polygon_intersects((0, 0), (10, 0), box) and not geom.segment_polygon_intersects((0, 5), (10, 5), box)


# --- kit ------------------------------------------------------------------------------------------------------------------------------
def test_kit_defaults_are_tracked_not_silent(project):
    raw = K.load_kit(project.root / "examples" / "kit" / "kit.yaml")
    k, f = K.normalize_kit(mutate_kit(raw, [{"op": "unset", "module": "barrel", "field": "color"}]))
    assert K.report(k)["unresolved"] == {"barrel": ["color"]}
    thin, f = K.normalize_kit({"modules": [{"id": "mystery", "footprint": [2, 2], "height": 2}]})
    assert thin["modules"][0]["_defaulted"] == ["pivot", "collision", "material", "color", "tags", "scale"] and "no_tags" in {x["code"] for x in f}


def test_pivots_and_size_classes(kit):
    mods = K.index(kit)
    assert mods["fireplace"]["size_class"] == "large" and mods["lantern"]["size_class"] == "small" and mods["chair"]["size_class"] == "medium"
    k2, _ = K.normalize_kit({"modules": [{"id": "a", "footprint": [4, 2], "height": 6, "pivot": "corner"}, {"id": "b", "footprint": [2, 2], "height": 4, "pivot": "center"}]})
    a, b = K.index(k2)["a"], K.index(k2)["b"]
    assert a["footprint_offset"] == [2, 1] and b["y_offset"] == -2


def test_kit_search_is_filtered_and_ranked(kit):
    assert K.search(kit, "wooden chair")[0]["id"] == "chair"
    assert {h["id"] for h in K.search(kit, tags_all=["container"], k=10)} == {"crate_small", "crate_tiny", "barrel"}
    assert K.search(kit, "zebra") == []
    with pytest.raises(ValueError, match="Did you mean"):
        K.get(kit, "chiar")


# --- scenes -----------------------------------------------------------------------------------------------------------------------------
def test_example_scenes_validate_as_intended(project, kit):
    assert not errs(SC.validate(scene_file(project, "tavern_corner"), kit))
    assert errs(SC.validate(scene_file(project, "tavern_blocked_path"), kit)) >= {"blocks_path", "blocks_sightline", "collision"}


def test_collision_needs_height_overlap_and_collision_flag(project, kit):
    base = scene_file(project, "tavern_bare", [{"op": "set_top", "field": "paths", "value": []}, {"op": "set_top", "field": "corridors", "value": []}])
    def with_(*items):
        return SC.normalize({**base, "instances": base["instances"] + list(items)})
    t = {"id": "t", "module": "table_round", "at": [10, 24]}
    crate_on = {"id": "c", "module": "crate_tiny", "at": [10, 24], "y": 3}
    crate_in = {"id": "c", "module": "crate_tiny", "at": [10, 24], "y": 1}
    rug = {"id": "r", "module": "rug", "at": [10, 24]}
    assert "collision" not in errs(SC.validate(with_(t, crate_on), kit))
    assert "collision" in errs(SC.validate(with_(t, crate_in), kit))
    assert "collision" not in errs(SC.validate(with_(t, rug), kit))  # rug has collision: false


def test_collisions_are_reported_once_per_pair(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "add", "value": {"id": "a", "module": "crate_small", "at": [10, 24]}}, {"op": "add", "value": {"id": "b", "module": "crate_small", "at": [11, 24]}}])
    assert sum(f["code"] == "collision" for f in SC.validate(sc, kit)) == 1


def test_sightline_blocking_depends_on_height(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "set_top", "field": "paths", "value": []}])
    lamp = {"id": "l", "module": "lantern", "at": [26, 16]}  # 1.5 high: below eye height
    pillar = {"id": "p", "module": "pillar", "at": [26, 16]}
    assert "blocks_sightline" not in errs(SC.validate(SC.normalize({**sc, "instances": sc["instances"] + [lamp]}), kit))
    assert "blocks_sightline" in errs(SC.validate(SC.normalize({**sc, "instances": sc["instances"] + [pillar]}), kit))


def test_plans_apply_invert_and_respect_locks(project, kit):
    sc = scene_file(project, "tavern_corner")
    plan = {"adds": [dress.make_instance(sc, kit, "crate_tiny", (36, 26), iid="extra_1")], "moves": [{"id": "table_001", "from": {"at": sc["instances"][2]["at"]}, "to": {"at": [13, 20]}}], "removes": ["chair_back"]}
    after = SC.apply_plan(sc, plan)
    assert [i["id"] for i in after["instances"]].count("extra_1") == 1 and "chair_back" not in {i["id"] for i in after["instances"]}
    back = SC.apply_plan(after, SC.invert_plan(sc, plan))
    assert SC.diff(sc, back)["unchanged"] and SC.scene_hash(sc) == SC.scene_hash(back)
    with pytest.raises(ValueError, match="locked"):
        SC.apply_plan(sc, {"adds": [], "moves": [{"id": "bar_001", "from": {}, "to": {"at": [1, 1]}}], "removes": []})
    assert sc == scene_file(project, "tavern_corner")  # apply_plan never mutates its input


# --- sockets ---------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("yaw", [0, 90, 180, -90])
def test_snapped_sockets_coincide_and_face_each_other(project, kit, yaw):
    sc = scene_file(project, "tavern_bare", [{"op": "set_top", "field": "paths", "value": []}, {"op": "set_top", "field": "corridors", "value": []},
                                              {"op": "add", "value": {"id": "t1", "module": "table_round", "at": [20, 15], "yaw": yaw}}])
    mods = K.index(kit)
    chair = dress.snap_to_socket(sc, kit, "t1", "seat_front", "chair", "front")
    t = dress.socket_world(sc["instances"][-1], mods["table_round"], "seat_front")
    c = dress.socket_world(chair, mods["chair"], "front")
    assert c["pos"] == pytest.approx(t["pos"]) and abs(geom.angle_diff(c["yaw"], t["yaw"] + 180)) < 1e-6


def test_socket_snapping_validates(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "add", "value": {"id": "t1", "module": "table_round", "at": [20, 15]}}])
    with pytest.raises(ValueError, match="incompatible"):
        dress.snap_to_socket(sc, kit, "t1", "top", "chair", "front")
    with pytest.raises(ValueError, match="no socket"):
        dress.snap_to_socket(sc, kit, "t1", "nope", "chair", "front")
    with pytest.raises(ValueError, match="unknown target"):
        dress.snap_to_socket(sc, kit, "ghost", "top", "lantern", "base")


def test_surface_sockets_keep_the_targets_yaw_and_height(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "add", "value": {"id": "t1", "module": "table_round", "at": [20, 15], "yaw": 90, "scale": 1.1}}])
    lamp = dress.snap_to_socket(sc, kit, "t1", "top", "lantern", "base")
    assert lamp["yaw"] == 90 and lamp["y"] == pytest.approx(3 * 1.1) and lamp["at"] == pytest.approx([20, 15])


# --- dressing ------------------------------------------------------------------------------------------------------------------------------
def test_dressing_is_deterministic_valid_and_never_touches_locked_pieces(project, kit):
    sc = scene_file(project, "tavern_bare")
    rules = {"density": 0.2, "orient": "face_wall", "exclude_tags": ["wall"]}
    a, b = dress.dress_region(sc, kit, rules, 9), dress.dress_region(sc, kit, rules, 9)
    assert a["plan"] == b["plan"] and a["plan"]["adds"]
    after = SC.apply_plan(sc, a["plan"])
    assert not errs(SC.validate(after, kit)) and not SC.check_locks(after, SC.lock_snapshot(sc))
    assert dress.dress_region(sc, kit, rules, 10)["plan"] != a["plan"]
    assert len(a["explain"]) == len(a["plan"]["adds"]) and all(e["id"] for e in a["explain"])


def test_face_wall_orientation_points_into_the_room(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "set_top", "field": "paths", "value": []}, {"op": "set_top", "field": "corridors", "value": []}])
    res = dress.dress_region(sc, kit, {"density": 0.3, "orient": "face_wall", "wall_distance": 4, "include_modules": ["bench"], "max_items": 25, "spacing": 0.5}, 2)
    west = [i for i in res["plan"]["adds"] if i["at"][0] <= 4 and 6 <= i["at"][1] <= 24]
    assert west and all(i["yaw"] == 90 for i in west)  # back to the west wall, facing +x


def test_focal_hierarchy_places_a_hero_module(project, kit):
    sc = scene_file(project, "tavern_bare", [{"op": "set_top", "field": "locked", "value": {}}, {"op": "remove", "id": "hearth_001"}])
    res = dress.dress_region(sc, kit, {"density": 0.0}, 1)
    hero = [i for i in res["plan"]["adds"] if i["module"] == "fireplace"]
    assert hero and math.dist(hero[0]["at"], [2, 15]) <= 10
    assert hero[0]["yaw"] == pytest.approx(geom.norm_yaw(math.degrees(math.atan2(2 - hero[0]["at"][0], 15 - hero[0]["at"][1]))), abs=100)


def test_repetition_controls(project, kit):
    sc = scene_file(project, "tavern_bare")
    res = dress.dress_region(sc, kit, {"density": 0.25, "include_modules": ["barrel", "crate_small", "chair", "plant_pot"], "max_items": 40}, 4)
    counts = {}
    for i in res["plan"]["adds"]:
        counts[i["module"]] = counts.get(i["module"], 0) + 1
    assert max(counts.values()) / len(res["plan"]["adds"]) <= 0.55


# --- composition -------------------------------------------------------------------------------------------------------------------------------
def test_largest_free_rectangle_is_exact():
    g = [[False] * 6 for _ in range(4)]
    assert compose.largest_free_rectangle(g) == 24
    g[1][2] = True
    assert compose.largest_free_rectangle(g) == 12 and compose.largest_free_rectangle([[True]]) == 0


def test_metrics_for_examples(project, kit):
    style = load_style(project)
    from setdress.core.style import check_ranges

    flagged = lambda name: {f["metric"] for f in check_ranges(compose.measure(scene_file(project, name), kit)["metrics"], style["ranges"]) if f["severity"] in ("warning", "error")}
    assert flagged("tavern_corner") == set()
    assert {"max_module_share", "adjacent_same_ratio"} <= flagged("tavern_all_barrels") and "coverage" in flagged("tavern_bare")


# --- export + mock --------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["primitives", "clone"])
def test_luau_runs_on_the_mock(project, kit, snapshot, mode):
    sc = scene_file(project, "tavern_corner")
    code = export.build_luau(sc, kit, mode=mode)
    assert export.lint(code) == [] and all(t not in code for t in export.FORBIDDEN)
    mock = MockRoblox(snapshot)
    if mode == "clone":
        mock.lua.eval("function(ws, names) local rs = Instance.new('Folder'); rs.Name = 'ReplicatedStorage'; rs.Parent = ws; local k = Instance.new('Folder'); k.Name = 'Kit'; k.Parent = rs; for _, n in pairs(names) do local m = Instance.new('Model'); m.Name = n; m.Parent = k end end")(
            mock.env.workspace, mock.lua.table_from(sorted({m['template'] for m in kit['modules']})))
        code = code.replace('resolveTarget("ReplicatedStorage.Kit")', 'resolveTarget("workspace.ReplicatedStorage.Kit")')
    report = json.loads(mock.run(code))
    root = next(c for c in mock.tree()["children"] if c["name"].startswith("AI_SetDress_"))
    assert report["status"] == "ok" and len(root["children"]) == len(sc["instances"])


def test_primitive_transforms_match_the_kit_geometry(project, kit, snapshot):
    sc = scene_file(project, "tavern_corner")
    rows = {r["id"]: r for r in export.primitive_rows(sc, kit)}
    t = rows["table_001"]
    assert t["size"] == [5, 3, 5] and t["position"][1] == pytest.approx(1.5)  # footprint x height x footprint; centre is half the height up
    fp = rows["hearth_001"]
    assert fp["yaw"] == 90 and fp["size"] == [8, 9, 3]
    mock = MockRoblox(snapshot)
    mock.run(export.build_luau(sc, kit))
    root = next(c for c in mock.tree()["children"] if c["name"].startswith("AI_SetDress_"))
    built = {c["name"]: c for c in root["children"]}
    assert built["hearth_001"]["props"]["Orientation"] == [0, 90, 0]
    assert built["hearth_001"]["props"]["Size"] == pytest.approx([8, 9, 3]) and built["table_001"]["props"]["Position"] == pytest.approx(t["position"])


def test_corner_pivot_primitives_are_centred(kit):
    k2, _ = K.normalize_kit({"modules": [{"id": "slab", "footprint": [4, 2], "height": 2, "pivot": "corner", "tags": ["x"]}]})
    sc = SC.normalize({"id": "s", "region": {"x": 0, "z": 0, "w": 20, "d": 20}, "instances": [{"id": "a", "module": "slab", "at": [5, 5], "yaw": 90}]})
    row = export.primitive_rows(sc, k2)[0]
    poly = SC.poly_of(sc["instances"][0], k2["modules"][0])
    cx = sum(p[0] for p in poly) / 4
    cz = sum(p[1] for p in poly) / 4
    assert (row["position"][0], row["position"][2]) == pytest.approx((cx, cz))


def test_rerun_and_foreign_and_unsafe_inputs(project, kit, snapshot):
    sc = scene_file(project, "tavern_corner")
    code = export.build_luau(sc, kit, target_path="workspace.Props")
    mock = MockRoblox(snapshot)
    mock.make_folder("Props")
    mock.run(code)
    mock.run(code)
    props = next(c for c in mock.tree()["children"] if c["name"] == "Props")
    assert len(props["children"]) == 1
    foreign = MockRoblox(snapshot)
    folder = foreign.make_folder("Props")
    foreign.make_folder("AI_SetDress_tavern_corner", folder)
    with pytest.raises(LuaError, match="refusing to replace"):
        foreign.run(code)
    for bad in ('workspace; os.exit()', 'workspace"] = nil --', ""):
        with pytest.raises(ValueError):
            export.build_luau(sc, kit, target_path=bad)
    sc["id"] = 'x"]=nil'
    with pytest.raises(ValueError, match="must match"):
        export.build_luau(sc, kit)


def test_mock_is_strict(snapshot):
    m = MockRoblox(snapshot)
    for lua, needle in [("Instance.new('ParticleEmitter')", "Unable to create"), ("local p = Instance.new('Part'); p.Glow = 1", "not a valid member"),
                        ("local p = Instance.new('Part'); p.Orientation = 5", "Vector3 expected"), ("local p = Instance.new('Part'); p:PivotTo(5)", "expects a CFrame")]:
        with pytest.raises(LuaError, match=needle):
            m.run(lua)
