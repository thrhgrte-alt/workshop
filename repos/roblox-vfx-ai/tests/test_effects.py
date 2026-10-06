import json
from pathlib import Path

import pytest

from rbxvfx.domain import api, effects
from rbxvfx.hooks import apply_mutations

RECIPES = ["arcane_burst", "ember_aura", "energy_beam", "sprint_trail"]


def recipe(project, rid):
    return effects.load_recipe(project.root / "recipes" / f"{rid}.yaml")


def err_codes(exc):
    return str(exc.value)


@pytest.mark.parametrize("rid", RECIPES)
def test_shipped_recipes_build_and_validate(project, rid):
    plan = effects.build_plan(recipe(project, rid))
    assert plan["root_name"] == f"AIEffect_{rid}" and plan["instances"]
    assert not [f for f in effects.validate_plan(plan) if f["severity"] == "error"]


@pytest.mark.parametrize("rid", RECIPES)
def test_parameter_extremes_still_build(project, rid):
    """Every declared min/max must yield a valid effect: ranges can never push a property out of bounds."""
    r = recipe(project, rid)
    for pick in ("min", "max"):
        overrides = {k: v[pick] for k, v in r["parameters"].items() if pick in v}
        effects.build_plan(r, overrides)


def test_snapshot_is_provenanced_and_covers_effect_classes(snapshot):
    assert snapshot["source"].startswith("https://github.com/Roblox/creator-docs") and snapshot["generated_at"]
    assert set(api.ALLOWED_CLASSES) <= set(snapshot["classes"])
    assert api.properties("PointLight")["Range"]["type"] == "float"
    assert "Brightness" in api.properties("PointLight")  # inherited from Light
    assert "Name" in api.properties("ParticleEmitter")  # inherited from Instance
    assert "Sphere" in api.enum_values("ParticleEmitterShape")


def test_encoding_is_typed(project):
    plan = effects.build_plan(recipe(project, "arcane_burst"))
    sparks = next(i for i in plan["instances"] if i["id"] == "sparks")["properties"]
    assert sparks["Lifetime"] == {"t": "NumberRange", "min": 0.35, "max": 0.7}
    assert sparks["Size"]["t"] == "NumberSequence" and sparks["Size"]["keys"][0] == [0.0, 0.0, 0.0]
    assert sparks["Color"]["t"] == "ColorSequence" and len(sparks["Color"]["keys"]) == 3
    assert sparks["Enabled"] is False and sparks["Rate"] == 0.0
    assert "Texture" not in sparks  # empty texture parameter means "use Roblox's default particle"


def test_parameters_flow_into_values_and_emit_counts(project):
    plan = effects.build_plan(recipe(project, "arcane_burst"), {"intensity": 2.0, "speed": 20, "size": 2.0})
    sparks = next(i for i in plan["instances"] if i["id"] == "sparks")["properties"]
    assert plan["emit"] == {"sparks": 48, "flash": 2}
    assert sparks["Speed"] == {"t": "NumberRange", "min": 10.0, "max": 20.0}
    assert max(k[1] for k in sparks["Size"]["keys"]) == 2.0
    assert plan["duration_estimate"] == 0.7


def test_loop_effects_have_no_duration(project):
    assert effects.build_plan(recipe(project, "ember_aura"))["duration_estimate"] is None


def test_parameter_validation_reports_everything(project):
    r = recipe(project, "arcane_burst")
    with pytest.raises(ValueError) as exc:
        effects.resolve_parameters(r, {"size": 99, "intensity": 0, "nope": 1, "color_primary": [2, 0, 0]})
    msg = str(exc.value)
    for needle in ("size", "intensity", "unknown parameter 'nope'", "color_primary"):
        assert needle in msg


def mutated(project, rid, *muts, **kw):
    return effects.build_plan(apply_mutations(recipe(project, rid), list(muts)), **kw)


@pytest.mark.parametrize("mut,code", [
    ({"op": "set_property", "instance": "sparks", "property": "Glowiness", "value": 1}, "no property 'Glowiness'"),
    ({"op": "set_property", "instance": "sparks", "property": "Drag", "value": "fast"}, "expected a number"),
    ({"op": "set_property", "instance": "sparks", "property": "Shape", "value": "Triangle"}, "not a member of Enum.ParticleEmitterShape"),
    ({"op": "set_property", "instance": "sparks", "property": "Transparency", "value": {"sequence": [[0.2, 1], [1, 1]]}}, "sequence_bounds"),
    ({"op": "set_property", "instance": "sparks", "property": "Transparency", "value": {"sequence": [[0, 1], [0.5, 0], [0.5, 1], [1, 1]]}}, "sequence_order"),
    ({"op": "set_property", "instance": "sparks", "property": "Transparency", "value": {"sequence": [[0, 2], [1, 1]]}}, "out_of_range"),
    ({"op": "set_property", "instance": "sparks", "property": "Lifetime", "value": [2, 1]}, "range_order"),
    ({"op": "set_property", "instance": "sparks", "property": "LightEmission", "value": 1.5}, "out_of_range"),
    ({"op": "set_property", "instance": "sparks", "property": "SpreadAngle", "value": [200, 10]}, "out_of_range"),
    ({"op": "add_instance", "instance": {"id": "x", "class": "Script", "parent": "root", "properties": {}}}, "class_not_allowed"),
    ({"op": "set_field", "instance": "flash", "field": "parent", "value": "ghost"}, "unknown_parent"),
    ({"op": "set_emit", "instance": "ghost", "value": 3}, "bad_emit"),
])
def test_invalid_effects_are_rejected_with_a_reason(project, mut, code):
    with pytest.raises(ValueError, match=code):
        mutated(project, "arcane_burst", mut)


def test_texture_rules(project):
    for good in ("rbxassetid://1234", "rbxasset://textures/particles/sparkles_main.dds"):
        effects.build_plan(recipe(project, "arcane_burst"), {"texture": good})
    for bad in ("http://evil.example/x.png", "javascript:alert(1)", "rbxassetid://abc", "../../etc/passwd"):
        with pytest.raises(ValueError, match="bad_texture"):
            effects.build_plan(recipe(project, "arcane_burst"), {"texture": bad})


def test_beam_rules(project):
    with pytest.raises(ValueError, match="missing_attachment"):
        mutated(project, "energy_beam", {"op": "remove_property", "instance": "beam", "property": "Attachment1"})
    with pytest.raises(ValueError, match="same_attachment"):
        mutated(project, "energy_beam", {"op": "set_property", "instance": "beam", "property": "Attachment1", "value": "start_point"})
    with pytest.raises(ValueError, match="bad_attachment"):
        mutated(project, "energy_beam", {"op": "set_property", "instance": "beam", "property": "Attachment1", "value": "impact"})
    with pytest.raises(ValueError, match="attachment_mount"):
        mutated(project, "energy_beam", {"op": "set_top", "field": "mount", "value": "attachment"})


def test_names_and_kinds(project):
    with pytest.raises(ValueError, match="bad_name"):
        effects.build_plan(recipe(project, "arcane_burst"), name="Not Valid!")
    with pytest.raises(ValueError, match="bad_kind"):
        mutated(project, "arcane_burst", {"op": "set_top", "field": "kind", "value": "explosion"})
    with pytest.raises(ValueError, match="duplicate_name"):
        mutated(project, "arcane_burst", {"op": "set_field", "instance": "flash", "field": "name", "value": "sparks"})


def test_effect_must_show_something(project):
    with pytest.raises(ValueError, match="nothing_visible"):
        mutated(project, "ember_aura", {"op": "remove_instance", "instance": "embers"},
                {"op": "remove_instance", "instance": "haze"})


def test_warnings_do_not_block(project):
    plan = mutated(project, "arcane_burst", {"op": "set_property", "instance": "sparks", "property": "Rate", "value": 999})
    assert any("unusually large" in w for w in plan["warnings"])


def test_plan_hash_tracks_content(project):
    a = effects.build_plan(recipe(project, "arcane_burst"))
    assert effects.plan_hash(a) == effects.plan_hash(effects.build_plan(recipe(project, "arcane_burst")))
    assert effects.plan_hash(a) != effects.plan_hash(effects.build_plan(recipe(project, "arcane_burst"), {"size": 2.0}))


def test_validate_plan_works_on_hand_made_plans(snapshot):
    plan = {"recipe": "x", "name": "hand_made", "root_name": "AIEffect_hand_made", "kind": "loop", "mount": "part",
            "parameters": {}, "emit": {}, "instances": [
                {"id": "e", "class": "ParticleEmitter", "parent": "root", "name": "e", "refs": {},
                 "properties": {"Rate": {"t": "NumberRange", "min": 1, "max": 2}}}]}
    findings = effects.validate_plan(plan, snapshot)
    assert any(f["code"] == "type_mismatch" for f in findings)
