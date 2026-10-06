import pytest
import yaml

from guide_core.scope import (GLOBAL, PathNotAllowed, ProjectRegistry, Scope, ScopeError, load_layered_style, match_open_studio, overlay_style,
                              project_for_scope, require_scope, resolve_inside, safe_name, scope_from_dict, scoped_project)

# The three registry dialects used by the first repositories, reproduced here so the loader is tested against all of them.
LUAUREV = """
version: 1
projects:
  - id: example-obby
    alias: Example Obby
    universe_id: 9000000001
    ruleset: projects/example-obby/ruleset.yaml
    places:
      - {id: lobby, place_id: 9100000001, name: Lobby, path: lobby}
      - {id: stage-1, place_id: 9100000002, name: Stage 1, ruleset: projects/example-obby/places/stage-1.ruleset.yaml}
  - id: example-sandbox
    universe_id: null
    places:
      - {id: sandbox, place_id: null, name: Sandbox (unpublished)}
"""
ECONBAL = """
projects:
  - project_id: demo_mine
    alias: Demo Mine
    universe_id: 0
    places:
      - {place_id: main, alias: Main mine, studio_name: "Demo Mine Main", roblox_place_id: 0, spec: projects/demo_mine/main/economy.yaml}
      - {place_id: hardcore, studio_name: "Demo Mine Hardcore", roblox_place_id: 0, bands: projects/demo_mine/hardcore/bands.yaml}
"""
PREFLIGHT = """
projects:
  - id: synthetic-mining-tycoon
    alias: Synthetic Mining Tycoon
    place_id: 1000000001
    universe: 2000000001
    profile: projects/synthetic-mining-tycoon/profiles.yaml
"""


def reg(text):
    return ProjectRegistry.from_dict(yaml.safe_load(text))


def test_scope_basics():
    s = Scope("p", "pl")
    assert s.key == "p/pl" and s.label == "p/pl" and Scope("p").key == "p"
    g = Scope.make_global()
    assert g.is_global and g.key == GLOBAL and g.label == "global"
    assert scope_from_dict(s.to_dict()) == s and scope_from_dict(g.to_dict()) == g and scope_from_dict(None) is None


@pytest.mark.parametrize("bad", ["", "a b", "../x", "x/y", None])
def test_scope_rejects_unusable_ids(bad):
    with pytest.raises(ScopeError):
        Scope(bad)


def test_global_scope_cannot_carry_a_project():
    with pytest.raises(ScopeError):
        Scope("proj", None, True)


def test_covers_rules():
    g, p, pl, other = Scope.make_global(), Scope("a"), Scope("a", "main"), Scope("b", "main")
    assert g.covers(pl) and p.covers(pl) and pl.covers(pl)
    assert not pl.covers(p) and not p.covers(other) and not pl.covers(Scope("a", "alt")) and not p.covers(g)


def test_require_scope_refuses_unresolved():
    with pytest.raises(ScopeError, match="project_id is required"):
        require_scope(None)
    with pytest.raises(ScopeError, match="place_id is required"):
        require_scope("a", None, need_place=True)
    assert require_scope("a", "m") == Scope("a", "m")
    assert require_scope(None, is_global=True).is_global


def test_registry_loads_luaurev_dialect():
    r = reg(LUAUREV)
    assert r.project_ids() == ["example-obby", "example-sandbox"]
    lobby = r.project("example-obby").place("lobby")
    assert lobby.roblox_place_id == 9100000001 and lobby.studio_name == "Lobby"
    assert r.project("example-sandbox").place("sandbox").roblox_place_id is None  # null = unpublished
    assert r.project("example-obby").profiles == {"ruleset": "projects/example-obby/ruleset.yaml"}
    assert r.project("example-obby").place("stage-1").profiles["ruleset"].endswith("stage-1.ruleset.yaml")


def test_registry_loads_econbal_dialect():
    r = reg(ECONBAL)
    hard = r.project("demo_mine").place("hardcore")
    assert hard.studio_name == "Demo Mine Hardcore" and hard.roblox_place_id is None and hard.profiles["bands"].endswith("bands.yaml")
    assert r.project("demo_mine").place("main").alias == "Main mine"


def test_registry_loads_preflight_dialect():
    p = reg(PREFLIGHT).project("synthetic-mining-tycoon")
    assert p.universe_id == 2000000001 and p.profiles == {"profile": "projects/synthetic-mining-tycoon/profiles.yaml"}
    assert len(p.places) == 1 and p.places[0].roblox_place_id == 1000000001  # project-level numeric place becomes the one implicit place


def test_resolve_by_registry_id_or_roblox_id():
    r = reg(LUAUREV)
    assert r.resolve("example-obby", "lobby") == Scope("example-obby", "lobby")
    assert r.resolve("example-obby", 9100000002) == Scope("example-obby", "stage-1")
    assert r.resolve("example-obby") == Scope("example-obby")


def test_resolve_refuses_and_suggests():
    r = reg(LUAUREV)
    with pytest.raises(ScopeError, match="Did you mean"):
        r.resolve("example-obbyy")
    with pytest.raises(ScopeError, match="does not belong"):
        r.resolve("example-obby", "sandbox")
    with pytest.raises(ScopeError, match="project_id is required"):
        r.resolve(None)


def test_duplicate_project_or_place_rejected():
    with pytest.raises(ScopeError, match="duplicate"):
        ProjectRegistry.from_dict({"projects": [{"project_id": "a"}, {"project_id": "a"}]})
    with pytest.raises(ScopeError, match="duplicate place"):
        ProjectRegistry.from_dict({"projects": [{"project_id": "a", "places": [{"place_id": "x"}, {"place_id": "x"}]}]})


def test_registry_load_missing_file(tmp_path):
    with pytest.raises(ScopeError, match="missing"):
        ProjectRegistry.load(tmp_path / "nope.yaml")


def test_profile_path_prefers_place_then_project(tmp_path):
    f = tmp_path / "projects.yaml"
    f.write_text(ECONBAL.replace("projects:", "projects:", 1), encoding="utf-8")
    r = ProjectRegistry.load(f)
    assert r.profile_path(Scope("demo_mine", "hardcore"), "bands") == tmp_path / "projects/demo_mine/hardcore/bands.yaml"
    assert r.profile_path(Scope("demo_mine", "main"), "bands") is None


STUDIOS = [{"name": "Demo Mine Main", "place_id": 0, "studio_id": "s1"}]


def test_studio_match_by_name_and_by_id():
    r = reg(ECONBAL)
    hit = match_open_studio(r.project("demo_mine").place("main"), STUDIOS)
    assert hit["matched_by"] == "name" and hit["studio_id"] == "s1" and hit["input_schema"] == "schema_unverified"
    pl = reg(LUAUREV).project("example-obby").place("lobby")
    hit = match_open_studio(pl, [{"placeName": "Anything", "placeId": "9100000001", "id": "z"}])
    assert hit["matched_by"] == "place_id" and hit["studio_id"] == "z"


def test_studio_refuses_wrong_empty_and_ambiguous():
    place = reg(ECONBAL).project("demo_mine").place("main")
    with pytest.raises(ScopeError, match="are not place"):
        match_open_studio(place, [{"name": "Other Game", "place_id": 0}])
    with pytest.raises(ScopeError, match="cannot confirm"):
        match_open_studio(place, [])
    with pytest.raises(ScopeError, match="2 open Studio instances"):
        match_open_studio(place, [{"name": "Demo Mine Main"}, {"name": "demo mine  main"}])
    with pytest.raises(ScopeError, match="mapping"):
        match_open_studio(place, ["Demo Mine Main"])


def test_path_allowlist_and_safe_name(tmp_path):
    (tmp_path / "ok").mkdir()
    assert resolve_inside(tmp_path / "ok" / "f", [tmp_path / "ok"]).name == "f"
    with pytest.raises(PathNotAllowed):
        resolve_inside(tmp_path / "ok" / ".." / "x", [tmp_path / "ok"])
    assert safe_name("a b/c") == "a_b_c"
    with pytest.raises(ValueError):
        safe_name("///")


def test_scoped_project_workspaces_are_separate(project):
    a = scoped_project(project, "proj-a", "main")
    b = scoped_project(project, "proj-b", "main")
    g = scoped_project(project, GLOBAL, GLOBAL)
    assert a.workspace != b.workspace and a.workspace.parts[-3:] == ("projects", "proj-a", "main")
    assert g.workspace.parts[-2:] == (GLOBAL, GLOBAL)
    assert a.feedback_dir == a.workspace / "feedback"
    assert project_for_scope(project, Scope("proj-a")).workspace.parts[-1] == "_project"
    assert project_for_scope(project, Scope.make_global()).workspace == g.workspace


BASE_STYLE = {"version": 1, "name": "Base", "summary": "s", "traits": {"palette": "grey", "detail": "low"},
              "ranges": {"contrast": {"min": 0.2, "max": 0.9}}, "constraints": [{"id": "C1", "severity": "must", "rule": "keep simple"}],
              "exclusions": ["noise"]}


def test_overlay_style_merges_by_key_and_id():
    over = {"traits": {"detail": "high"}, "ranges": {"edge": {"max": 5}},
            "constraints": [{"id": "C1", "severity": "should", "rule": "relaxed"}, {"id": "C9", "severity": "must", "rule": "new"}], "exclusions": ["noise", "glow"]}
    out = overlay_style(BASE_STYLE, over)
    assert out["traits"] == {"palette": "grey", "detail": "high"}  # one key overridden, the other kept
    assert set(out["ranges"]) == {"contrast", "edge"}
    assert [(c["id"], c["severity"]) for c in out["constraints"]] == [("C1", "should"), ("C9", "must")]
    assert out["exclusions"] == ["noise", "glow"]
    assert BASE_STYLE["traits"]["detail"] == "low"  # the global style is not mutated
    assert out["_layers"]["name"] == "global" and out["_layers"]["traits"] == "project"


def test_load_layered_style_uses_project_and_place_files(project):
    (project.root / "projects" / "proj-a" / "main").mkdir(parents=True)
    (project.root / "projects" / "proj-a" / "style.yaml").write_text("traits: {detail: high}\n", encoding="utf-8")
    (project.root / "projects" / "proj-a" / "main" / "style.yaml").write_text("ranges: {contrast: {min: 0.4, max: 0.8}}\n", encoding="utf-8")
    base = load_layered_style(project, None)
    proj = load_layered_style(project, Scope("proj-a"))
    place = load_layered_style(project, Scope("proj-a", "main"))
    other = load_layered_style(project, Scope("proj-b"))
    assert base["traits"]["detail"] == "low to medium" and proj["traits"]["detail"] == "high" and other["traits"] == base["traits"]
    assert proj["ranges"]["contrast"]["max"] == 0.9 and place["ranges"]["contrast"] == {"min": 0.4, "max": 0.8}
    assert load_layered_style(project, Scope.make_global())["traits"] == base["traits"]


def test_layered_style_that_breaks_validation_is_refused(project):
    (project.root / "projects" / "proj-a").mkdir(parents=True)
    (project.root / "projects" / "proj-a" / "style.yaml").write_text("ranges: {contrast: {min: 5, max: 1}}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="min > max"):
        load_layered_style(project, Scope("proj-a"))
