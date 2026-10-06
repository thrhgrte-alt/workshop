"""The shared guide-core Luau mock, extended just enough to run the collector.

``guide_core.mock.MockRoblox`` models Folder, ModuleScript, Configuration and the Value classes only. The collector needs scripts, models, parts with positions, prompts and remotes,
``IsA("BasePart")`` and ``GetPivot``. Rather than edit guide-core, this module patches the mock's Lua prelude text (and asserts every patch applied) and adds a few helpers. It is a guide-core
follow-up to take these classes into the shared mock. Like the original it cannot prove Studio accepts the code; it proves the script is coherent and does what it says.
"""

from __future__ import annotations

from ..guide_adapter import mock as M

CLASSES = ("Script", "LocalScript", "Model", "Part", "MeshPart", "SpawnLocation", "ProximityPrompt", "ClickDetector", "TouchInterest", "Humanoid", "RemoteEvent", "RemoteFunction",
           "BindableEvent", "Tool", "TextLabel", "ScreenGui", "Frame", "Terrain")
SOURCE_CLASSES = ("ModuleScript", "Script", "LocalScript")

EXTRA_LUA = r"""
local BASEPART = {Part = true, SpawnLocation = true, MeshPart = true}
function methods.IsA(self, class)
  local c = store[self].class
  if c == class or class == "Instance" then return true end
  if class == "BasePart" and BASEPART[c] then return true end
  if class == "ValueBase" and VALUEBASE[c] then return true end
  if class == "LuaSourceContainer" and (c == "Script" or c == "LocalScript" or c == "ModuleScript") then return true end
  if class == "PVInstance" and (BASEPART[c] or c == "Model") then return true end
  return false
end
function methods.GetPivot(self)
  local p = store[self].props.Position
  if not p then error("GetPivot is not available on " .. store[self].class) end
  return {Position = p}
end
Vector3 = {new = function(x, y, z) return {X = x, Y = y, Z = z} end}
"""


def patched_prelude() -> str:
    src = M.PRELUDE
    edits = [
        ('local ALLOWED = {ModuleScript = "Source", Folder = true, Configuration = true, NumberValue = "Value", IntValue = "Value", StringValue = "Value", BoolValue = "Value"}',
         'local ALLOWED = {ModuleScript = "Source", Folder = true, Configuration = true, NumberValue = "Value", IntValue = "Value", StringValue = "Value", BoolValue = "Value", '
         + ", ".join(f"{c} = true" for c in CLASSES) + "}"),
        ('if k == "Source" or k == "Value" then return st.props[k] end', 'if k == "Source" or k == "Value" or k == "Position" or k == "RunContext" then return st.props[k] end'),
        ('elseif k == "Source" and st.class == "ModuleScript" then', 'elseif k == "Source" and (st.class == "ModuleScript" or st.class == "Script" or st.class == "LocalScript") then'),
        ('  elseif k == "Value" and VALUEBASE[st.class] then st.props.Value = v\n  else error(', '  elseif k == "Value" and VALUEBASE[st.class] then st.props.Value = v\n  elseif k == "Position" or k == "RunContext" then st.props[k] = v\n  else error('),
    ]
    for old, new in edits:
        if old not in src:
            raise RuntimeError("guide_core.mock.PRELUDE changed: mock_extra needs updating (this is the follow-up to move these classes into guide-core)")
        src = src.replace(old, new)
    # the extra definitions must see the prelude's locals, so they go before the first global function that closes over nothing else
    marker = "function __mock_new("
    if marker not in src:
        raise RuntimeError("guide_core.mock.PRELUDE changed: cannot place the extra Lua")
    return src.replace(marker, EXTRA_LUA + "\n" + marker, 1)


class ExtendedMock(M.MockRoblox):
    def __init__(self) -> None:
        M._require()
        self.lua = M.LuaRuntime(unpack_returned_tuples=True)
        self.lua.eval("function(enc) " + patched_prelude().replace("local json_encode = ...", "local json_encode = enc") + " end")(self._encode)
        self._load = self.lua.eval("function(src) local f, err = load(src, 'generated') if not f then error(err) end return f() end")

    def add_node(self, parent: str, class_name: str, name: str, *, attributes: dict | None = None, position: tuple | None = None, source: str | None = None,
                 run_context: str | None = None) -> None:
        self.add(parent, class_name, name, attributes=attributes)
        inst = self.lua.globals()["__mock_resolve"](f"{parent}.{name}")
        if position is not None:
            inst.Position = self.lua.table(X=position[0], Y=position[1], Z=position[2])
        if source is not None:
            inst.Source = source
        if run_context is not None:
            inst.RunContext = run_context

    def build_tree(self, nodes: list[dict]) -> None:
        """Create a place from ``[{parent, class, name, attrs, pos, source, rc}]`` (parent is a dotted path under the root; missing Folders are created)."""
        for n in nodes:
            self.ensure_path(n["parent"])
            self.add_node(n["parent"], n["class"], n["name"], attributes=n.get("attrs"), position=n.get("pos"), source=n.get("source"), run_context=n.get("rc"))
