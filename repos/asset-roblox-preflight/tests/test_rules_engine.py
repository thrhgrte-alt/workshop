"""Rules as data, limit resolution, project layering, safe fixes, overrides, and the vacuous-eval guard."""
import copy
import json
from pathlib import Path

import pytest
import yaml

from preflight import hooks
from preflight.domain import bpystub, engine, fixes, overrides as OV, report as R
from preflight.domain import rules as RU
from preflight.guide_adapter import evals

ROOT = Path(__file__).resolve().parents[1]


def cfg(pid=None):
    return engine.load_config(ROOT, pid)


def test_every_rule_has_code_and_every_check_has_a_rule():
    import preflight.domain.report  # noqa: F401  registers checks

    assert engine.check_coverage(cfg()) == {"rules_without_code": [], "code_without_rules": []}


def test_each_check_function_stays_inside_one_tool_group():
    import preflight.domain.report  # noqa: F401

    groups = [g for k, g in R.TOOL_GROUPS.items() if g is not None]
    for fn, ids in engine.CHECKS:
        cats = {cfg().rules[r]["category"] for r in ids}
        assert any(cats <= g for g in groups) or cats == {"upload"}, (fn.__name__, cats)


def test_rule_files_are_well_formed():
    c = cfg()
    assert len(c.rules) >= 60
    for rid, r in c.rules.items():
        assert r["severity"] in ("error", "warn", "info") and isinstance(r["safe"], bool) and r["fix"] and r["explanation"], rid
        for k, lim in r["limits"].items():
            assert "verify_against_current_docs" in lim, (rid, k)
            if lim["verify_against_current_docs"]:
                assert "DEFAULT" in lim.get("note", "DEFAULT").upper(), (rid, k)
    assert {r["category"] for r in c.rules.values()} == {"mesh", "transform", "names", "uv", "materials", "rig", "collision", "upload"}


def test_rig_rules_only_apply_to_tool_and_accessory():
    for rid, r in cfg().rules.items():
        if rid.startswith("RIG_") and rid != "RIG_UNEXPECTED":
            assert set(r["applies_to"]) == {"tool", "character_accessory"}, rid
    assert set(cfg().rules["RIG_UNEXPECTED"]["applies_to"]) == {"prop", "terrain_piece"}


def test_profiles_are_labelled_defaults():
    for name, p in cfg().profiles.items():
        assert p["limits_are_defaults"] is True and p["verify_against_current_docs"] is True, name
        text = (ROOT / "rules" / "profiles" / f"{name}.yaml").read_text().upper()
        assert "DEFAULT" in text


def test_bad_rule_and_profile_files_are_refused(tmp_path):
    assert any("severity" in p for p in RU.validate_rule({"id": "X_Y", "severity": "fatal", "title": "t", "explanation": "e", "fix": "f", "safe": False}, "mesh"))
    probs = RU.validate_rule({"id": "X_Y", "severity": "fatal", "title": "t", "explanation": "e", "fix": "f", "safe": "yes", "limits": {"a": {"default": 1}}}, "mesh")
    assert len(probs) == 3
    layer = {"profiles": {"prop": {"limits": {"MESH_TRI_BUDGET": {"nope": 1}}}}}
    with pytest.raises(ValueError, match="no limit 'nope'"):
        RU.apply_project_layer(cfg().profiles, cfg().style, layer, cfg().rules, "t")
    with pytest.raises(ValueError, match="unknown profile"):
        RU.apply_project_layer(cfg().profiles, cfg().style, {"profiles": {"car": {}}}, cfg().rules, "t")


def test_project_layering_does_not_leak_between_projects():
    a, b, g = cfg("synthetic-obby"), cfg("synthetic-mining-tycoon"), cfg()
    lim = lambda c: c.profiles["prop"]["limits"]["MESH_TRI_BUDGET"]["max_triangles"]
    assert (lim(a), lim(b), lim(g)) == (4000, 12000, 10000)
    assert a.style["naming"]["mesh_patterns"]["prop"].startswith("^ob_") and g.style["naming"]["mesh_patterns"]["prop"].startswith("^prop_")
    assert cfg("synthetic-sandbox").profiles["prop"]["limits"] == g.profiles["prop"]["limits"]


def test_limit_resolution_order(tmp_path):
    from preflight.domain import loader

    asset = loader.load_export(ROOT / "examples/assets/good_prop_crate.glb")
    c = cfg("synthetic-obby")
    ov = RU.Overrides(call={"MESH_TRI_BUDGET": {"max_triangles": 1}}, saved=[{"rule": "MESH_TRI_BUDGET", "limit_key": "max_triangles", "value": 2, "waive": False, "asset_type": "prop", "reason": "r"}])
    assert engine.Ctx(asset, c, "prop", ov).limit("MESH_TRI_BUDGET", "max_triangles") == 1  # call beats saved
    ov2 = RU.Overrides(saved=ov.saved)
    assert engine.Ctx(asset, c, "prop", ov2).limit("MESH_TRI_BUDGET", "max_triangles") == 2  # saved beats project profile
    assert engine.Ctx(asset, c, "prop").limit("MESH_TRI_BUDGET", "max_triangles") == 4000  # project profile beats rule default
    assert engine.Ctx(asset, c, "prop").limit("MESH_TRI_BUDGET_TOTAL", "max_triangles_total") == 6000
    assert engine.Ctx(asset, cfg(), "prop").limit("MESH_OBJECT_COUNT", "max_mesh_objects") == 12
    assert engine.Ctx(asset, cfg(), "tool").limit("MESH_NON_MANIFOLD", "max_non_manifold_edges") == 0  # rule default


def test_asset_name_glob_scopes_a_saved_override():
    row = {"rule": "MESH_TRI_BUDGET", "limit_key": "max_triangles", "value": 5, "waive": False, "asset_glob": "hero_*"}
    assert RU.Overrides(saved=[row], asset_name="hero_crate").lookup("MESH_TRI_BUDGET", "max_triangles")[0]
    assert not RU.Overrides(saved=[row], asset_name="crate").lookup("MESH_TRI_BUDGET", "max_triangles")[0]


def test_disabled_and_severity_overrides(monkeypatch):
    from preflight.domain import loader

    asset = loader.load_export(ROOT / "examples/assets/bad_flipped_normals.glb")
    c = copy.deepcopy(cfg())
    c.profiles["prop"]["severity"] = {"MESH_FLIPPED_NORMALS": "warn"}
    rep = R.build_report(copy.deepcopy(asset), c, "prop")
    assert rep["summary"]["result"] == "pass" and rep["findings"][0]["severity"] == "warn"
    c.profiles["prop"]["disabled_rules"] = ["MESH_FLIPPED_NORMALS"]
    rep = R.build_report(copy.deepcopy(asset), c, "prop")
    assert not rep["findings"] and {"rule": "MESH_FLIPPED_NORMALS", "reason": "disabled in rules"} in rep["skipped_rules"]


def test_waiver_keeps_finding_visible_and_not_an_error():
    from preflight.domain import loader

    asset = loader.load_export(ROOT / "examples/assets/bad_unapplied_scale.glb")
    ov = RU.Overrides(saved=[{"rule": "XFM_UNAPPLIED_SCALE", "waive": True, "reason": "on purpose", "asset_type": "prop"}])
    rep = R.build_report(asset, cfg(), "prop", overrides=ov)
    f = rep["findings"][0]
    assert f["severity"] == "info" and f["severity_original"] == "error" and f["overridden"]["reason"] == "on purpose" and rep["summary"]["ready_to_upload"] is True


def test_ready_is_never_true_for_partial_runs_or_unmeasured_fbx(call, assets):
    full = call("preflight_report", project_id="synthetic-sandbox", path=assets("good_prop_crate"), profile="prop")
    part = call("check_mesh", project_id="synthetic-sandbox", path=assets("good_prop_crate"), profile="prop")
    fbx = call("preflight_report", project_id="synthetic-sandbox", path=assets("sample_binary_header"), profile="prop")
    assert full["ready_to_upload"] is True and part["ready_to_upload"] is None and fbx["ready_to_upload"] is False and "handoff" not in fbx


def test_findings_always_carry_rule_object_measured_limit_fix(call, assets):
    out = call("preflight_report", project_id="synthetic-sandbox", path=assets("bad_summary_prop"), profile="prop", max_findings=100)
    assert out["findings"] and all({"id", "rule", "severity", "object", "measured", "limit", "fix"} <= set(f) for f in out["findings"])
    sev = [f["severity"] for f in out["findings"]]
    assert sev == sorted(sev, key=["error", "warn", "info"].index)
    assert all("\n" not in f["fix"] and "{" not in f["fix"] for f in out["findings"])


# --------------------------------------------------------------------------------------------- safe fixes
def finding(op, rule="NAME_INVALID_CHARS", safe=True):
    return {"rule": rule, "safe_fix": safe, "fix_op": op, "object": "x"}


def test_hostile_names_cannot_inject_code():
    evil = "a'); import os; os.system('x') #\n\"\"\""
    ops, _ = fixes.plan_operations([finding({"op": "rename", "object": evil, "kind": "mesh"})], {evil}, None)
    script = fixes.render_script(ops)
    assert fixes.lint_script(script) == []
    run = bpystub.run_script(script, [(evil, "MESH", (1, 1, 1), (0, 0, 0))])
    new = run["result"]["objects_renamed"][0][1]
    assert run["object_names"] == [new] and all(c.isalnum() or c in "_.-" for c in new)  # the hostile name was cleaned, not executed
    assert "import os" not in "\n".join(l for l in script.splitlines() if not l.startswith("OPS = "))


def test_lint_catches_unsafe_scripts():
    assert fixes.lint_script("import os\n")
    assert fixes.lint_script("import bpy\nbpy.ops.wm.save_mainfile()\n")
    assert fixes.lint_script("import bpy\nopen('x')\n")
    assert fixes.lint_script("def (:\n")
    assert fixes.lint_script(fixes.render_script([])) == []


def test_plan_merges_renames_and_skips_skinned_and_empties():
    fs = [finding({"op": "rename", "object": "My Crate.001", "kind": "mesh"}), finding({"op": "rename", "object": "My Crate.001", "kind": "mesh"}, "NAME_BLENDER_SUFFIX"),
          finding({"op": "apply_scale", "object": "m", "kind": "mesh", "skinned": True}, "XFM_UNAPPLIED_SCALE"), finding({"op": "apply_scale", "object": "e", "kind": "empty", "skinned": False}, "XFM_UNAPPLIED_SCALE"),
          finding({"op": "apply_scale", "object": "ok", "kind": "mesh", "skinned": False}, "XFM_UNAPPLIED_SCALE"), finding(None, "MESH_TRI_BUDGET", safe=False)]
    ops, skipped = fixes.plan_operations(fs, {"My Crate.001", "My_Crate"}, None)
    assert [o["op"] for o in ops] == ["apply_scale", "rename_object"] and ops[1]["new"] == "My_Crate_2"  # name collision resolved
    assert len(skipped) == 2
    only, _ = fixes.plan_operations(fs, set(), ["XFM_UNAPPLIED_SCALE"])
    assert [o["op"] for o in only] == ["apply_scale"]


def test_script_applies_transforms_before_renames_on_the_fake_scene():
    ops = [{"op": "rename_object", "old": "a b", "new": "a_b"}, {"op": "apply_scale", "object": "a b"}, {"op": "apply_rotation", "object": "missing"}, {"op": "rename_bone", "bone": "Spin", "new": "spin"}]
    run = bpystub.run_script(fixes.render_script(ops), [("a b", "MESH", (2, 2, 2), (0, 0, 0))], [["root", "Spin"]])
    r = run["result"]
    assert r["scale_applied"] == ["a b"] and r["objects_renamed"] == [["a b", "a_b"]] and r["bones_renamed"] == [["Spin", "spin"]]
    assert run["scales"]["a_b"] == [1.0, 1.0, 1.0] and r["skipped"][0]["object"] == "missing"


def test_apply_safe_fix_never_touches_the_export(call, assets, project):
    path = Path(assets("bad_unapplied_scale"))
    before = path.read_bytes()
    dry = call("apply_safe_fix", project_id="synthetic-sandbox", path=str(path), profile="prop")
    real = call("apply_safe_fix", project_id="synthetic-sandbox", path=str(path), profile="prop", dry_run=False)
    assert dry["dry_run"] and "written" not in dry and Path(real["written"]).read_text() == real["script"]
    assert path.read_bytes() == before and "ws/projects/synthetic-sandbox" in real["written"] and "synthetic-sandbox" in real["written"]
    with pytest.raises(ValueError, match="not marked safe"):
        call("apply_safe_fix", project_id="synthetic-sandbox", path=str(path), profile="prop", rules=["MESH_FLIPPED_NORMALS"])


# --------------------------------------------------------------------------------------------- overrides
def test_override_validation():
    c = cfg()
    ok = dict(rule="MESH_TRI_BUDGET", asset_type="prop", reason="hero", value=30000, limit_key=None, waive=False)
    OV.validate_override(c, **ok)
    for bad, msg in [({"rule": "NOPE"}, "unknown rule"), ({"asset_type": "car"}, "asset_type"), ({"reason": " "}, "reason"), ({"value": "x"}, "expects"), ({"value": None}, "new value"),
                     ({"limit_key": "zzz"}, "no limit"), ({"rule": "RIG_BONE_COUNT"}, "does not apply"), ({"waive": True}, "takes no value"), ({"rule": "NAME_SCHEME"}, "no numeric limit")]:
        with pytest.raises(ValueError, match=msg):
            OV.validate_override(c, **{**ok, **bad})
    with pytest.raises(ValueError, match="several limits"):
        OV.validate_override(c, **{**ok, "rule": "MESH_LOOSE_GEOMETRY"})
    OV.validate_override(c, rule="NAME_SCHEME", asset_type="prop", reason="r", value=None, limit_key=None, waive=True)


def rows(n, project="p", is_global=False, value=30000):
    return [OV.make_row(rule="MESH_TRI_BUDGET", asset_type="prop", reason=f"r{i}", value=value, limit_key="max_triangles", waive=False, asset_glob=None, run_id=None, asset=f"a{i}",
                        project_id=project, is_global=is_global) for i in range(n)]


def test_promotion_needs_repeats_and_targets_the_right_file():
    c = cfg("synthetic-sandbox")
    assert OV.suggest(rows(2), c) == []
    s = OV.suggest(rows(3, "synthetic-sandbox"), c)[0]
    assert s["target_file"] == "projects/synthetic-sandbox/profiles.yaml" and "max_triangles: 30000" in s["suggested_patch"] and s["distinct_assets"] == 3
    g = OV.suggest(rows(3, "synthetic-sandbox", True), c)[0]
    assert g["target_file"] == "rules/profiles/prop.yaml" and "projects/" not in g["suggested_patch"]
    already = OV.suggest(rows(3, "synthetic-mining-tycoon", value=12000), cfg("synthetic-mining-tycoon"))[0]
    assert already["already_in_profile"] and already["offer"] is None
    mixed = rows(2, "x") + rows(2, "y")
    assert OV.suggest(mixed, c) == []  # different projects never add up


# ---------------------------------------------------------------------------------- vacuous-eval guard
def run_tasks(project, ids=None):
    tasks = evals.load_tasks(project.root / "evals" / "tasks")
    if ids:
        tasks = [t for t in tasks if t["id"] in ids]
    return evals.run_suite(tasks, hooks.eval_solver(project), hooks.eval_checks(project), label="t", root=project.root)


def failed(rep):
    return {t["id"] for t in rep["tasks"] if not t["passed"]}


def test_evals_fail_when_a_rule_is_broken(project, monkeypatch):
    real = engine.load_config

    def broken(root, pid=None):
        c = copy.deepcopy(real(root, pid))
        c.rules["MESH_TRI_BUDGET"]["limits"]["max_triangles"]["default"] = 10**9
        for prof in c.profiles.values():
            prof.get("limits", {}).get("MESH_TRI_BUDGET", {}).update(max_triangles=10**9)
        return c

    monkeypatch.setattr(engine, "load_config", broken)
    ids = {"bad-too-many-triangles", "bad-summary-many-problems", "project-layer-global-still-fails", "surface-call-limit-override", "explain-finding-rule", "profiles-project-layer"}
    assert {"bad-too-many-triangles", "project-layer-global-still-fails", "bad-summary-many-problems"} <= failed(run_tasks(project, ids))


def test_evals_fail_when_every_rule_is_disabled(project, monkeypatch):
    real = engine.load_config

    def off(root, pid=None):
        c = copy.deepcopy(real(root, pid))
        for r in c.rules.values():
            r["enabled"] = False
        return c

    monkeypatch.setattr(engine, "load_config", off)
    rep = run_tasks(project)
    bad = failed(rep)
    must = {t["id"] for t in evals.load_tasks(project.root / "evals" / "tasks") if t["id"].startswith(("bad-", "surface-", "override-flow"))}
    assert len(bad) > 50 and {"bad-flipped-normals", "bad-spin-bone-name", "bad-texture-4k", "bad-unapplied-scale", "bad-missing-texture", "surface-dup-names"} <= bad, sorted(must - bad)[:10]


def test_evals_fail_when_the_ready_flag_lies(project, monkeypatch):
    real = R.build_report

    def liar(*a, **k):
        rep = real(*a, **k)
        if rep["summary"]["ready_to_upload"] is not None:
            rep["summary"]["ready_to_upload"] = True
            rep["handoff"] = {"next_tool": "roblox_upload_plan"}
        return rep

    monkeypatch.setattr(R, "build_report", liar)
    assert {"bad-fbx-header-only", "bad-flipped-normals"} <= failed(run_tasks(project, {"bad-fbx-header-only", "bad-flipped-normals", "known-good-prop-crate"}))
