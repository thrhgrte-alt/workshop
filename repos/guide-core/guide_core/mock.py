"""A tiny mock of the parts of the Roblox DataModel that generated Luau commonly uses, executed with an embedded Lua runtime.

Shared by every guide repository that generates Luau (extracted from ``econbal.domain.mock_luau``). Needs the optional ``lupa``
package (``pip install 'guide-core[mock]'``); :func:`available` says whether it is importable and tests skip when it is not. It models: ``game:GetService`` (ReplicatedStorage, ServerStorage, Workspace,
HttpService with ``JSONEncode``), ``workspace``, ``Instance.new`` for a handful of classes (ModuleScript, Folder, Configuration and the Value
objects), ``Parent``/``Name``/``Source``/``Value`` properties (unknown properties raise, like Studio), ``FindFirstChild``, ``GetChildren``,
attributes, ``IsA``, ``GetFullName``. It does NOT model Studio's security model (for example whether ``Script.Source`` may be written from
the command bar), so it cannot prove that Studio accepts the code; it only proves the script is coherent and does what it says.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import lupa
    from lupa import LuaError, LuaRuntime
except ImportError:  # optional dependency
    lupa = None  # type: ignore
    LuaRuntime = None  # type: ignore
    LuaError = Exception  # type: ignore

PRELUDE = r"""
local json_encode = ...
local printed = {}
function print(...) local t = {} for i = 1, select('#', ...) do t[#t+1] = tostring(select(i, ...)) end printed[#printed+1] = table.concat(t, " ") end
function string.split(s, sep) local out = {} for piece in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do out[#out+1] = piece end return out end
function typeof(v) return type(v) end

local ALLOWED = {ModuleScript = "Source", Folder = true, Configuration = true, NumberValue = "Value", IntValue = "Value", StringValue = "Value", BoolValue = "Value"}
local VALUEBASE = {NumberValue = true, IntValue = true, StringValue = true, BoolValue = true}
local store = setmetatable({}, {__mode = "k"})

local methods = {}
local Instance_mt = {}
local function newInstance(class, name)
  if not ALLOWED[class] then error("Unable to create an Instance of type \"" .. tostring(class) .. "\" in this mock") end
  local obj = {}
  store[obj] = {class = class, name = name or class, parent = nil, children = {}, attrs = {}, props = {}}
  return setmetatable(obj, Instance_mt)
end
function methods.FindFirstChild(self, name)
  for _, c in ipairs(store[self].children) do if store[c].name == name then return c end end
  return nil
end
function methods.GetChildren(self) local out = {} for i, c in ipairs(store[self].children) do out[i] = c end return out end
function methods.GetAttribute(self, k) return store[self].attrs[k] end
function methods.SetAttribute(self, k, v) store[self].attrs[k] = v end
function methods.GetAttributes(self) local out = {} for k, v in pairs(store[self].attrs) do out[k] = v end return out end
function methods.IsA(self, class) local c = store[self].class return c == class or (class == "ValueBase" and VALUEBASE[c] == true) or class == "Instance" end
function methods.GetFullName(self)
  local parts, node = {}, self
  while node do
    local st = store[node]
    if st.isRoot then break end
    table.insert(parts, 1, st.name)
    node = st.parent
  end
  return table.concat(parts, ".")
end
Instance_mt.__index = function(self, k)
  if methods[k] then return methods[k] end
  local st = store[self]
  if k == "Name" then return st.name elseif k == "ClassName" then return st.class elseif k == "Parent" then return st.parent end
  if k == "Source" or k == "Value" then return st.props[k] end
  return nil
end
Instance_mt.__newindex = function(self, k, v)
  local st = store[self]
  if k == "Name" then st.name = v
  elseif k == "Parent" then
    if st.parent then
      local siblings = store[st.parent].children
      for i, c in ipairs(siblings) do if c == self then table.remove(siblings, i) break end end
    end
    st.parent = v
    if v then table.insert(store[v].children, self) end
  elseif k == "Source" and st.class == "ModuleScript" then
    if type(v) ~= "string" then error("Source must be a string") end
    st.props.Source = v
  elseif k == "Value" and VALUEBASE[st.class] then st.props.Value = v
  else error(tostring(k) .. " is not a valid member of " .. st.class) end
end

Instance = {new = function(class) return newInstance(class) end}

local root = newInstance("Folder", "game")
store[root].isRoot = true
local function service(name) local s = newInstance("Folder", name); s.Parent = root; return s end
local ReplicatedStorage, ServerStorage, Workspace = service("ReplicatedStorage"), service("ServerStorage"), service("Workspace")
local HttpService = {JSONEncode = function(self, t) return json_encode(t) end}
game = {
  Name = "Place1", PlaceId = 0,
  GetService = function(self, name)
    if name == "HttpService" then return HttpService end
    local c = methods.FindFirstChild(root, name)
    if c then return c end
    error("'" .. tostring(name) .. "' is not a valid Service name in this mock")
  end,
  FindFirstChild = function(self, name) return methods.FindFirstChild(root, name) end,
}
workspace = Workspace

function __mock_new(class, name, parent) local o = newInstance(class, name) o.Parent = parent return o end
function __mock_printed() return printed end
function __mock_root() return root end
function __mock_info(inst)
  local st = store[inst]
  local out = {class = st.class, name = st.name, source = st.props.Source, value = st.props.Value, attrs = st.attrs, children = #st.children}
  return out
end
function __mock_resolve(path)
  local node = root
  for part in string.gmatch(path, "[^.]+") do
    node = methods.FindFirstChild(node, part)
    if not node then return nil end
  end
  return node
end
"""


def available() -> bool:
    """True when ``lupa`` is installed (the mock can run)."""
    return LuaRuntime is not None


def _require() -> None:
    if LuaRuntime is None:
        raise RuntimeError("The Luau mock needs the optional 'lupa' package: pip install 'guide-core[mock]'")


def _to_py(obj: Any) -> Any:
    if lupa is not None and lupa.lua_type(obj) == "table":
        keys = list(obj.keys())
        if not keys:
            return []
        if all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [_to_py(obj[k]) for k in sorted(keys)]
        return {str(k): _to_py(obj[k]) for k in keys}
    return obj


class MockRoblox:
    def __init__(self) -> None:
        _require()
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.eval("function(enc) " + PRELUDE.replace("local json_encode = ...", "local json_encode = enc") + " end")(self._encode)
        self._load = self.lua.eval("function(src) local f, err = load(src, 'generated') if not f then error(err) end return f() end")

    @staticmethod
    def _encode(table: Any) -> str:
        return json.dumps(_to_py(table), sort_keys=True)

    def run(self, code: str) -> Any:
        """Execute the generated code; returns what the script returned (a JSON string for these scripts)."""
        return self._load(code)

    @property
    def printed(self) -> list[str]:
        return list(self.lua.globals()["__mock_printed"]().values())

    def add(self, parent_path: str, class_name: str, name: str, *, attributes: dict | None = None, value: Any = None) -> None:
        parent = self.lua.globals()["__mock_resolve"](parent_path)
        if parent is None:
            raise ValueError(f"mock parent '{parent_path}' does not exist")
        inst = self.lua.globals()["__mock_new"](class_name, name, parent)
        if value is not None:
            inst.Value = value
        for k, v in (attributes or {}).items():
            inst.SetAttribute(inst, k, v)

    def set_place(self, name: str, place_id: int = 0) -> None:
        """Pretend this is the open place (``game.Name`` and ``game.PlaceId``)."""
        g = self.lua.globals()
        g.game["Name"] = name
        g.game["PlaceId"] = place_id

    def ensure_path(self, path: str) -> None:
        """Create any missing Folders along a dotted path (the first part may be an existing service)."""
        g = self.lua.globals()
        node = g["__mock_root"]()
        for part in path.split("."):
            child = node.FindFirstChild(node, part)
            node = child if child is not None else g["__mock_new"]("Folder", part, node)

    def info(self, path: str) -> dict | None:
        inst = self.lua.globals()["__mock_resolve"](path)
        if inst is None:
            return None
        got = self.lua.globals()["__mock_info"](inst)
        return {"class": got["class"], "name": got["name"], "source": got["source"], "value": got["value"], "attributes": _to_py(got["attrs"]) if got["attrs"] else {},
                "children": got["children"]}
