"""Generated Designer scripts: syntax, determinism, and end-to-end execution against a FAKE Designer API.

Passing here means the script logic is coherent against the API shape it assumes - it does NOT prove that
shape matches the real Designer. See README "Verify on your install".
"""

import json

import fake_designer
import pytest

from sdai.domain import graphspec, scriptgen
from sdai.domain.catalog import diff_against_probe, load_catalog


@pytest.fixture()
def catalog(project):
    return load_catalog(project.root / "recipes" / "node_catalog.yaml")


def plan_for(project, catalog, rid="stylized_stone_wall", **params):
    r = graphspec.load_recipe(project.root / "recipes" / f"{rid}.yaml")
    return graphspec.compile_recipe(r, catalog, params or None)


def test_scripts_compile_and_have_no_unfilled_placeholders(project, catalog, tmp_path):
    plan = plan_for(project, catalog)
    for script in (scriptgen.build_create_script(plan, str(tmp_path / "r.json"), str(tmp_path / "x.sbs")),
                   scriptgen.build_create_script(plan, str(tmp_path / "r.json")),
                   scriptgen.build_inspect_script(str(tmp_path / "i.json")),
                   scriptgen.build_probe_script(catalog, str(tmp_path / "p.json"))):
        scriptgen.check_script_syntax(script)
        assert "__PLAN" not in script and "__ATOMIC__" not in script and "__SAVE_PATH__" not in script


def test_scripts_are_deterministic_and_hash_tracks_content(project, catalog, tmp_path):
    a = plan_for(project, catalog)
    assert scriptgen.build_create_script(a, "r.json") == scriptgen.build_create_script(plan_for(project, catalog), "r.json")
    assert scriptgen.plan_hash(a) != scriptgen.plan_hash(plan_for(project, catalog, bevel_softness=5.0))


def test_layout_places_nodes_by_depth_without_overlap(project, catalog):
    laid = scriptgen.layout(plan_for(project, catalog))
    spots = [(n["x"], n["y"]) for n in laid["nodes"]] + [(o["x"], o["y"]) for o in laid["outputs"]]
    assert len(spots) == len(set(spots))
    by = {n["id"]: n for n in laid["nodes"]}
    for c in laid["connections"]:
        assert by[c["from_node"]]["x"] < by[c["to_node"]]["x"]


def test_build_script_runs_against_fake_designer(project, catalog, tmp_path):
    plan = plan_for(project, catalog, brick_columns=12)
    result = tmp_path / "out" / "result.json"
    with fake_designer.install(catalog) as fake:
        fake_designer.run_script(scriptgen.build_create_script(plan, str(result), str(tmp_path / "wall.sbs")))
        graph = fake.graphs[-1]
        saved = fake.manager.saved
    data = json.loads(result.read_text())
    assert data["status"] == "ok", data
    assert data["connections"] == len(plan["connections"] + [{}] * len(plan["outputs"])) - len(plan["outputs"])
    assert len([n for n in data["created_nodes"] if n.startswith("output:")]) == 4
    assert graph.ident == plan["graph_name"] and saved == [str(tmp_path / "wall.sbs")]
    tiles = next(n for n in graph.nodes if n.definition == "tile_generator")
    assert tiles.values["x_amount"] == 12
    outputs = {n.annotations["identifier"] for n in graph.nodes if n.definition == "sbs::compositing::output"}
    assert outputs == {"baseColor", "normal", "roughness", "height"}
    assert data["plan_hash"] == scriptgen.plan_hash(plan)


@pytest.mark.parametrize("rid", ["hand_painted_wood_planks", "stylized_pebble_ground"])
def test_other_recipes_build_too(project, catalog, tmp_path, rid):
    result = tmp_path / "r.json"
    with fake_designer.install(catalog):
        fake_designer.run_script(scriptgen.build_create_script(plan_for(project, catalog, rid), str(result)))
    assert json.loads(result.read_text())["status"] == "ok"


def test_missing_library_graph_is_reported_not_swallowed(project, catalog, tmp_path):
    result = tmp_path / "r.json"
    with fake_designer.install(catalog, missing_library=("Tile Generator",)):
        fake_designer.run_script(scriptgen.build_create_script(plan_for(project, catalog), str(result)))
    data = json.loads(result.read_text())
    assert data["status"] == "error" and "Tile Generator" in data["errors"][0] and "traceback" in data


def test_wrong_parameter_id_is_reported_with_the_fix(project, catalog, tmp_path):
    plan = plan_for(project, catalog)
    next(n for n in plan["nodes"] if n["id"] == "soften")["params"]["strength"] = 1.0  # not a real parameter
    result = tmp_path / "r.json"
    with fake_designer.install(catalog):
        fake_designer.run_script(scriptgen.build_create_script(plan, str(result)))
    err = json.loads(result.read_text())["errors"][0]
    assert "no input property 'strength'" in err and "catalog-diff" in err


def test_rejected_connection_is_an_error(project, catalog, tmp_path):
    result = tmp_path / "r.json"
    with fake_designer.install(catalog, fail_connections=True):
        fake_designer.run_script(scriptgen.build_create_script(plan_for(project, catalog), str(result)))
    assert "rejected by Designer" in json.loads(result.read_text())["errors"][0]


def test_probe_script_against_fake_matches_catalog_then_detects_drift(project, catalog, tmp_path):
    result = tmp_path / "probe.json"
    with fake_designer.install(catalog):
        fake_designer.run_script(scriptgen.build_probe_script(catalog, str(result)))
    probe = json.loads(result.read_text())
    assert probe["status"] == "ok" and probe["designer_version"] == "fake-1.0"
    assert diff_against_probe(catalog, probe)["clean"], diff_against_probe(catalog, probe)["problems"]
    probe["atomic"]["sbs::compositing::blur"]["params"] = ["strength"]
    diff = diff_against_probe(catalog, probe)
    assert not diff["clean"] and diff["problems"][0]["node"] == "blur"
    assert diff["problems"][0]["detail"][0]["available"] == ["strength"]


def test_probe_reports_nodes_that_cannot_be_created(project, catalog, tmp_path):
    result = tmp_path / "probe.json"
    with fake_designer.install(catalog, missing_library=("Cells 1",)):
        fake_designer.run_script(scriptgen.build_probe_script(catalog, str(result)))
    diff = diff_against_probe(catalog, json.loads(result.read_text()))
    assert [p["node"] for p in diff["problems"]] == ["cells_1"]


def test_inspect_script_snapshots_a_built_graph(project, catalog, tmp_path):
    built, snap = tmp_path / "b.json", tmp_path / "snap.json"
    with fake_designer.install(catalog):
        fake_designer.run_script(scriptgen.build_create_script(plan_for(project, catalog), str(built)))
        fake_designer.run_script(scriptgen.build_inspect_script(str(snap)))
    data = json.loads(snap.read_text())
    assert data["status"] == "ok" and data["graph"] == "stylized_stone_wall"
    assert len(data["nodes"]) == 11 + 4 and data["connections"]


def test_inspect_script_reports_no_open_graph(catalog, tmp_path):
    snap = tmp_path / "snap.json"
    with fake_designer.install(catalog):
        fake_designer.run_script(scriptgen.build_inspect_script(str(snap)))
    assert "no graph is open" in json.loads(snap.read_text())["errors"][0]
