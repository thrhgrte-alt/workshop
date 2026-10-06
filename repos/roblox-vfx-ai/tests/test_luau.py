"""Generated Luau: safety lint, execution on the mock DataModel, inspection round trip."""
import json

import pytest

from rbxvfx.domain import api, effects, luau
from rbxvfx.domain.mock_roblox import LuaError, MockRoblox, simulate

RECIPES = ["arcane_burst", "ember_aura", "energy_beam", "sprint_trail"]


def plan_of(project, rid, **params):
    return effects.build_plan(effects.load_recipe(project.root / "recipes" / f"{rid}.yaml"), params or None)


@pytest.fixture()
def mock(snapshot):
    return MockRoblox(snapshot)


# --- the mock itself must be strict, or the tests below prove nothing ------------------------------------
@pytest.mark.parametrize("lua,needle", [
    ("Instance.new('Script')", "Unable to create an Instance"),
    ("local e = Instance.new('ParticleEmitter'); e.Glowiness = 1", "not a valid member"),
    ("local e = Instance.new('ParticleEmitter'); e.Rate = 'fast'", "float expected"),
    ("local e = Instance.new('ParticleEmitter'); e.Size = NumberRange.new(1, 2)", "NumberSequence expected"),
    ("local e = Instance.new('ParticleEmitter'); e.Shape = Enum.Material.Plastic", "not a valid Enum"),
    ("local e = Instance.new('ParticleEmitter'); e.Shape = Enum.ParticleEmitterShape.Triangle", "not a valid member"),
    ("NumberSequence.new({NumberSequenceKeypoint.new(0.5, 1, 0), NumberSequenceKeypoint.new(1, 1, 0)})", "first Time must be 0"),
    ("local e = Instance.new('ParticleEmitter'); e:Destroy(); return e.Nope", "not a valid member"),
])
def test_mock_rejects_what_studio_rejects(mock, lua, needle):
    with pytest.raises(LuaError, match=needle):
        mock.run(lua)


def test_mock_accepts_valid_use_and_reports_types(mock):
    assert mock.run("local e = Instance.new('ParticleEmitter'); e.Rate = 5; return typeof(e) .. ':' .. e.ClassName") == "Instance:ParticleEmitter"
    assert mock.run("return typeof(NumberRange.new(1, 2)) .. typeof(Color3.new(1, 0, 0))") == "NumberRangeColor3"


# --- generated code ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("rid", RECIPES)
def test_generated_code_runs_cleanly_on_the_mock(project, snapshot, rid):
    plan = plan_of(project, rid)
    res = simulate(luau.build_create_script(plan, "workspace", (0, 5, 0), emit_now=True), snapshot)
    assert res["ok"], res
    report = res["report"]
    assert report["status"] == "ok" and report["plan_hash"] == effects.plan_hash(plan)
    assert {c["name"] for c in report["created"]} == {i["name"] for i in plan["instances"]}
    assert res["emits"] == {k: v for k, v in plan["emit"].items()}
    assert res["tags"] == [[plan["root_name"], "AIEffect"]]


def test_emit_only_happens_when_asked(project, snapshot):
    plan = plan_of(project, "arcane_burst")
    quiet = simulate(luau.build_create_script(plan, "workspace", (0, 5, 0), emit_now=False), snapshot)
    assert quiet["ok"] and quiet["emits"] == {}


def test_rerun_replaces_only_its_own_root(project, snapshot, mock):
    code = luau.build_create_script(plan_of(project, "arcane_burst"), "workspace", (0, 5, 0), emit_now=False)
    first, second = json.loads(mock.run(code)), json.loads(mock.run(code))
    assert first["root"] == second["root"] == "Workspace.AIEffect_arcane_burst"
    count = mock.lua.eval("function(ws) local n = 0 for _, c in ipairs(ws:GetChildren()) do if c.Name == 'AIEffect_arcane_burst' then n = n + 1 end end return n end")
    assert count(mock.env.workspace) == 1


def test_it_refuses_to_replace_something_it_did_not_create(project, snapshot):
    code = luau.build_create_script(plan_of(project, "arcane_burst"), "workspace", (0, 5, 0))
    res = simulate(code, snapshot, preexisting_foreign="AIEffect_arcane_burst")
    assert not res["ok"] and "refusing to replace" in res["error"]


def test_missing_target_is_a_clear_error(project, snapshot):
    code = luau.build_create_script(plan_of(project, "arcane_burst"), "workspace.Nowhere", (0, 5, 0))
    res = simulate(code, snapshot)
    assert not res["ok"] and "was not found" in res["error"]


def test_nested_target_path_works(project, snapshot):
    code = luau.build_create_script(plan_of(project, "arcane_burst"), "workspace.Effects", (0, 5, 0))
    res = simulate(code, snapshot, target="Effects")
    assert res["ok"] and res["report"]["root"] == "Workspace.Effects.AIEffect_arcane_burst"


def test_remove_script_removes_own_and_refuses_foreign(project, snapshot, mock):
    mock.run(luau.build_create_script(plan_of(project, "arcane_burst"), "workspace", (0, 5, 0)))
    assert json.loads(mock.run(luau.build_remove_script("AIEffect_arcane_burst")))["status"] == "removed"
    assert json.loads(mock.run(luau.build_remove_script("AIEffect_arcane_burst")))["status"] == "not_found"
    mock.make_folder("AIEffect_other")
    with pytest.raises(LuaError, match="refusing to remove"):
        mock.run(luau.build_remove_script("AIEffect_other"))


@pytest.mark.parametrize("rid", RECIPES)
def test_inspection_round_trip_passes_the_same_validator(project, snapshot, rid):
    mock = MockRoblox(snapshot)
    report = json.loads(mock.run(luau.build_create_script(plan_of(project, rid), "workspace", (0, 5, 0))))
    seen = json.loads(mock.run(luau.build_inspect_script(report["root"])))
    back = luau.parse_inspection(seen)
    assert not [f for f in effects.validate_plan(back) if f["severity"] == "error"]
    assert sorted(i["id"] for i in back["instances"]) == sorted(i["id"] for i in plan_of(project, rid)["instances"])


def test_inspection_detects_a_tampered_effect(project, snapshot):
    mock = MockRoblox(snapshot)
    report = json.loads(mock.run(luau.build_create_script(plan_of(project, "arcane_burst"), "workspace", (0, 5, 0))))
    mock.run("local root = workspace:FindFirstChild('AIEffect_arcane_burst'); "
             "root:FindFirstChild('sparks').Transparency = NumberSequence.new(0.5)")  # constant -> valid; now break lifetime
    mock.run("local root = workspace:FindFirstChild('AIEffect_arcane_burst'); "
             "root:FindFirstChild('sparks').Lifetime = NumberRange.new(3, 1)")
    seen = json.loads(mock.run(luau.build_inspect_script(report["root"])))
    findings = effects.validate_plan(luau.parse_inspection(seen))
    assert any(f["code"] == "range_order" for f in findings)


# --- safety lint ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rid", RECIPES)
def test_generated_code_is_lint_clean_and_contains_no_forbidden_tokens(project, rid):
    code = luau.build_create_script(plan_of(project, rid), "workspace.Effects", (1, 2, 3), emit_now=True)
    assert luau.lint(code) == []
    for tok in luau.FORBIDDEN:
        assert tok not in code
    assert code.count(":Destroy()") == 1 and 'GetAttribute("AIGeneratedBy") == "rbxvfx"' in code


@pytest.mark.parametrize("text,needle", [
    ("game:GetService('AssetService'):CreatePlaceAsync()", "forbidden token"),
    ("MarketplaceService:PromptPurchase()", "forbidden token"),
    ("HttpService:GetAsync('http://x')", "forbidden token"),
    ("loadstring('x')()", "forbidden token"),
    ("workspace:FindFirstChild('x'):Destroy()", "not directly guarded"),
    ("game:GetService('HttpService'):UrlEncode('x')", "only be used for JSONEncode"),
])
def test_lint_catches_unsafe_code(text, needle):
    assert any(needle in p for p in luau.lint(text))


@pytest.mark.parametrize("path", ['workspace; os.exit()', 'workspace"] = nil --', "workspace..x", "", "a b.c d e!", "workspace\nlocal x"])
def test_target_path_injection_is_refused(project, path):
    with pytest.raises(ValueError, match="dotted path"):
        luau.build_create_script(plan_of(project, "arcane_burst"), path, (0, 5, 0))


def test_non_ascii_strings_are_refused():
    with pytest.raises(ValueError, match="ASCII"):
        luau.string("café")


def test_invalid_plans_never_become_code(project):
    plan = plan_of(project, "arcane_burst")
    plan["instances"][0]["properties"]["Glowiness"] = 1
    with pytest.raises(ValueError, match="refusing to generate Luau"):
        luau.build_create_script(plan, "workspace", (0, 5, 0))


def test_generation_is_deterministic(project):
    a = luau.build_create_script(plan_of(project, "energy_beam"), "workspace", (0, 5, 0))
    assert a == luau.build_create_script(plan_of(project, "energy_beam"), "workspace", (0, 5, 0))


def test_inspect_properties_come_from_the_snapshot():
    props = luau.inspectable_properties("ParticleEmitter")
    assert "Rate" in props and "Parent" not in props and "VelocitySpread" not in props  # deprecated excluded
