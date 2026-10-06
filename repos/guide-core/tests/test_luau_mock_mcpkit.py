import inspect
from typing import Any

import pytest

from guide_core import luau_safety as ls
from guide_core import mock
from guide_core.mcpkit import ToolSpec, build_server, call_local, disabled_groups, filter_groups, tool_catalog

needs_lupa = pytest.mark.skipif(not mock.available(), reason="the optional 'lupa' package is not installed (pip install 'guide-core[mock]')")


# --- luau_safety -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("code,token", [
    ('game:HttpGet("http://x")', "HttpGet"), ('HttpService:RequestAsync({})', "RequestAsync"), ("x:GetAsync(k)", "GetAsync"), ("x:PostAsync(u, d)", "PostAsync"),
    ('local s = WebSocket.connect("ws://x")', "WebSocket"), ("loadstring(s)()", "loadstring"), ("require(m)", "require("), ("getfenv(0)", "getfenv"),
    ("setfenv(f, {})", "setfenv"), ("p:Destroy()", ":Destroy("), ("p:ClearAllChildren()", "ClearAllChildren"), ("p.Parent = nil", "Parent = nil"),
    ("p.Parent=nil", "Parent=nil"), ("game:GetService('MarketplaceService')", "MarketplaceService"), ("TeleportService:Teleport()", "TeleportService"),
    ("DataStoreService", "DataStoreService"), ("MessagingService", "MessagingService"), ("InsertService:LoadAsset(1)", "InsertService"),
    ("AssetService:CreatePlaceAsync()", "AssetService"), ("plugin:SaveInstance()", "SaveInstance"), ("game:SavePlace()", "SavePlace"), ("x:Publish()", "Publish"),
])
def test_lint_blocks(code, token):
    assert f"forbidden token '{token}'" in ls.lint(code)


def test_lint_allows_clean_and_jsonencode_only():
    assert ls.lint("local x = workspace:FindFirstChild('A')\nreturn x") == []
    assert ls.lint('local j = game:GetService("HttpService"):JSONEncode({a = 1})') == []
    assert ls.lint('local h = game:GetService("HttpService")\nh:UrlEncode("x")') == ["HttpService may only be used for JSONEncode"]


def test_lint_forbidden_is_a_superset_of_what_the_three_repos_blocked():
    econbal = ("Publish", "SavePlace", "MarketplaceService", "AssetService", "RequestAsync", "GetAsync", "PostAsync", "HttpService:Get", "HttpService:Post", "loadstring", "require(",
               "getfenv", "setfenv", "TeleportService", "DataStoreService", "MessagingService", "InsertService", "LoadAsset", ":Destroy(", "ClearAllChildren", "game:Destroy",
               "workspace:Destroy", "Parent = nil", "Parent=nil", "SaveInstance", "SavePlaceAsync")
    vfx = ("Publish", "SavePlace", "SaveToRoblox", "MarketplaceService", "AssetService", "RequestAsync", "HttpService:Get", "HttpService:Post", "loadstring", "require(", "getfenv",
           "setfenv", "TeleportService", "DataStoreService", "MessagingService", "ClearAllChildren", "game:Destroy", "workspace:Destroy")
    assert set(econbal) <= set(ls.FORBIDDEN) and set(vfx) <= set(ls.FORBIDDEN)


def test_lint_allow_only_lifts_guardable_tokens():
    assert ls.lint("p:Destroy()", allow=(":Destroy(",)) == []
    with pytest.raises(ValueError, match="can be allowed"):
        ls.lint("loadstring(x)", allow=("loadstring",))
    assert ls.lint("secret()", extra_forbidden=("secret",)) == ["forbidden token 'secret'"]


def test_assert_safe_and_check():
    assert ls.assert_safe("local x = 1") == "local x = 1"
    with pytest.raises(ValueError, match="failed the safety lint"):
        ls.assert_safe("loadstring(x)")
    assert ls.check("loadstring(x)")[0].token == "loadstring"


@pytest.mark.parametrize("bad", ["", "A..B", "A.B/C", "A.B C", 'A."B"', "A.B]", "1A.B", "A.B\n", "../A", "A.b;os.exit()"])
def test_validate_path_rejects(bad):
    with pytest.raises(ValueError):
        ls.validate_path(bad)


def test_validate_path_accepts_dotted_names():
    assert ls.validate_path("ReplicatedStorage.Config.Items") == "ReplicatedStorage.Config.Items"


def test_validate_name_and_quote_string():
    assert ls.validate_name("Config_1") == "Config_1"
    with pytest.raises(ValueError):
        ls.validate_name('x"); evil("')
    with pytest.raises(ValueError):
        ls.validate_name("Config\n")  # a trailing newline must not slip through
    assert ls.quote_string('say "hi" \\') == '"say \\"hi\\" \\\\"'
    with pytest.raises(ValueError, match="control characters"):
        ls.quote_string("a\nb")
    with pytest.raises(ValueError):
        ls.quote_string(5)  # type: ignore[arg-type]


def test_long_bracket_guard():
    assert ls.check_long_bracket_safe("return {a = 1}") == "return {a = 1}"
    with pytest.raises(ValueError, match="terminator"):
        ls.check_long_bracket_safe("x ]==] evil")


def test_place_guard_validates_inputs():
    lines = ls.place_guard(0, "Demo Place", "demo/main")
    assert lines[0] == "local EXPECTED_PLACE_ID = 0" and 'local EXPECTED_NAME = "Demo Place"' in lines
    for args in ((0, 'Bad"Name', "ok"), (0, "Ok", "bad label!"), (-1, "Ok", "ok"), (True, "Ok", "ok"), ("1", "Ok", "ok")):
        with pytest.raises(ValueError):
            ls.place_guard(*args)


# --- mock -------------------------------------------------------------------------------------------------------------
@needs_lupa
def test_mock_runs_a_script_and_reports_what_it_created():
    m = mock.MockRoblox()
    m.ensure_path("ReplicatedStorage.Config")
    code = ('local p = game:GetService("ReplicatedStorage"):FindFirstChild("Config")\nlocal m = Instance.new("ModuleScript")\nm.Name = "Values"\nm.Source = "return {a = 1}"\n'
            'm:SetAttribute("Who", "test")\nm.Parent = p\nreturn game:GetService("HttpService"):JSONEncode({ok = true})')
    assert m.run(code) == '{"ok": true}'
    info = m.info("ReplicatedStorage.Config.Values")
    assert info["class"] == "ModuleScript" and info["source"] == "return {a = 1}" and info["attributes"] == {"Who": "test"}
    assert m.info("ReplicatedStorage.Config.Nope") is None


@needs_lupa
def test_mock_rejects_unknown_class_property_and_service():
    m = mock.MockRoblox()
    for code, msg in (('Instance.new("Part")', "Unable to create"), ('local f = Instance.new("Folder")\nf.Bogus = 1', "not a valid member"),
                      ('game:GetService("Nope")', "not a valid Service")):
        with pytest.raises(Exception, match=msg):
            m.run(code)


@needs_lupa
def test_mock_place_identity_supports_place_guards():
    m = mock.MockRoblox()
    guard = "\n".join(ls.place_guard(123, "Demo Place", "demo/main"))
    m.set_place("Demo Place", 123)
    m.run(guard)
    m.set_place("Other", 999)
    with pytest.raises(Exception, match="wrong place"):
        m.run(guard)


@needs_lupa
def test_mock_ensure_path_and_add_with_attributes_and_value():
    m = mock.MockRoblox()
    m.ensure_path("ServerStorage.Shop")
    m.add("ServerStorage.Shop", "NumberValue", "Price", value=5, attributes={"Tier": 2})
    info = m.info("ServerStorage.Shop.Price")
    assert info["value"] == 5 and info["attributes"] == {"Tier": 2}
    with pytest.raises(ValueError, match="does not exist"):
        m.add("ServerStorage.Nope", "Folder", "x")


def test_mock_reports_when_lupa_is_missing(monkeypatch):
    monkeypatch.setattr(mock, "LuaRuntime", None)
    assert mock.available() is False
    with pytest.raises(RuntimeError, match="guide-core\\[mock\\]"):
        mock.MockRoblox()


# --- mcpkit -----------------------------------------------------------------------------------------------------------
def _fn(x: int = 1) -> dict[str, Any]:
    """Echo."""
    return {"x": x}


def test_toolspec_labels_and_groups():
    a = ToolSpec("a", _fn, "Read it.")
    b = ToolSpec("b", _fn, "[rare] Change it.", read_only=False)
    c = ToolSpec("c", _fn, "Plain.", group="rare")
    assert (a.label, b.label) == ("read-only", "write") and a.effective_group is None and b.effective_group == "rare" and c.effective_group == "rare"
    ann = b.annotations()
    assert ann.readOnlyHint is False and a.annotations().readOnlyHint is True


def test_toolspec_stays_positionally_compatible_with_the_vendored_kit():
    t = ToolSpec("n", _fn, "d", True, False, True, True)
    assert t.expensive is True and t.group is None


def test_group_disable_by_env(project, monkeypatch):
    specs = [ToolSpec("core", _fn, "Core."), ToolSpec("rare1", _fn, "x", group="rare"), ToolSpec("rare2", _fn, "[rare] y"), ToolSpec("other", _fn, "z", group="admin")]
    assert [s.name for s in filter_groups(project, specs)] == ["core", "rare1", "rare2", "other"]
    monkeypatch.setenv("TOYTEST_DISABLE_GROUPS", "rare")
    assert disabled_groups(project) == {"rare"} and [s.name for s in filter_groups(project, specs)] == ["core", "other"]
    monkeypatch.setenv("TOYTEST_DISABLE_GROUPS", "rare, admin")
    assert [s.name for s in filter_groups(project, specs)] == ["core"]
    monkeypatch.delenv("TOYTEST_DISABLE_GROUPS")
    monkeypatch.setenv("TOYTEST_DISABLE_RARE", "1")  # the older spelling
    assert disabled_groups(project) == {"rare"}


def test_build_server_registers_tools_and_can_apply_groups(project, monkeypatch):
    import asyncio

    specs = [ToolSpec("core", _fn, "Core."), ToolSpec("rare1", _fn, "x", group="rare")]
    names = lambda srv: sorted(t.name for t in asyncio.run(srv.list_tools()))  # noqa: E731
    assert names(build_server(project, specs, "i")) == ["core", "rare1"]
    monkeypatch.setenv("TOYTEST_DISABLE_GROUPS", "rare")
    assert names(build_server(project, specs, "i")) == ["core", "rare1"]  # opt-in: existing callers see no change
    assert names(build_server(project, specs, "i", apply_groups=True)) == ["core"]


def test_build_server_refuses_untyped_return_and_duplicates(project):
    def bad(x: int = 1) -> dict:
        return {}

    with pytest.raises(ValueError, match="structured output"):
        build_server(project, [ToolSpec("bad", bad, "d")], "i")
    with pytest.raises(ValueError, match="duplicate"):
        build_server(project, [ToolSpec("a", _fn, "d"), ToolSpec("a", _fn, "d")], "i")


def test_call_local_checks_arguments():
    specs = [ToolSpec("a", _fn, "d")]
    assert call_local(specs, "a", {"x": 5}) == {"x": 5}
    with pytest.raises(ValueError, match="unknown argument"):
        call_local(specs, "a", {"y": 1})
    with pytest.raises(ValueError, match="unknown tool"):
        call_local(specs, "zzz")


def test_tool_catalog_lines():
    cat = tool_catalog([ToolSpec("a", _fn, "First line.\nSecond."), ToolSpec("b", _fn, "[rare] x", read_only=False)])
    assert cat[0] == {"name": "a", "label": "read-only", "group": None, "line": "First line."}
    assert cat[1]["label"] == "write" and cat[1]["group"] == "rare"


def test_serve_refuses_public_binding(project):
    from guide_core.mcpkit import serve

    with pytest.raises(ValueError, match="refusing to bind"):
        serve(project, [ToolSpec("a", _fn, "d")], "i", transport="streamable-http", host="0.0.0.0")
