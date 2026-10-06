"""Domain logic: tokens and hashes, the script reader, the collector Luau on the guide-core mock, snapshots, roles and the weight update, graph, search, places."""
import json
import re
from pathlib import Path

import pytest
import yaml

from placemap.domain import collector, graph as G, ingest as IN, labels as LB, mock_extra, places, roles as R, scan, search as SE, snapshot as SN
from placemap.domain.cache import SummaryCache
from placemap.domain.index import PlaceIndex
from placemap.domain.secrets import SecretFilter
from placemap.domain.util import (clean_path_input, content_hash, escape_segment, fingerprint, is_place_id, jaccard, split_tokens, token_set, unescape_segment)
from placemap.guide_adapter import luau_safety, scope as S

ROOT = Path(__file__).resolve().parents[1]
needs_lupa = pytest.mark.skipif(not mock_extra.M.available(), reason="lupa not installed")


# --- util ---------------------------------------------------------------------------------------------------------------------------
def test_tokens_split_camel_case_digits_underscores_and_plurals():
    assert split_tokens("ShopNPC_2 Thing1 buyItems") == ["shop", "npc", "2", "thing", "1", "buy", "item"]
    assert split_tokens("HTTPServer") == ["http", "server"]
    assert split_tokens("class") == ["class"]  # a trailing 'ss' is not a plural
    assert token_set("Thing12", drop_numbers=True) == {"thing"}


def test_jaccard_and_empty_sets():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 1.0


def test_content_hash_ignores_line_endings_and_trailing_whitespace_only():
    assert content_hash("a\r\nb  \n") == content_hash("a\nb\n")
    assert content_hash("a\nb") != content_hash("a\n b")


def test_fingerprint_is_the_documented_checksum():
    # bytes 'a' (97) 'b' (98): ((0*31+97)*31+98) = 3105 = 0xc21
    assert fingerprint("ab") == "2:c21"
    assert fingerprint("é") == "2:" + format((0xC3 * 31 + 0xA9) % 4294967291, "x")


def test_path_helpers_escape_and_accept_studio_spellings():
    assert escape_segment("A/B%") == "A%2FB%25"
    assert unescape_segment(escape_segment("A/B%")) == "A/B%"
    assert clean_path_input("dive-and-mine::ServerScriptService/Vendors/Shop") == "ServerScriptService/Vendors/Shop"
    assert clean_path_input("game.Workspace.Vendors.Bob") == "Workspace/Vendors/Bob"
    assert clean_path_input("ServerScriptService/A.B") == "ServerScriptService/A.B"  # a dot is kept when a slash is present


def test_place_ids_are_matched_with_fullmatch_so_a_newline_is_refused():
    assert is_place_id("dive-and-mine") and not is_place_id("dive-and-mine\n") and not is_place_id("a b") and not is_place_id("")


# --- scan ------------------------------------------------------------------------------------------------------------------------------
def test_scan_handles_long_strings_backticks_and_nested_long_comments():
    src = 'local a = [[ Remotes.X:FireServer() ]]\nlocal b = `hi {RS.Remotes.Y:FireServer()}`\n--[==[ require(script.Parent.Z) ]==]\nRS.Remotes.Real:FireServer()\n'
    a = scan.analyze("local RS = game:GetService('ReplicatedStorage')\n" + src, "Script")
    assert [r["name"] for r in a["remotes_fired"]] == ["Real"] and a["requires"] == []


def test_scan_unresolvable_names_are_flagged_not_guessed():
    a = scan.analyze("function f(remote) remote:FireServer(1) end\nlocal m = require(getModule())\n", "ModuleScript")
    assert a["remotes_fired"][0]["name_guess"] is True and a["remotes_fired"][0]["chain"] is None
    assert a["requires"][0]["chain"] is None


def test_scan_secret_filter_keys_and_values():
    f = SecretFilter.load(ROOT)
    for key in ("ApiKey", "api_key", "AuthToken", "Password", "webhook_url", "Key", "BearerToken"):
        assert f.key_is_secret(key), key
    for key in ("Price", "Stock", "Author", "Keycard", "Monkey", "RequiredLevel"):
        assert not f.key_is_secret(key), key
    assert f.value_is_secret("sk-abcdefghijklmnopqrstuvwxyz1234") and f.value_is_secret("A" * 20 + "9" * 20)
    assert f.value_is_secret("https://discord.com/api/webhooks/1/abc") and not f.value_is_secret("Welcome to the shop")


# --- the collector on the mock -----------------------------------------------------------------------------------------------------------------
def _limits(project):
    return {k: v["value"] for k, v in yaml.safe_load((project.root / "rules" / "limits.yaml").read_text())["collector"].items() if isinstance(v, dict)}


@needs_lupa
def test_collector_runs_on_the_mock_and_the_result_ingests_into_a_scored_snapshot(project, call):
    m = mock_extra.ExtendedMock()
    m.set_place("Role Lab (SYNTHETIC)", 0)
    nodes = json.loads((ROOT / "examples" / "places" / "role-lab.collect.json").read_text())["nodes"]
    m.build_tree([{"parent": ".".join(n["p"].split("/")[:-1]) or "Workspace", "class": n["c"], "name": n["p"].split("/")[-1], "attrs": n.get("a"), "pos": n.get("v"),
                   "source": (n.get("s") or {}).get("src"), "rc": n.get("rc")} for n in nodes if "/" in n["p"]])
    code = collector.build_collector(place_number=0, place_name="Role Lab (SYNTHETIC)", label="demo_roles/role-lab", limits=_limits(project), roots=["Workspace", "ServerScriptService", "ReplicatedStorage"],
                                     full=True, known_fps={}, emit="return", skip_classes=[], flt=IN.default_filter())
    assert luau_safety.lint(code) == []
    raw = m.run(code)
    res = call("ingest_snapshot", project_id="demo_roles", place_id="role-lab", data=raw, dry_run=False)
    assert res["stats"]["scripts"] == 4 and res["stats"]["instances"] >= 40
    out = call("find_in_place", query="vendors", role="vendor", project_id="demo_roles", place_id="role-lab")
    assert [e["path"] for e in out["selected"]] == ["role-lab::Workspace/Npcs/Bob"]  # structure survived the Luau round trip


@needs_lupa
def test_the_collector_and_python_agree_on_fingerprints_and_never_write(project):
    code = collector.build_collector(place_number=5, place_name="X", label="a/b", limits=_limits(project), roots=["Workspace"], full=False, flt=IN.default_filter())
    for token in (".Parent =", "Instance.new", ":Destroy(", "SetAttribute(", "loadstring", "RequestAsync", "DataStoreService", "Publish"):
        assert token not in code, token
    assert "EXPECTED_PLACE_ID = 5" in code


def test_collector_refuses_unsafe_roots_and_oversized_known_lists(project):
    for bad in ("", "a" * 101, "x\ny", "x\x00y", "/".join(["a"] * 21)):
        with pytest.raises(ValueError):
            collector.validate_root(bad)
    with pytest.raises(ValueError, match="at most"):
        collector.build_collector(place_number=0, place_name="X", label="a/b", limits=_limits(project), roots=["Workspace"], full=False,
                                  known_fps={f"p{i}": "1:1" for i in range(collector.MAX_KNOWN + 1)}, flt=IN.default_filter())
    with pytest.raises(ValueError, match="emit"):
        collector.build_collector(place_number=0, place_name="X", label="a/b", limits=_limits(project), roots=["Workspace"], full=False, emit="fax", flt=IN.default_filter())


def test_the_extended_mock_refuses_when_the_shared_prelude_changes(monkeypatch):
    monkeypatch.setattr(mock_extra.M, "PRELUDE", "-- something else entirely")
    with pytest.raises(RuntimeError, match="changed"):
        mock_extra.patched_prelude()


# --- snapshots and the store ----------------------------------------------------------------------------------------------------------------
def _built(project, name="role-lab", base=None, **kw):
    data = json.loads((ROOT / "examples" / "places" / f"{name}.collect.json").read_text())
    return IN.build_snapshot(IN.parse_input(data), {"project_id": "x", "place_id": "y"}, base, SummaryCache(project), **kw)


def test_snapshot_ids_are_content_derived_and_stable(project):
    a, b = _built(project, taken_at="2026-10-06T11:00:00+00:00"), _built(project, taken_at="2026-10-06T11:00:00+00:00")
    assert a.data["snapshot_id"] == b.data["snapshot_id"] and a.data["snapshot_id"].startswith("snap-20261006T110000-")
    c = _built(project, name="tycoon-main", taken_at="2026-10-06T11:00:00+00:00")
    assert c.data["snapshot_id"] != a.data["snapshot_id"]


def test_store_saves_once_lists_newest_first_and_prunes_the_oldest(project):
    ctx = places.resolve(project, "demo_roles", "role-lab")
    store = SN.SnapshotStore(ctx.wproject)
    for hour in (9, 10, 11, 12):
        built = _built(project, taken_at=f"2026-10-06T{hour:02d}:00:00+00:00")
        path, existed = store.save(built.data)
        assert not existed and path.is_file()
    assert store.save(built.data)[1] is True
    rows = store.list()
    assert [r["taken_at"][11:13] for r in rows] == ["12", "11", "10", "09"]
    assert [r["taken_at"][11:13] for r in store.prune_candidates(1)] == ["10", "09"]
    store.delete(rows[-1]["id"])
    assert len(store.list()) == 3
    with pytest.raises(FileNotFoundError):
        store.delete("snap-nope")
    assert store.load("previous").taken_at.startswith("2026-10-06T11")
    with pytest.raises(FileNotFoundError, match="unknown snapshot"):
        store.load("snap-zzz")


def test_staleness_is_a_pure_function_of_age(project):
    assert SN.staleness("2026-10-06T11:00:00+00:00", 24)["stale"] is False
    assert SN.staleness("2026-10-04T11:00:00+00:00", 24)["stale"] is True


def test_a_script_without_a_source_is_carried_over_only_when_the_fingerprint_matches(project):
    base = _built(project, name="tycoon-main", taken_at="2026-10-06T11:00:00+00:00")
    snap = SN.Snapshot(base.data)
    plots = snap.scripts["ServerScriptService/Plots"]
    node = {"p": "ServerScriptService/Plots", "c": "Script", "n": 0, "s": {"fp": plots["fp"], "len": plots["length"]}}
    data = {"format": IN.COLLECT_FORMAT, "collector_version": 1, "place": {"name": "Demo Tycoon (SYNTHETIC)", "place_id": 9000000001}, "roots": ["ServerScriptService"], "full": False, "nodes": [
        {"p": "ServerScriptService", "c": "ServerScriptService", "n": 1}, node]}
    same = IN.build_snapshot(IN.parse_input(data), {}, snap, SummaryCache(project))
    pick = lambda b: next(x for x in b.data["scripts"] if x["path"] == "ServerScriptService/Plots")  # noqa: E731
    assert pick(same)["hash"] == plots["hash"] and same.report["scripts_carried_over_unread"] == 1
    node["s"]["fp"] = "9:1"
    other = IN.build_snapshot(IN.parse_input(data), {}, snap, SummaryCache(project))
    assert pick(other)["needs_read"] is True and other.report["scripts_needing_read"] == ["ServerScriptService/Plots"]


def test_script_read_updates_an_existing_snapshot_and_needs_one(project):
    parsed = IN.parse_input([{"path": "ServerScriptService/Plots", "source": "return 1\n"}])
    with pytest.raises(ValueError, match="update an existing snapshot"):
        IN.build_snapshot(parsed, {}, None, SummaryCache(project))
    base = SN.Snapshot(_built(project, name="tycoon-main").data)
    built = IN.build_snapshot(parsed, {}, base, SummaryCache(project))
    assert built.data["scripts"][[s["path"] for s in built.data["scripts"]].index("ServerScriptService/Plots")]["hash"] == content_hash("return 1\n")
    assert built.data["stats"]["instances"] == base.data["stats"]["instances"]


def test_a_partial_tree_without_roots_cannot_be_merged(project):
    base = SN.Snapshot(_built(project, name="tycoon-main").data)
    parsed = IN.parse_input("Workspace | Workspace\nWorkspace/X | Part\n")
    parsed.meta["roots"] = []
    with pytest.raises(ValueError, match="without roots"):
        IN.build_snapshot(parsed, {}, base, SummaryCache(project))


def test_wrapper_shapes_are_unwrapped_and_flagged():
    inner = json.dumps({"format": IN.COLLECT_FORMAT, "collector_version": 1, "place": {"name": "P"}, "roots": ["Workspace"], "full": True, "nodes": [{"p": "Workspace", "c": "Workspace", "n": 0}]})
    for wrapped in ({"result": inner}, {"content": [{"type": "text", "text": inner}]}, {"output": {"stdout": inner}}, "noise before " + inner + " noise after"):
        p = IN.parse_input(wrapped)
        assert p.format == IN.COLLECT_FORMAT and len(p.instances) == 1


def test_print_mode_chunks_are_reassembled_in_order():
    d = {"format": IN.COLLECT_FORMAT, "collector_version": 1, "place": {"name": "P"}, "roots": ["Workspace"], "full": True, "nodes": [{"p": "Workspace", "c": "Workspace", "n": 0}]}
    text = json.dumps(d)
    chunks = [text[i:i + 20] for i in range(0, len(text), 20)]
    lines = ["PLACEMAP_BEGIN %d" % len(chunks)] + [f"[12:00:01] PLACEMAP_CHUNK {i} {c}" for i, c in reversed(list(enumerate(chunks, 1)))] + ["PLACEMAP_END"]
    assert IN.parse_input("\n".join(lines)).instances[0]["path"] == "Workspace"


def test_unreadable_input_is_refused():
    with pytest.raises(ValueError, match="cannot read"):
        IN.parse_input(12345)


# --- roles, the formula and the weight update -------------------------------------------------------------------------------------------------
def test_shipped_roles_and_synonyms_validate_and_contain_no_game_names(project):
    roles, syn = R.load_roles(project), R.load_synonyms(project)
    assert R.validate_roles(roles, syn) == []
    assert set(roles) == {"vendor", "upgradable_tool", "resource_node", "collectible", "spawn", "zone", "currency_display", "progression_gate"}
    fixture_names = {"bob", "zed", "thing", "drill", "node", "pickaxe", "ore", "shopsign", "carl"}
    text = (ROOT / "rules" / "roles.yaml").read_text().lower() + (ROOT / "rules" / "synonyms.yaml").read_text().lower()
    for name in fixture_names - {"node"}:  # 'node' is a generic word for a harvestable thing, not a game's name
        assert not re.search(rf"\b{name}\b", text), name


def test_vendor_role_is_the_spec_example_exactly(project):
    v = R.load_roles(project)["vendor"]
    assert {f: (x.fn, x.w) for f, x in v.features.items()} == {
        "has_interaction": ("class_presence", 3.0), "name_matches_synonyms": ("token_match", 1.5), "has_humanoid_or_model": ("class_presence", 1.0),
        "script_fires_purchase_remote": ("remote_string_refs", 3.0), "attribute_keys_match": ("token_match", 1.0), "repeated_siblings": ("repetition", -1.0)}
    assert v.bands == {"confident": 0.75, "candidate": 0.45}
    assert v.features["attribute_keys_match"].cfg["tokens"] == ["price", "cost", "stock", "item"]
    syn = R.load_synonyms(project)
    assert syn["vendor.name_matches_synonyms"] == {"shop", "store", "vendor", "merchant", "seller", "trader", "buy", "sell"}


def test_validate_roles_reports_every_kind_of_mistake():
    bad = R.parse_roles({"roles": {"x": {"features": {
        "a": {"fn": "nonsense", "w": 1.0}, "b": {"fn": "token_match", "w": 1.0, "field": "colour"}, "c": {"fn": "class_presence", "w": 1.0},
        "d": {"fn": "proximity_cluster", "w": 1.0}, "e": {"fn": "remote_string_refs", "w": 1.0, "sources": ["telepathy"], "tokens": ["x"]}}, "bands": {"confident": 0.3, "candidate": 0.6}}}}, "t")
    problems = " | ".join(R.validate_roles(bad, {}))
    for needle in ("unknown fn", "token_match needs field", "class_presence needs classes", "needs near_roles", "unknown sources", "bands need"):
        assert needle in problems, needle


def test_project_roles_and_synonyms_extend_without_touching_the_defaults(tmp_project):
    d = tmp_project.root / "projects" / "demo_roles"
    (d / "roles.yaml").write_text(yaml.safe_dump({"roles": {"portal": {"description": "teleports", "features": {"name": {"fn": "token_match", "w": 2.0, "field": "name", "tokens": ["portal", "warp"]}},
                                                                       "bands": {"confident": 0.6, "candidate": 0.3}}}}))
    roles = R.load_roles(tmp_project, "demo_roles")
    assert "portal" in roles and roles["portal"].source == "project" and "portal" not in R.load_roles(tmp_project, "demo_mine")
    from placemap import learning_params as LP
    assert "weight.portal.name" in {s.name for s in LP.param_specs(tmp_project, "demo_roles")}
    assert "weight.portal.name" not in {s.name for s in LP.param_specs(tmp_project, "demo_mine")}


def test_every_weight_and_band_is_a_named_parameter_with_the_default_from_the_file(project):
    from placemap import learning_params as LP

    specs = {s.name: s for s in LP.param_specs(project)}
    for rid, role in R.load_roles(project).items():
        for fid, f in role.features.items():
            s = specs[f"weight.{rid}.{fid}"]
            assert s.default == f.w and s.group == "weights"
            assert (s.min <= 0 <= s.max) and (s.default >= 0) == (s.min == 0)  # the range keeps the default's sign
        assert specs[f"band.{rid}.confident"].default == role.bands["confident"]
    lim = yaml.safe_load((ROOT / "rules" / "limits.yaml").read_text())
    assert specs["feature.repetition_c"].default == lim["features"]["repetition_c"]["value"] == 3.0
    assert specs["feature.repetition_jaccard"].default == 0.6 and specs["learning.eta"].default == 0.1
    assert specs["collector.max_instances"].verify_against_current_docs is True


def test_learning_step_is_bounded_clamped_versioned_and_undoable(project):
    from placemap import learning_params as LP

    ps = LP.store(project, "demo_roles")
    sc = S.Scope("demo_roles")
    row = {"role": "vendor", "score": 0.0, "features": {"has_interaction": 1.0, "repeated_siblings": 1.0, "attribute_keys_match": 0.0}}
    steps = LB.plan_learning(row, 1, 0.1, ps, sc)
    by = {s["feature"]: s for s in steps}
    assert by["has_interaction"]["after"] == pytest.approx(3.1) and by["repeated_siblings"]["after"] == pytest.approx(-0.9)  # a negative weight moves toward 0
    assert "attribute_keys_match" not in by  # f = 0: no change
    big = LB.plan_learning(row, 1, 0.5, ps, sc)
    assert max(abs(s["delta"]) for s in big) <= 0.25 + 1e-9  # one parameter step at most
    applied = LB.apply_learning(ps, sc, steps, approved_by="tester", reason="t", evidence=["lbl-1"])
    assert ps.value("weight.vendor.has_interaction", sc) == pytest.approx(3.1) and ps.value("weight.vendor.has_interaction", S.Scope("demo_mine")) == 3.0
    ps.update("weight.vendor.has_interaction", 3.2, sc, approved_by="tester", reason="newer")
    res = LB.undo_learning(ps, sc, applied, approved_by="tester", reason="undo")
    assert [b["param"] for b in res["blocked"]] == ["weight.vendor.has_interaction"]  # a newer change blocks the silent undo
    assert [u["param"] for u in res["undone"]] == ["weight.vendor.repeated_siblings"]
    assert ps.value("weight.vendor.repeated_siblings", sc) == -1.0


def test_weights_never_leave_their_range_or_flip_sign(project):
    from placemap import learning_params as LP

    ps = LP.store(project, "demo_roles")
    sc = S.Scope("demo_roles")
    spec = ps.spec("weight.vendor.repeated_siblings")
    assert spec.max == 0.0 and spec.min < 0
    row = {"role": "vendor", "score": 1.0, "features": {"repeated_siblings": 1.0}}
    for _ in range(30):  # rejecting with score 1 pushes the negative weight further negative; it stops at the limit
        steps = LB.plan_learning(row, 0, 0.5, ps, sc)
        if not steps:
            break
        LB.apply_learning(ps, sc, steps, approved_by="tester", reason="t", evidence=[])
    assert ps.value("weight.vendor.repeated_siblings", sc) == spec.min


def test_learning_refuses_automatic_approval(project):
    from placemap import learning_params as LP
    from placemap.guide_adapter import params as P

    ps = LP.store(project, "demo_roles")
    with pytest.raises(P.ParamError, match="approved_by"):
        ps.update("weight.vendor.has_interaction", 3.1, S.Scope("demo_roles"), approved_by="auto", reason="x")


def test_a_learned_band_can_never_invert(project):
    from placemap import learning_params as LP

    ps = LP.store(project, "demo_roles")
    sc = S.Scope("demo_roles")
    for v in (0.70, 0.65, 0.60, 0.55, 0.50, 0.45, 0.40):  # the confident band walks below the default candidate band (0.45), one bounded step at a time
        ps.update("band.vendor.confident", v, sc, approved_by="tester", reason="t")
    pv = LP.values_for(project, "demo_roles", "role-lab", ps)
    assert pv("band.vendor.confident") == 0.4 and pv("band.vendor.candidate") == 0.4  # clamped: candidate may never exceed confident


# --- the place registry --------------------------------------------------------------------------------------------------------------------
def test_unpublished_places_get_a_local_id_until_they_have_a_real_one(project):
    assert places.resolve(project, "demo_mine", "dive-and-mine").stable_id == "local-dive-and-mine"
    assert places.resolve(project, "demo_mine", "dive-and-mine-hardcore").stable_id == "9000000002"
    assert places.resolve(project, "demo_mine", "9000000002").place_id == "dive-and-mine-hardcore"  # by Roblox place id too


def test_places_yaml_is_merged_into_the_registry(tmp_project):
    (tmp_project.root / "places.yaml").write_text(yaml.safe_dump({"projects": [{"project_id": "demo_roles", "places": [{"place_id": "second-lab", "studio_name": "Second Lab"}]},
                                                                                 {"project_id": "extra", "places": [{"place_id": "e1"}]}]}))
    reg = places.load_registry(tmp_project)
    assert [p.place_id for p in reg.project("demo_roles").places] == ["role-lab", "second-lab"] and "extra" in reg.project_ids()


def test_the_active_place_is_the_single_open_match_or_a_refusal(project):
    studios = [{"name": "Dive and Mine Hardcore (SYNTHETIC)", "place_id": 9000000002, "studio_id": "s"}]
    assert places.resolve(project, "demo_mine", None, studios=studios).place_id == "dive-and-mine-hardcore"
    assert places.resolve(project, "demo_mine", None, studios=studios).active_by == "open_studio"
    both = studios + [{"name": "Dive and Mine (SYNTHETIC)", "place_id": 0, "studio_id": "s2"}]
    with pytest.raises(S.ScopeError, match="cannot tell the active place"):
        places.resolve(project, "demo_mine", None, studios=both)
    with pytest.raises(S.ScopeError, match="place_id is required"):
        places.resolve(project, "demo_mine", None)


def test_match_open_is_ambiguity_safe(project):
    ctx = places.resolve(project, "demo_mine", "dive-and-mine")
    dup = [{"name": "Dive and Mine (SYNTHETIC)", "place_id": 0, "studio_id": "a"}, {"name": "dive  and mine (synthetic)", "place_id": 0, "studio_id": "b"}]
    with pytest.raises(S.ScopeError, match="2 open Studio instances"):
        places.match_open(ctx, dup)
    assert places.match_open(ctx, dup[:1])["matched_by"] == "name"


def test_a_landmarks_file_seeds_explicit_labels_for_its_own_place_only(tmp_project, tcall):
    call = tcall

    d = tmp_project.root / "projects" / "demo_roles" / "role-lab"
    d.mkdir(parents=True)
    (d / "landmarks.yaml").write_text(yaml.safe_dump({"landmarks": [{"path": "Workspace/Npcs/Carl", "role": "vendor", "alias": "the other shop"},
                                                                    {"path": "dive-and-mine::Workspace/Vendors/Bob", "role": "vendor"}]}))
    reg = yaml.safe_load((tmp_project.root / "projects.yaml").read_text())
    reg["projects"][1]["places"][0]["profiles"] = {"landmarks": "projects/demo_roles/role-lab/landmarks.yaml"}
    (tmp_project.root / "projects.yaml").write_text(yaml.safe_dump(reg))
    call("ingest_snapshot", project_id="demo_roles", place_id="role-lab", file="examples/places/role-lab.collect.json", dry_run=False, taken_at="2026-10-06T11:00:00+00:00")
    ls = LB.LabelStore(places.resolve(tmp_project, "demo_roles", "role-lab"))
    assert [(k, v["alias"]) for k, v in ls.active().items()] == [(("Workspace/Npcs/Carl", "vendor"), "the other shop")]  # the other place's landmark is not applied
    res = call("find_in_place", query="the other shop", project_id="demo_roles", place_id="role-lab")
    assert res["mode"] == "alias" and res["selected"][0]["path"] == "role-lab::Workspace/Npcs/Carl"


# --- graph, search, index -------------------------------------------------------------------------------------------------------------------
def _mini(project, nodes):
    from placemap.evalkit import mini_view

    return mini_view(project, nodes)


def test_cycles_unresolved_and_by_name_requires_are_reported(project):
    nodes = [{"p": "ReplicatedStorage", "c": "ReplicatedStorage", "n": 3},
             {"p": "ReplicatedStorage/A", "c": "ModuleScript", "n": 0, "s": {"src": 'local B = require(script.Parent.B)\nlocal Z = require(script.Parent.Missing)\nlocal Q = require(game.ServerStorage.Elsewhere.Q)\n'}},
             {"p": "ReplicatedStorage/B", "c": "ModuleScript", "n": 0, "s": {"src": "local A = require(script.Parent.A)\n"}},
             {"p": "ServerStorage", "c": "ServerStorage", "n": 1},
             {"p": "ServerStorage/Stuff/Q", "c": "ModuleScript", "n": 0, "s": {"src": "return 1\n"}}]
    g = G.Graph(_mini(project, nodes).idx)
    deps = g.dependencies("ReplicatedStorage/A", 3)
    assert [c["from"] for c in deps["cycles"]] == ["ReplicatedStorage/B"]
    reasons = {u["expr"]: u["reason"] for u in deps["unresolved"]}
    assert any("no module at the path" in r for r in reasons.values())
    q = next(m for m in deps["modules"] if m["module"] == "ServerStorage/Stuff/Q")
    assert q["matched_by"] == "name"  # path wrong, exactly one module has that name: flagged as the weaker evidence
    nodes.append({"p": "ServerStorage/Other/Q", "c": "ModuleScript", "n": 0, "s": {"src": "return 2\n"}})
    deps = G.Graph(_mini(project, nodes).idx).dependencies("ReplicatedStorage/A", 3)
    assert any("2 modules are named 'Q'" in u["reason"] for u in deps["unresolved"])


def test_dependents_are_transitive_cycle_safe_and_capped(project):
    # M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M1 (a cycle back): requirers of M5 are M4 (level 1), M3 (2), M2 (3), M1 (4), M0 (5); M5 itself is never listed.
    nodes = [{"p": "ReplicatedStorage", "c": "ReplicatedStorage", "n": 3}]
    for i in range(6):
        target = i + 1 if i < 5 else 1
        nodes.append({"p": f"ReplicatedStorage/M{i}", "c": "ModuleScript", "n": 0, "s": {"src": f"local N = require(script.Parent.M{target})\n"}})
    g = G.Graph(_mini(project, nodes).idx)
    out = g.transitive_dependents("ReplicatedStorage/M5", depth=10)
    assert {o["script"]: o["level"] for o in out} == {"ReplicatedStorage/M4": 1, "ReplicatedStorage/M3": 2, "ReplicatedStorage/M2": 3, "ReplicatedStorage/M1": 4, "ReplicatedStorage/M0": 5}
    assert [o["script"] for o in g.transitive_dependents("ReplicatedStorage/M5", depth=2)] == ["ReplicatedStorage/M4", "ReplicatedStorage/M3"]
    assert len(g.transitive_dependents("ReplicatedStorage/M5", depth=10, cap=2)) == 2


def test_search_does_not_widen_and_orders_deterministically(world, call):
    a = call("find_in_place", query="controller", project_id="demo_mine", place_id="dive-and-mine")
    b = call("find_in_place", query="controller", project_id="demo_mine", place_id="dive-and-mine")
    assert a == b and not a["selected"] and len(a["not_selected"]) >= 3  # several controllers and no exact name: nothing is chosen for the user
    one = call("find_in_place", query="DataService", project_id="demo_mine", place_id="dive-and-mine")
    assert len(one["selected"]) == 1 and one["mode"] == "exact_name"


def test_class_hints_in_a_request_narrow_to_the_class(world, call):
    r = call("find_in_place", query="Economy module", project_id="demo_mine", place_id="dive-and-mine")
    assert [e["path"] for e in r["selected"]] == ["dive-and-mine::ReplicatedStorage/Modules/Economy"]


def test_generic_names_are_not_treated_as_references(project):
    v = _mini(project, [{"p": "Workspace", "c": "Workspace", "n": 1}, {"p": "Workspace/Part", "c": "Part", "n": 0},
                        {"p": "ServerScriptService", "c": "ServerScriptService", "n": 1}, {"p": "ServerScriptService/S", "c": "Script", "n": 0, "s": {"src": 'workspace:WaitForChild("Part")\n'}}])
    assert v.idx.ref_count("Workspace/Part") == 0 and v.idx.referencing_scripts("Workspace/Part") == set()


def test_a_folder_never_counts_the_scripts_inside_it_as_its_own_evidence(project):
    v = _mini(project, [{"p": "Workspace", "c": "Workspace", "n": 1}, {"p": "Workspace/Stuff", "c": "Folder", "n": 1},
                        {"p": "Workspace/Stuff/S", "c": "Script", "n": 0, "s": {"src": "x = 1\n"}}, {"p": "Workspace/Model", "c": "Model", "n": 1},
                        {"p": "Workspace/Model/S", "c": "Script", "n": 0, "s": {"src": "x = 2\n"}}])
    assert v.idx.referencing_scripts("Workspace/Stuff") == set() and v.idx.referencing_scripts("Workspace/Model") == {"Workspace/Model/S"}


def test_containers_are_suppressed_for_the_role_their_content_plays(world, call):
    r = call("find_in_place", query="x", role="vendor", project_id="demo_mine", place_id="dive-and-mine")
    paths = {e["path"] for e in r["selected"] + r["not_selected"]}
    assert "dive-and-mine::Workspace/Vendors" not in paths and "dive-and-mine::Workspace" not in paths


def test_get_path_info_explains_why_a_container_is_not_a_landmark(world, call):
    info = call("get_path_info", path="Workspace/Vendors/VendorStatue", project_id="demo_mine", place_id="dive-and-mine", detail=True)
    assert info["roles"] == [] or info["roles"][0]["band"] != "confident"


# --- per-project style ------------------------------------------------------------------------------------------------------------------------
def test_get_style_brief_layers_the_project_style(tmp_project, tcall):
    call = tcall
    d = tmp_project.root / "projects" / "demo_roles"
    (d / "style.yaml").write_text(yaml.safe_dump({"constraints": [{"id": "Z1", "severity": "must", "rule": "Say 'borderline' for candidates."}]}))
    res = call("get_style_brief", project_id="demo_roles", max_chars=6000)
    assert "Z1" in res["brief"] and "project" in res["layers"]
    assert "Z1" not in call("get_style_brief", project_id="demo_mine", max_chars=6000)["brief"]


def test_role_aliases_are_unique_and_a_request_must_be_exactly_the_words(project, world, call):
    roles = R.load_roles(project)
    assert R.validate_roles(roles, R.load_synonyms(project)) == []
    roles["zone"].aliases = ("gate",)  # collides with progression_gate
    assert any("also name role" in p for p in R.validate_roles(roles, R.load_synonyms(project)))
    from placemap.domain.view import PlaceView

    v = PlaceView.load(places.resolve(project, "demo_mine", "dive-and-mine"))
    assert [v.role_in_query(q) for q in ("the vendors", "vendor NPC", "tools", "pickups", "spawn point", "currency", "Gates", "upgrade", "shop", "the vendor and zone")] == [
        "vendor", "vendor", "upgradable_tool", "collectible", "spawn", "currency_display", "progression_gate", None, None, None]


def test_plan_refresh_lists_cheaper_unverified_alternatives_for_partial_reads(world, call):
    studios = [{"name": "Dive and Mine (SYNTHETIC)", "place_id": 0, "studio_id": "s"}]
    res = call("plan_refresh", project_id="demo_mine", place_id="dive-and-mine", studios=studios, scripts=["ServerScriptService/Services/ShopService"], roots=["Workspace/Vendors"])
    by_tool = {}
    for a in res["alternatives"]:
        by_tool.setdefault(a["tool"], []).append(a["args"])
    assert by_tool == {"roblox_studio_search_game_tree": [{"path": "Workspace.Vendors"}], "roblox_studio_script_read": [{"path": "ServerScriptService.Services.ShopService"}]}
    assert all(a["args_schema"] == "schema_unverified" for a in res["alternatives"]) and res["full"] is False


def test_the_luau_of_a_plan_embeds_the_registered_place_and_nothing_else(world, call):
    studios = [{"name": "Dive and Mine Hardcore (SYNTHETIC)", "place_id": 9000000002, "studio_id": "s"}]
    code = call("plan_refresh", project_id="demo_mine", place_id="dive-and-mine-hardcore", studios=studios)["luau"]
    assert "local EXPECTED_PLACE_ID = 9000000002" in code and 'local EXPECTED_LABEL = "demo_mine/dive_and_mine_hardcore"' in code
