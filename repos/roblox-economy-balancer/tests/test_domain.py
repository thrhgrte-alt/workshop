"""Simulator against independent hand/paper models, analyses on the toy economies, rebalance safety, Luau, importer, learning, places."""
import copy
import importlib.util
import json
import random
from fractions import Fraction
from math import ceil

import pytest
import yaml

from econbal.domain import analysis as A, importer, learning, luau, places, plot, rebalance as R, sim as M, spec as S
from econbal.guide_adapter import config


def ctx_for(project, raw):
    assert not S.errors_of(S.validate(raw)), S.validate(raw)
    return A.make_ctx(raw, config.load_style(project), A.load_rules(project.root))


def single_track(costs, adds, base=10, eff=1.0, start=0):
    return {"format": S.FORMAT, "id": "t", "simulation": {"days": 14, "seed": 0}, "currencies": {"coins": {"start": start}},
            "sources": [{"id": "mining", "currency": "coins", "per_minute": base}],
            "upgrades": [{"id": f"u{i + 1}", "track": "t", "tier": i + 1, "currency": "coins", "cost": c, "effects": [{"source": "mining", "add": a}]} for i, (c, a) in enumerate(zip(costs, adds))],
            "archetypes": {"regular": {"session_minutes": 20, "sessions_per_day": 3, "efficiency": eff}}}


def paper_model(costs, adds, base, eff, start=0):
    """Written separately from the simulator, with exact fractions: wait ceil((cost - balance)/rate) whole minutes, keep the leftover."""
    t, bal, rate = 0, Fraction(start), (Fraction(base)) * Fraction(eff).limit_denominator(1000)
    out = []
    for c, a in zip(costs, adds):
        need = c - bal
        n = max(0, ceil(need / rate))
        t += n
        bal += n * rate - c
        rate += Fraction(a) * Fraction(eff).limit_denominator(1000)
        out.append(t)
    return out


def test_simulator_matches_the_paper_model_on_many_random_ladders():
    rng = random.Random(2024)
    for case in range(60):
        n = rng.randint(1, 7)
        costs = [rng.randint(20, 5000) for _ in range(n)]
        adds = [rng.randint(1, 60) for _ in range(n)]
        eff = rng.choice([0.5, 0.8, 1.0, 1.25, 1.5])
        base, start = rng.randint(2, 30), rng.choice([0, 0, 25])
        raw = single_track(costs, adds, base, eff, start)
        sm = M.simulate(S.normalize(raw), "regular", days=60)
        got = [p["minute"] for p in sm["purchases"]]
        want = paper_model(costs, adds, base, eff, start)
        want = [w for w in want if w <= sm["horizon_minutes"]]
        assert got == want, (case, costs, adds, base, eff, start)


@pytest.mark.parametrize("name", ["balanced", "wall", "dominant", "runaway"])
@pytest.mark.parametrize("arch", ["casual", "regular", "grinder"])
def test_fast_forwarding_equals_a_minute_by_minute_stepper(toys, name, arch):
    spec = S.normalize(getattr(toys, name)())
    spec["rebirths"] = []
    spec["simulation"]["days"] = 6
    fast = [(p["minute"], p["id"]) for p in M.simulate(spec, arch)["purchases"]]
    horizon = M.simulate(spec, arch)["horizon_minutes"]
    slow = M.reference_stepper(spec, arch, horizon)
    assert fast == slow


def test_balanced_toy_matches_the_numbers_derived_on_paper(toys):
    """r_n = 10 * 2^n; step n costs r_(n-1) x wanted minutes, so the gaps ARE the wanted minutes: 10,10,10,20,30,40,60,80,100,120."""
    spec = S.normalize(toys.balanced())
    sm = M.simulate(spec, "regular")
    gaps = [p["gap_minutes"] for p in sm["purchases"][:10]]
    assert gaps == [10, 10, 10, 20, 30, 40, 60, 80, 100, 120]
    assert [p["minute"] for p in sm["purchases"][:10]] == [10, 20, 30, 50, 80, 120, 180, 260, 360, 480]
    rb = sm["purchases"][10]
    assert (rb["kind"], rb["minute"], rb["gap_minutes"]) == ("rebirth", 580, 100)  # 1,024,000 / 10,240
    assert sm["purchases"][11]["gap_minutes"] == 7  # 100 / 15 per minute, rounded up


def test_variance_is_seeded_bounded_and_reproducible():
    raw = single_track([100, 900, 4000], [10, 40, 200])
    raw["archetypes"]["regular"]["variance"] = 0.5
    a, b, c = (M.simulate(S.normalize(raw), "regular", seed=s) for s in (1, 1, 2))
    key = lambda s: [(p["id"], p["minute"]) for p in s["purchases"]]
    assert key(a) == key(b) and key(a) != key(c)
    base = M.simulate(S.normalize(single_track([100, 900, 4000], [10, 40, 200])), "regular")
    lo = M.simulate(S.normalize({**raw, "archetypes": {"regular": {**raw["archetypes"]["regular"], "variance": 0.0, "efficiency": 0.5}}}), "regular")
    hi = M.simulate(S.normalize({**raw, "archetypes": {"regular": {**raw["archetypes"]["regular"], "variance": 0.0, "efficiency": 1.5}}}), "regular")
    # every session earns between 0.5x and 1.5x, so the finish time must sit between the all-slow and all-fast runs
    assert hi["purchases"][-1]["minute"] <= a["purchases"][-1]["minute"] <= lo["purchases"][-1]["minute"]
    assert base["purchases"][0]["minute"] == 10


def test_no_randomness_without_a_seed_dependence_when_variance_is_zero(toys):
    spec = S.normalize(toys.balanced())
    assert M.simulate(spec, "regular", seed=1)["purchases"] == M.simulate(spec, "regular", seed=999)["purchases"]


@pytest.mark.parametrize("name", ["balanced", "wall", "dominant", "runaway"])
def test_currency_is_conserved_every_day(project, toys, name):
    ctx = ctx_for(project, getattr(toys, name)())
    for sm in ctx.sims().values():
        bal = {c: float(ctx.spec["currencies"][c]["start"]) for c in sm["currencies"]}
        for d in sm["daily"]:
            for c in sm["currencies"]:
                bal[c] += d["sources"][c] - sum(d["sinks"][c].values())
                assert bal[c] == pytest.approx(d["balance_end"][c], rel=1e-9, abs=1e-6), (sm["archetype"], d["day"], c)


def test_tax_and_upkeep_are_conserved_too(project, toys):
    raw = toys.balanced()
    raw["sinks"] = [{"id": "fee", "currency": "coins", "kind": "tax", "value": 0.07}, {"id": "fuel", "currency": "coins", "kind": "upkeep", "value": 4}]
    sm = M.simulate(S.normalize(raw), "regular")
    bal = 0.0
    for d in sm["daily"]:
        bal += d["sources"]["coins"] - sum(d["sinks"]["coins"].values())
        assert bal == pytest.approx(d["balance_end"]["coins"], rel=1e-9, abs=1e-6)
    assert sum(d["sinks"]["coins"].get("fee", 0) for d in sm["daily"]) == pytest.approx(0.07 * sum(d["sources"]["coins"] for d in sm["daily"]))


def test_a_balance_never_goes_negative(toys):
    raw = toys.balanced()
    raw["sinks"] = [{"id": "fuel", "currency": "coins", "kind": "upkeep", "value": 9}]
    sm = M.simulate(S.normalize(raw), "regular")
    assert all(v >= 0 for d in sm["daily"] for v in d["balance_end"].values())


# --- the toys ------------------------------------------------------------------------------------------------------------------
def codes(res, sev=("error", "warning")):
    return {f["code"] for f in res["findings"] if f["severity"] in sev}


def test_the_four_toys_behave_as_designed(project, toys):
    res = {n: A.check(ctx_for(project, getattr(toys, n)())) for n in ("balanced", "wall", "runaway", "dominant")}
    assert res["balanced"]["verdict"] == "pass" and codes(res["balanced"]) == set()
    assert {"wall", "cost_cliff", "gap_cliff", "dead_option"} <= codes(res["wall"])
    assert {"runaway_inflation", "content_exhausted", "under_band"} <= codes(res["runaway"])
    assert {"dominant_option", "dominant_strategy"} <= codes(res["dominant"])
    for n in ("wall", "runaway", "dominant"):
        assert res[n]["verdict"] == "fail"


def test_dead_option_boundary_is_the_style_limit(project, toys):
    ctx = ctx_for(project, toys.balanced())
    assert not [f for f in A.dead_options(ctx)["findings"] if f["code"] == "dead_option"]
    raw = toys.balanced()
    next(u for u in raw["upgrades"] if u["id"] == "drill_10")["cost"] = 5120 * 241  # payback 241 minutes for regular, 160.7 for the grinder -> grinder is under the limit, so NOT dead
    assert not [f for f in A.dead_options(ctx_for(project, raw))["findings"] if f["code"] == "dead_option"]
    next(u for u in raw["upgrades"] if u["id"] == "drill_10")["cost"] = 5120 * 400  # grinder payback 266.7 > 240
    assert [f for f in A.dead_options(ctx_for(project, raw))["findings"] if f["code"] == "dead_option"]


def test_band_boundaries_are_inclusive(project):
    raw = single_track([100], [10])  # gap 10, band early 5-15
    assert not [f for f in A.time_to_upgrade(ctx_for(project, raw))["findings"] if f["code"] in ("over_band", "under_band")]
    for cost, code in ((150, None), (160, "over_band"), (50, None), (40, "under_band")):
        raw = single_track([cost], [10])
        got = {f["code"] for f in A.time_to_upgrade(ctx_for(project, raw))["findings"] if f["code"] in ("over_band", "under_band")}
        assert got == ({code} if code else set()), (cost, got)


def test_pending_step_beyond_the_horizon_is_projected_not_hidden(project):
    raw = single_track([10_000_000], [10])
    tt = A.time_to_upgrade(ctx_for(project, raw))
    assert [f for f in tt["findings"] if f["code"] == "wall" and f.get("projected")]
    assert any(r.get("projected") for r in tt["rows"])


# --- rebalance ---------------------------------------------------------------------------------------------------------------------
def propose(project, raw, code, **kw):
    ctx = ctx_for(project, raw)
    base = A.check(ctx)
    return ctx, R.propose(ctx, base, R.select_finding(base["findings"], code, **kw), max_changes=kw.pop("max_changes", 2))


def test_wall_fix_is_one_value_and_resolves_everything_on_that_step(project, toys):
    ctx, res = propose(project, toys.wall(), "wall", upgrade="drill_4")
    assert res["found"] and [(c["path"], c["after"]) for c in res["changes"]] == [("upgrades.drill_4.cost", 1920)]
    after = A.check(ctx_for(project, res["proposed_spec"]))
    assert after["verdict"] == "pass"
    # 1920 is the first grid step whose cost (4.8x drill_3) is under the 5x cost-jump limit; one grid step less (2240) would still be a cost cliff
    assert 2240 / 400 > 5 and 1920 / 400 < 5


def test_locks_are_never_changed_whatever_is_locked(project, toys):
    paths = list(S.value_paths(S.normalize(toys.wall())))
    for lock in ("upgrades.drill_4.cost", "upgrades.drill_3.*", "sources.*", "upgrades.*", "upgrades.drill_4"):
        raw = toys.wall()
        raw["locked"] = [lock]
        ctx = ctx_for(project, raw)
        locked = set(S.locked_paths(ctx.spec))
        base = A.check(ctx)
        res = R.propose(ctx, base, R.select_finding(base["findings"], "wall", upgrade="drill_4"), max_changes=2)
        changed = {c["path"] for c in res["changes"]} if res["found"] else set()
        assert not (changed & locked), (lock, changed & locked)
        assert locked <= set(paths)


def test_proposals_never_modify_their_input(project, toys):
    raw = toys.wall()
    before = copy.deepcopy(raw)
    ctx = ctx_for(project, raw)
    base = A.check(ctx)
    R.propose(ctx, base, R.select_finding(base["findings"], "wall", upgrade="drill_4"))
    assert raw == before


def test_two_value_search_is_used_only_when_one_value_cannot_do_it(project, toys):
    raw = toys.wall()
    raw["locked"] = ["upgrades.drill_4.cost"]
    ctx = ctx_for(project, raw)
    base = A.check(ctx)
    res = R.propose(ctx, base, R.select_finding(base["findings"], "wall", upgrade="drill_4"), max_changes=2)
    if res["found"]:
        assert res["n_changes"] in (1, 2)
    single = R.propose(ctx, base, R.select_finding(base["findings"], "wall", upgrade="drill_4"), max_changes=1)
    if res["found"] and res["n_changes"] == 2:
        assert not single["found"]


def test_apply_to_raw_keeps_the_users_format(toys):
    raw = toys.wall()
    out = R.apply_to_raw(raw, [{"path": "upgrades.drill_4.cost", "after": 1920}])
    assert S.diff_values(raw, out) == [{"path": "upgrades.drill_4.cost", "before": 32000, "after": 1920}]
    with pytest.raises(ValueError, match="does not state"):
        R.apply_to_raw(raw, [{"path": "upgrades.drill_4.min_rebirths", "after": 3}])


# --- spec -----------------------------------------------------------------------------------------------------------------------------
def test_value_paths_round_trip_and_set_value_is_pure(toys):
    spec = S.normalize(toys.balanced())
    for path, v in S.value_paths(spec).items():
        assert S.get_value(spec, path) == v
    out = S.set_value(spec, "upgrades.drill_2.cost", 999)
    assert S.get_value(spec, "upgrades.drill_2.cost") == 200 and S.get_value(out, "upgrades.drill_2.cost") == 999
    with pytest.raises(ValueError):
        S.set_value(spec, "upgrades.nope.cost", 1)
    with pytest.raises(ValueError, match="names an entity"):
        S.get_value(spec, "upgrades.drill_2")


def test_locked_patterns_and_entity_flags(toys):
    raw = toys.balanced()
    raw["locked"] = ["upgrades.drill_1.*", "rebirths.rebirth"]
    raw["upgrades"][1]["locked"] = True
    locked = set(S.locked_paths(raw))
    assert {"upgrades.drill_1.cost", "upgrades.drill_1.effects.0.add", "rebirths.rebirth.cost", "upgrades.drill_2.cost"} <= locked
    assert "upgrades.drill_3.cost" not in locked


def test_the_example_specs_validate_without_errors_or_warnings(toys):
    for n in ("balanced", "wall", "runaway", "dominant"):
        assert S.validate(getattr(toys, n)()) == [] or all(f["severity"] == "info" for f in S.validate(getattr(toys, n)())), n


def test_yaml_with_a_colon_in_text_is_reported_helpfully(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("id: x\nname: bad: text: here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="quote"):
        S.load_spec(p)


# --- luau ------------------------------------------------------------------------------------------------------------------------------
PLACE = (0, "Test Place", "test/place")
HAS_LUPA = importlib.util.find_spec("lupa") is not None


def build(values=None, **kw):
    values = values or {"upgrades.drill_1.cost": 100, "sources.mining.per_minute": 10.5}
    module = luau.values_module_source(values, spec_id="t", spec_hash="abc", label="test/place")
    kw.setdefault("parent_path", "ReplicatedStorage.Config")
    kw.setdefault("config_name", "Econ_P")
    return module, luau.build_export_luau(module, n_values=len(values), spec_hash="abc", place=PLACE, **kw), values


def test_exported_luau_is_lint_clean_and_free_of_forbidden_calls():
    _, code, _ = build()
    assert luau.lint(code) == []
    for bad in ("loadstring", "HttpService:GetAsync", "PostAsync", "require(", ":Destroy(", "MarketplaceService", "Publish", "DataStoreService"):
        assert bad in " ".join(luau.lint(code + "\n" + bad)) or bad.split(":")[0] in " ".join(luau.lint(code + "\n" + bad)), bad


@pytest.mark.skipif(not HAS_LUPA, reason="lupa not installed")
def test_exported_luau_runs_on_the_mock_and_creates_exactly_one_new_module():
    module, code, values = build()
    res = luau.verify_export_on_mock(code, module, values, "ReplicatedStorage.Config", "Econ_P", open_place=("Test Place", 0))
    assert res["status"] == "ok" and res["attributes"] == {"AIGeneratedBy": "econbal"}


@pytest.mark.skipif(not HAS_LUPA, reason="lupa not installed")
def test_exported_luau_never_overwrites_and_refuses_the_wrong_place():
    module, code, values = build()
    again = luau.verify_export_on_mock(code, module, values, "ReplicatedStorage.Config", "Econ_P", pre_existing=True, open_place=("Test Place", 0))
    assert again["status"] == "error" and "refusing to overwrite" in again["error"]
    wrong = luau.verify_export_on_mock(code, module, values, "ReplicatedStorage.Config", "Econ_P", open_place=("Another Place", 0))
    assert wrong["status"] == "error" and "wrong place" in wrong["error"]


@pytest.mark.skipif(not HAS_LUPA, reason="lupa not installed")
def test_place_id_is_checked_when_the_place_is_published():
    from econbal.domain.mock_luau import MockRoblox

    module = luau.values_module_source({"a.b": 1}, spec_id="t", spec_hash="x", label="p/q")
    code = luau.build_export_luau(module, parent_path="ReplicatedStorage.Config", config_name="M", n_values=1, spec_hash="x", place=(555, "Name", "p/q"))
    for place_id, ok in ((555, True), (556, False)):
        m = MockRoblox()
        m.ensure_path("ReplicatedStorage.Config")
        m.set_place("Whatever", place_id)
        if ok:
            m.run(code)
        else:
            with pytest.raises(Exception, match="wrong place"):
                m.run(code)


@pytest.mark.skipif(not HAS_LUPA, reason="lupa not installed")
def test_module_source_evaluates_to_the_exact_values():
    from econbal.domain.mock_luau import MockRoblox

    values = {"upgrades.drill_1.cost": 100, "sources.mining.per_minute": 10.5, "boosts.b.effects.0.mult": 1.25}
    module, code, _ = build(values)
    m = MockRoblox()
    m.set_place("Test Place", 0)
    m.ensure_path("ReplicatedStorage.Config")
    m.run(code)
    src = m.info("ReplicatedStorage.Config.Econ_P")["source"]
    got = m.lua.eval("function(src) return load(src)() end")(src)
    assert {k: got["values"][k] for k in values} == values and got["meta"]["place"] == "test/place"


@pytest.mark.parametrize("name", ['X"]==]', "a b", "9x", "x;y", "", "A" * 60])
def test_unsafe_module_names_are_rejected(name):
    with pytest.raises(ValueError):
        luau.validate_name(name)


@pytest.mark.parametrize("path", ["ReplicatedStorage.Config; game:Destroy()", "a..b", "x.y z", "", "a.1b", "../x"])
def test_unsafe_paths_are_rejected(path):
    with pytest.raises(ValueError):
        luau.validate_path(path)


def test_unsafe_place_names_cannot_be_embedded():
    for name in ('a"b', "x\ny", "q;r", ""):
        with pytest.raises(ValueError):
            luau.place_guard(0, name, "a/b")
    with pytest.raises(ValueError):
        luau.place_guard(0, "ok", "a/b\"c")


def test_a_value_key_with_odd_characters_cannot_reach_the_module_text():
    with pytest.raises(ValueError):
        luau.values_module_source({'a"]=1 --': 1}, spec_id="t", spec_hash="x")


@pytest.mark.skipif(not HAS_LUPA, reason="lupa not installed")
def test_import_dump_round_trip_on_the_mock_tree():
    from econbal.domain.mock_luau import MockRoblox

    code = luau.build_import_luau(vendor_path="ReplicatedStorage.Shop.Items", tools_path="ReplicatedStorage.Tools", place=PLACE)
    assert luau.lint(code) == []
    m = MockRoblox()
    m.set_place("Test Place", 0)
    m.ensure_path("ReplicatedStorage.Shop.Items")
    m.ensure_path("ReplicatedStorage.Tools")
    m.add("ReplicatedStorage.Shop.Items", "Configuration", "Steel", attributes={"Price": 200, "Tier": 2})
    m.add("ReplicatedStorage.Shop.Items.Steel", "NumberValue", "SpeedMult", value=1.5)
    m.add("ReplicatedStorage.Tools", "Folder", "Empty")
    dump = json.loads(m.run(code))
    assert dump["format"] == "econbal-import/1"
    item = dump["containers"]["vendor_items"]["items"][0]
    assert item["name"] == "Steel" and item["fields"] == {"Price": 200, "Tier": 2, "SpeedMult": 1.5}
    assert dump["containers"]["tool_stats"]["items"][0]["fields"] in ({}, [])  # an empty Lua table encodes as []; the importer treats both as no fields


def test_import_script_changes_nothing():
    code = luau.build_import_luau(vendor_path="ReplicatedStorage.A", ores_path="ReplicatedStorage.B", place=PLACE)
    for token in ("Instance.new", ".Parent =", "SetAttribute", ".Source", ":Destroy", "Clone"):
        assert token not in code


# --- importer ------------------------------------------------------------------------------------------------------------------------
def sample_dump():
    from pathlib import Path

    return json.loads((Path(__file__).resolve().parents[1] / "examples" / "imports" / "sample_dump.json").read_text(encoding="utf-8"))


def test_sample_dump_normalises_into_a_valid_spec_with_every_assumption_listed(project):
    res = importer.normalise(sample_dump())
    assert res["ok"]
    sp = res["spec"]
    assert {s["id"]: s["per_minute"] for s in sp["sources"]} == {"ore_Copper": 20.0, "ore_Iron": 24.0}
    ups = {u["id"]: u for u in sp["upgrades"]}
    assert ups["Steel_Drill"]["effects"] == [{"source": "*", "mult": 1.5}] and ups["Diamond_Drill"]["effects"] == [{"source": "*", "mult": 3.0}]
    assert ups["Iron_Bonus"]["effects"] == [{"source": "ore_Iron", "mult": 1.25}]
    assert all(a.get("assumed") for a in sp["archetypes"].values())
    assert any("ASSUMED" in a for a in res["assumptions"])
    assert A.check(ctx_for(project, sp))["verdict"] in ("pass", "fail")


def test_import_never_invents_effects():
    dump = {"format": "econbal-import/1", "containers": {"ores": {"items": [{"name": "C", "fields": {"Value": 1}}]}, "vendor_items": {"items": [{"name": "D", "fields": {"Price": 5, "Tier": 1}}]}}}
    res = importer.normalise(dump)
    assert res["spec"]["upgrades"][0]["effects"] == [] and any("NO effect" in u for u in res["unresolved"])


# --- plot ------------------------------------------------------------------------------------------------------------------------------
def test_plot_is_a_real_png_with_content(project, toys, tmp_path):
    from PIL import Image

    ctx = ctx_for(project, toys.balanced())
    info = plot.render(ctx.sims(), tmp_path / "p.png", title="x")
    img = Image.open(info["path"])
    assert img.size == (info["width"], info["height"]) and len({c for _, c in img.convert("RGB").getcolors(10000)}) > 4
    again = plot.render(ctx.sims(), tmp_path / "p2.png", title="x")
    assert Image.open(again["path"]).tobytes() == img.tobytes()  # deterministic drawing
    with pytest.raises(ValueError, match="matplotlib"):
        if importlib.util.find_spec("matplotlib"):
            raise ValueError("matplotlib")
        plot.render(ctx.sims(), tmp_path / "m.png", backend="matplotlib")


# --- learning ---------------------------------------------------------------------------------------------------------------------------
def run_dec(i, corrections, decision="revise", constraints=None):
    return ({"run_id": f"r{i}", "request": "x", "constraints": constraints or {}}, {"run_id": f"r{i}", "decision": decision, "reason": "r", "corrections": corrections})


def test_learning_rules(project, toys):
    ctx = ctx_for(project, toys.balanced())
    pairs = [run_dec(1, [{"dimension": "pacing", "tier": 4, "felt": "too_slow", "note": "n"}]), run_dec(2, [{"dimension": "pacing", "note": "tier 4 felt too slow"}])]
    res = learning.suggest(ctx, [p[0] for p in pairs], [p[1] for p in pairs])
    s = res["suggestions"][0]
    assert (s["type"], s["band"], s["current"], s["suggested"], s["simulated_gap_minutes"]) == ("lower_band_max", "mid", 45, 16.0, 20)  # 20 x (1 - 0.2)
    assert res["applied"] is False
    one = learning.suggest(ctx, [pairs[0][0]], [pairs[0][1]])
    assert one["suggestions"][0]["type"] == "need_more_reports"
    fast = [run_dec(i, [{"dimension": "pacing", "tier": 5, "direction": "more", "note": "n"}]) for i in (1, 2)]
    s = learning.suggest(ctx, [p[0] for p in fast], [p[1] for p in fast])["suggestions"][0]
    assert (s["type"], s["suggested"]) == ("raise_band_min", 37.5)  # tier 5 simulates at 30 minutes: 30 x 1.25


def test_learning_median_and_lock_rules(project, toys):
    ctx = ctx_for(project, toys.balanced())
    obs = [{"dimension": "archetype_assumption", "archetype": "grinder", "field": "efficiency", "observed": v, "note": "t"} for v in (1.0, 1.2, 1.4, 2.0)]
    pairs = [run_dec(1, obs)]
    s = learning.suggest(ctx, [pairs[0][0]], [pairs[0][1]])["suggestions"][0]
    assert (s["type"], s["suggested"], s["current"]) == ("change_archetype", 1.3, 1.5)  # median of 1.0, 1.2, 1.4, 2.0
    rej = [run_dec(i, [], "reject", {"rebalance": {"changes": [{"path": "upgrades.drill_4.cost"}]}}) for i in (1, 2)]
    out = learning.suggest(ctx, [p[0] for p in rej], [p[1] for p in rej])["suggestions"]
    assert out and out[0]["type"] == "consider_locking"
    ctx.spec["locked"] = ["upgrades.drill_4.cost"]
    assert learning.suggest(ctx, [p[0] for p in rej], [p[1] for p in rej])["suggestions"] == []


# --- places ---------------------------------------------------------------------------------------------------------------------------------
def test_registry_resolution_and_refusals(project):
    e = places.resolve(project, "demo_mine", "main")
    assert e["synthetic"] and e["studio_name"].startswith("Demo Mine")
    for args, text in (((None, None), "missing project_id and place_id"), (("demo_mine", None), "missing place_id"), (("demo_mine", "zzz"), "unknown place"), (("zzz", "main"), "known projects")):
        with pytest.raises(ValueError, match=text):
            places.resolve(project, *args)


def test_studio_matching_rules(project):
    e = places.resolve(project, "demo_tycoon", "main")
    assert places.match_studio(e, [{"name": "x", "place_id": 9000000001, "studio_id": "s"}])["matched_by"] == "place_id"
    assert places.match_studio(e, [{"name": "Demo Tycoon (synthetic)", "place_id": 1, "studio_id": "s"}])["matched_by"] == "name"
    with pytest.raises(ValueError, match="not place"):
        places.match_studio(e, [{"name": "Other", "place_id": 3}])
    with pytest.raises(ValueError, match="list_roblox_studios"):
        places.match_studio(e, None)
    with pytest.raises(ValueError, match="unambiguous"):
        places.match_studio(e, [{"name": "Demo Tycoon (SYNTHETIC)"}, {"name": "demo tycoon (synthetic)"}])
    with pytest.raises(ValueError):
        places.match_studio(e, ["not a dict"])


def test_layered_bands_replace_only_what_the_place_defines(project):
    main = places.layered_style(project, places.resolve(project, "demo_mine", "main"))[0]
    hard = places.layered_style(project, places.resolve(project, "demo_mine", "hardcore"))[0]
    assert main["target_bands"][0]["max_minutes"] == 15 and hard["target_bands"][0]["max_minutes"] == 30
    assert hard["ranges"] == main["ranges"] and hard["learning"] == main["learning"]


# --- rubric -----------------------------------------------------------------------------------------------------------------------------
def test_rubric_auto_scores_and_manual_gate(project, toys):
    rub = config.load_rubric(project.root / "evals" / "rubric.yaml")
    good = config.score_rubric(rub, auto=A.rubric_auto_scores(A.check(ctx_for(project, toys.balanced()))["findings"]))
    assert good["unscored"] == ["bands_confirmed", "archetypes_confirmed", "matches_design_intent"] and not good["passed"] and not good["complete"]
    done = config.score_rubric(rub, auto=A.rubric_auto_scores(A.check(ctx_for(project, toys.balanced()))["findings"]),
                               manual={"bands_confirmed": 1, "archetypes_confirmed": 1, "matches_design_intent": 0.8})
    assert done["passed"]
    bad = config.score_rubric(rub, auto=A.rubric_auto_scores(A.check(ctx_for(project, toys.wall()))["findings"]),
                              manual={"bands_confirmed": 1, "archetypes_confirmed": 1, "matches_design_intent": 1})
    assert not bad["passed"] and "no_walls" in bad["required_failures"]
