"""A small mock of the Roblox DataModel, executed with an embedded Lua runtime (``pip install '.[simulate]'``).

It lets you (and the tests) RUN the generated Luau before it goes near Studio: ``Instance.new`` rejects classes
outside the allowed set, assigning an unknown property or a value of the wrong Luau type raises (like Studio
does), enums are checked, and ``Vector3``/``Color3``/``Enum`` exist. It is driven by the same API snapshot the
generator trusts. It does NOT model rendering, physics, collisions or Studio itself, so it cannot prove Studio
accepts the code, only that the code is coherent against the documented API. The generator avoids Luau-only syntax
so plain Lua can execute it.
"""

from __future__ import annotations

import json

try:
    from lupa import LuaError, LuaRuntime
except ImportError:  # optional dependency
    LuaRuntime = None  # type: ignore
    LuaError = Exception  # type: ignore

PRELUDE = r"""
local SNAP = ...
local printed = {}
function print(...) local t = {} for i = 1, select('#', ...) do t[#t+1] = tostring(select(i, ...)) end printed[#printed+1] = table.concat(t, " ") end
function string.split(s, sep) local out = {} for piece in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do out[#out+1] = piece end return out end

local function mk(tname, fields) return setmetatable(fields, {__rbxtype = tname}) end
function typeof(v)
  if type(v) == "table" then local mt = getmetatable(v); if mt and mt.__rbxtype then return mt.__rbxtype end end
  return type(v)
end
Color3 = {new = function(r, g, b) return mk("Color3", {R = r or 0, G = g or 0, B = b or 0}) end}
Vector3 = {new = function(x, y, z) return mk("Vector3", {X = x or 0, Y = y or 0, Z = z or 0}) end}

local enum_cache = {}
Enum = setmetatable({}, {__index = function(_, name)
  local members = SNAP.enums[name]
  if not members then error("'" .. tostring(name) .. "' is not a valid Enum") end
  if enum_cache[name] then return enum_cache[name] end
  local etype = setmetatable({}, {__rbxtype = "Enum", __tostring = function() return name end})
  local items = {}
  for i, m in ipairs(members) do items[m] = mk("EnumItem", {Name = m, Value = i - 1, EnumType = etype}) end
  getmetatable(etype).__index = function(_, k)
    if items[k] then return items[k] end
    error("'" .. tostring(k) .. "' is not a valid member of Enum." .. name)
  end
  enum_cache[name] = etype
  return etype
end})

local function default_for(ty)
  if ty == "float" or ty == "int" then return 0 end
  if ty == "boolean" then return false end
  if ty == "string" or ty == "ContentId" or ty == "Content" then return "" end
  if ty == "Vector3" then return Vector3.new(0, 0, 0) end
  if ty == "Color3" then return Color3.new(1, 1, 1) end
  if SNAP.enums[ty] then return Enum[ty][SNAP.enums[ty][1]] end
  return nil
end
local SUPPORTED = {float = true, int = true, boolean = true, string = true, ContentId = true, Vector3 = true, Color3 = true}

local ALLOWED = {}
for _, c in ipairs(SNAP.allowed) do ALLOWED[c] = true end
local function chain(cn)
  local out, seen = {}, {}
  local function walk(c)
    local info = SNAP.classes[c]
    if not info or seen[c] then return end
    seen[c] = true; out[#out+1] = c
    for _, parent in ipairs(info.inherits or {}) do walk(parent) end
  end
  walk(cn)
  return out
end
local function class_props(cn)
  if not ALLOWED[cn] then return nil end
  local props = {}
  for _, c in ipairs(chain(cn)) do
    for name, p in pairs(SNAP.classes[c].properties) do if props[name] == nil then props[name] = p.type end end
  end
  return props
end
local function check(ty, v)
  if ty == "float" then return type(v) == "number" end
  if ty == "int" then return type(v) == "number" and math.floor(v) == v end
  if ty == "boolean" then return type(v) == "boolean" end
  if ty == "string" or ty == "ContentId" or ty == "Content" then return type(v) == "string" end
  if SNAP.enums[ty] then return typeof(v) == "EnumItem" and tostring(v.EnumType) == ty end
  if SUPPORTED[ty] then return typeof(v) == ty end
  return false
end

local function new_instance(cn)
  local props = class_props(cn)
  if not props then error('Unable to create an Instance of type "' .. tostring(cn) .. '"') end
  local ancestors = {}
  for _, c in ipairs(chain(cn)) do ancestors[c] = true end
  local data = {store = {}, children = {}, attrs = {}, parent = nil}
  for name, ty in pairs(props) do data.store[name] = default_for(ty) end
  data.store.Name = cn
  local self = {}
  local methods = {}
  function methods.FindFirstChild(_, name) for _, c in ipairs(data.children) do if c.Name == name then return c end end return nil end
  function methods.GetChildren() local out = {} for i, c in ipairs(data.children) do out[i] = c end return out end
  function methods.GetDescendants()
    local out = {}
    local function walk(inst) for _, c in ipairs(inst:GetChildren()) do out[#out+1] = c; walk(c) end end
    walk(self)
    return out
  end
  function methods.IsA(_, c) return c == "Instance" or ancestors[c] == true end
  function methods.SetAttribute(_, k, v) data.attrs[k] = v end
  function methods.GetAttribute(_, k) return data.attrs[k] end
  function methods.GetFullName()
    local parts, cur = {}, self
    while cur ~= nil do table.insert(parts, 1, cur.Name); cur = cur.Parent end
    return table.concat(parts, ".")
  end
  function methods.Destroy()
    if data.parent then
      local sib = data.parent_data.children
      for i, c in ipairs(sib) do if c == self then table.remove(sib, i) break end end
    end
    data.parent = nil
  end
  local meta = {__rbxtype = "Instance"}
  meta.__index = function(_, k)
    if k == "ClassName" then return cn end
    if k == "Parent" then return data.parent end
    if k == "__data" then return data end
    if methods[k] then return methods[k] end
    if props[k] ~= nil or k == "Name" then return data.store[k] end
    error("'" .. tostring(k) .. "' is not a valid member of " .. cn)
  end
  meta.__newindex = function(_, k, v)
    if k == "Parent" then
      if v ~= nil and typeof(v) ~= "Instance" then error("Parent must be an Instance or nil") end
      if data.parent then
        local sib = data.parent_data.children
        for i, c in ipairs(sib) do if c == self then table.remove(sib, i) break end end
      end
      data.parent = v
      if v ~= nil then data.parent_data = v.__data; table.insert(v.__data.children, self) end
      return
    end
    local ty = props[k]
    if ty == nil then error("'" .. tostring(k) .. "' is not a valid member of " .. cn) end
    if not check(ty, v) then error("Unable to assign property " .. k .. ". " .. ty .. " expected, got " .. typeof(v)) end
    data.store[k] = v
  end
  return setmetatable(self, meta)
end
Instance = {new = new_instance}

local services = {}
SNAP.allowed[#SNAP.allowed + 1] = "Workspace"
SNAP.classes.Workspace = {properties = {}, inherits = {"Model"}}
ALLOWED.Workspace = true
local workspace_inst = new_instance("Workspace"); workspace_inst.Name = "Workspace"
services.Workspace = workspace_inst
workspace = workspace_inst
local function json_encode(v)
  local t = type(v)
  if t == "nil" then return "null" end
  if t == "boolean" then return tostring(v) end
  if t == "number" then if v == math.floor(v) and math.abs(v) < 1e15 then return string.format("%d", v) end return string.format("%.14g", v) end
  if t == "string" then return '"' .. v:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end) .. '"' end
  if t == "table" then
    local n = 0 for _ in pairs(v) do n = n + 1 end
    if n == #v and n > 0 then
      local out = {} for i, x in ipairs(v) do out[i] = json_encode(x) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    local keys = {} for k in pairs(v) do keys[#keys+1] = tostring(k) end table.sort(keys)
    local out = {} for _, k in ipairs(keys) do out[#out+1] = json_encode(k) .. ":" .. json_encode(v[k]) end
    return "{" .. table.concat(out, ",") .. "}"
  end
  error("cannot encode " .. t)
end
services.HttpService = {JSONEncode = function(_, v) return json_encode(v) end}
game = setmetatable({}, {__rbxtype = "DataModel", __index = function(_, k)
  if k == "GetService" then return function(_, name)
    if not services[name] then error("'" .. tostring(name) .. "' is not a valid Service name") end
    return services[name] end end
  if k == "FindFirstChild" then return function(_, name) return services[name] end end
  if k == "ClassName" then return "DataModel" end
  error("'" .. tostring(k) .. "' is not a valid member of DataModel")
end})

return {
  run = function(code)
    local f, err = load(code, "=blockout")
    if not f then error("syntax error: " .. tostring(err)) end
    return f()
  end,
  printed = function() return json_encode(printed) end,
  workspace = workspace_inst,
}
"""


class MockRoblox:
    """One mock DataModel. ``run(code)`` executes Luau-compatible Lua and returns what the chunk returns."""

    def __init__(self, snapshot: dict, allowed: tuple[str, ...] = ("Folder", "Model", "Part", "SpawnLocation")):
        if LuaRuntime is None:
            raise RuntimeError("Simulation needs the optional 'lupa' package: pip install '.[simulate]'")
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        loader = self.lua.eval("function(src, snap) local f = assert(load(src)) return f(snap) end")
        snap = {**snapshot, "allowed": list(allowed)}
        self.env = loader(PRELUDE, self.lua.table_from(snap, recursive=True))

    def run(self, code: str):
        return self.env.run(code)

    @property
    def printed(self) -> list[str]:
        return json.loads(self.env.printed())

    def make_folder(self, name: str, parent=None):
        mk = self.lua.eval("function(parent, name) local f = Instance.new('Folder'); f.Name = name; f.Parent = parent; return f end")
        return mk(parent or self.env.workspace, name)

    def tree(self, instance=None) -> dict:
        """Nested {name, class, props, attrs, children} for assertions (positions and sizes as plain lists)."""
        fn = self.lua.eval("""
        function(inst)
          local function ser(v)
            local t = typeof(v)
            if t == "Vector3" then return {v.X, v.Y, v.Z} end
            if t == "Color3" then return {v.R, v.G, v.B} end
            if t == "EnumItem" then return v.Name end
            if t == "number" or t == "boolean" or t == "string" then return v end
            return nil
          end
          local function walk(i)
            local row = {name = i.Name, class = i.ClassName, children = {}, props = {}, attrs = i.__data.attrs}
            for _, p in ipairs({"Size", "Position", "Color", "Material", "Anchored", "CanCollide", "Transparency", "Neutral"}) do
              local ok, v = pcall(function() return i[p] end)
              if ok then row.props[p] = ser(v) end
            end
            for _, c in ipairs(i:GetChildren()) do row.children[#row.children + 1] = walk(c) end
            return row
          end
          return walk(inst)
        end""")
        return _to_python(fn(instance or self.env.workspace))


def _to_python(obj):
    """Convert a Lua table (lupa proxy) into plain dict/list/scalars."""
    if hasattr(obj, "items"):
        items = dict(obj.items())
        if items and all(isinstance(k, int) for k in items):
            return [_to_python(items[k]) for k in sorted(items)]
        return {k: _to_python(v) for k, v in items.items()}
    return obj


def simulate(code: str, snapshot: dict, *, foreign: str | None = None, target: str | None = None) -> dict:
    """Run ``code`` on a fresh mock. ``foreign`` pre-creates an unrelated instance with that name under ``target``/workspace."""
    mock = MockRoblox(snapshot)
    parent = mock.make_folder(target) if target else None
    if foreign:
        mock.make_folder(foreign, parent)
    try:
        returned = mock.run(code)
    except LuaError as exc:
        return {"ok": False, "error": str(exc).splitlines()[0], "printed": mock.printed}
    try:
        report = json.loads(returned) if isinstance(returned, str) else None
    except json.JSONDecodeError:
        report = None
    return {"ok": True, "report": report, "printed": mock.printed, "mock": mock}
