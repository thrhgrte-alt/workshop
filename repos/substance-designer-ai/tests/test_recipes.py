import pytest
import yaml

from sdai.domain import graphspec
from sdai.domain.catalog import load_catalog

RECIPES = ["stylized_stone_wall", "hand_painted_wood_planks", "stylized_pebble_ground"]
REQUIRED = ("baseColor", "normal", "roughness", "height")


@pytest.fixture()
def catalog(project):
    return load_catalog(project.root / "recipes" / "node_catalog.yaml")


def recipe(project, rid):
    return graphspec.load_recipe(project.root / "recipes" / f"{rid}.yaml")


def errors(findings):
    return [f for f in findings if f["severity"] == "error"]


def codes(findings):
    return {f["code"] for f in findings}


@pytest.mark.parametrize("rid", RECIPES)
def test_shipped_recipes_are_valid_and_compile(project, catalog, rid):
    r = recipe(project, rid)
    assert errors(graphspec.validate_recipe(r, catalog, required_outputs=REQUIRED)) == []
    plan = graphspec.compile_recipe(r, catalog, required_outputs=REQUIRED)
    assert {o["usage"] for o in plan["outputs"]} >= set(REQUIRED)
    order = [n["id"] for n in plan["nodes"]]
    for c in plan["connections"]:  # topological: every connection goes forward
        assert order.index(c["from_node"]) < order.index(c["to_node"])


@pytest.mark.parametrize("rid", RECIPES)
def test_every_parameter_extreme_still_compiles(project, catalog, rid):
    """Recipe parameters must never be able to drive a node outside its own allowed range."""
    r = recipe(project, rid)
    for pick in ("min", "max"):
        overrides = {k: v[pick] for k, v in r["parameters"].items() if pick in v}
        graphspec.compile_recipe(r, catalog, overrides)


def test_unverified_nodes_are_always_reported(project, catalog):
    plan = graphspec.compile_recipe(recipe(project, "stylized_stone_wall"), catalog)
    assert "blur" in plan["unverified_nodes"] and plan["warnings"]


def test_parameter_mapping_scale_offset_and_int_rounding(project, catalog):
    plan = graphspec.compile_recipe(recipe(project, "stylized_stone_wall"), catalog,
                                    {"height_contrast": 0.5, "brick_columns": 9})
    shape = next(n for n in plan["nodes"] if n["id"] == "shape")
    assert shape["params"]["level_in_low"] == pytest.approx(0.2)
    assert shape["params"]["level_in_high"] == pytest.approx(0.8)
    tiles = next(n for n in plan["nodes"] if n["id"] == "tiles")
    assert tiles["params"]["x_amount"] == 9 and isinstance(tiles["params"]["x_amount"], int)
    blend = next(n for n in plan["nodes"] if n["id"] == "height_mix")
    assert blend["params"]["blendingmode"] == {"name": "add", "value": 1}


def test_defaults_applied_and_overrides_validated(project):
    r = recipe(project, "stylized_stone_wall")
    values = graphspec.resolve_parameters(r)
    assert values["brick_columns"] == 6
    for bad, msg in [({"bevel_softness": 99}, "above maximum"), ({"brick_columns": 1}, "below minimum"),
                     ({"brick_columns": 2.5}, "integer"), ({"nope": 1}, "unknown parameter"),
                     ({"stone_color_light": [2, 0, 0]}, "0-1")]:
        with pytest.raises(ValueError, match=msg):
            graphspec.resolve_parameters(r, bad)


def test_all_problems_are_reported_together(project):
    with pytest.raises(ValueError) as exc:
        graphspec.resolve_parameters(recipe(project, "stylized_stone_wall"), {"bevel_softness": 99, "brick_rows": 0})
    assert "bevel_softness" in str(exc.value) and "brick_rows" in str(exc.value)


FIXTURES = {
    "cycle.yaml": "cycle", "unknown_node.yaml": "unknown_node", "type_mismatch.yaml": "type_mismatch",
    "duplicate_input.yaml": "duplicate_input", "param_range.yaml": "param_range",
    "bad_param_ref.yaml": "unknown_param_ref", "missing_input.yaml": "missing_input",
}


@pytest.mark.parametrize("name,code", FIXTURES.items())
def test_invalid_fixtures_are_rejected_with_the_right_code(project, catalog, name, code):
    r = graphspec.load_recipe(project.root / "evals" / "fixtures" / name)
    assert code in codes(errors(graphspec.validate_recipe(r, catalog)))
    with pytest.raises(ValueError, match="invalid"):
        graphspec.compile_recipe(r, catalog)


def test_type_mismatch_suggests_the_fix(project, catalog):
    r = graphspec.load_recipe(project.root / "evals" / "fixtures" / "type_mismatch.yaml")
    msg = next(f["message"] for f in graphspec.validate_recipe(r, catalog) if f["code"] == "type_mismatch")
    assert "grayscale_conversion" in msg


def test_required_outputs_come_from_the_style(project, catalog):
    r = graphspec.load_recipe(project.root / "evals" / "fixtures" / "missing_output.yaml")
    assert "missing_output" in codes(graphspec.validate_recipe(r, catalog, required_outputs=("baseColor", "normal")))
    assert "missing_output" not in codes(graphspec.validate_recipe(r, catalog))


def test_graph_name_and_resolution_are_validated(project, catalog):
    r = recipe(project, "stylized_stone_wall")
    assert "bad_graph_name" in codes(graphspec.validate_recipe(r, catalog, graph_name="Bad Name!"))
    r2 = {**r, "resolution": 1000}
    assert "bad_resolution" in codes(graphspec.validate_recipe(r2, catalog))


def test_dangling_nodes_warn_but_do_not_block(project, catalog):
    r = graphspec.load_recipe(project.root / "evals" / "fixtures" / "dangling.yaml")
    f = graphspec.validate_recipe(r, catalog)
    assert "dangling_node" in codes(f) and not errors(f)


def test_plan_is_deterministic(project, catalog):
    r = recipe(project, "stylized_pebble_ground")
    a = graphspec.compile_recipe(r, catalog, {"pebble_density": 20})
    b = graphspec.compile_recipe(r, catalog, {"pebble_density": 20})
    assert a == b


def test_catalog_rejects_malformed_entries(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text(yaml.safe_dump({"nodes": {"x": {"source": "atomic", "inputs": [], "outputs": []}}}))
    with pytest.raises(ValueError, match="definition"):
        load_catalog(bad)
    bad.write_text(yaml.safe_dump({"nodes": {"x": {"source": "library", "inputs": [], "outputs": []}}}))
    with pytest.raises(ValueError, match="label"):
        load_catalog(bad)
