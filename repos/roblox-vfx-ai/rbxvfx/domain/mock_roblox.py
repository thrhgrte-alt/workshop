"""A small mock of the Roblox DataModel, executed with an embedded Lua runtime (``pip install '.[simulate]'``).

It lets you (and the tests) RUN the generated Luau before it goes anywhere near Studio: Instance.new rejects unknown classes, assigning an unknown property
or a value of the wrong Luau type raises (like Studio does), enums are checked, and the typed Roblox
datatypes (NumberSequence, ColorSequence, ...) exist. It is driven by the same API snapshot the validator
uses, so a bad property name fails here too. It does NOT model rendering, physics or Studio itself; it
cannot prove Studio accepts the code, only that the code behaves coherently against the documented API.
Luau-specific syntax is deliberately not used by the generator, so plain Lua can execute it.
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
local emits = {}
function print(...) local t = {} for i = 1, select('#', ...) do t[#t+1] = tostring(select(i, ...)) end printed[#printed+1] = table.concat(t, " ") end
function string.split(s, sep) local out = {} for piece in (s .. sep):gmatch("(.-)" .. sep:gsub("%p", "%%%0")) do out[#out+1] = piece end return out end

local function mk(tname, fields, extra)
  local mt = {__rbxtype = tname}
  if extra then for k, v in pairs(extra) do mt[k] = v end end
  return setmetatable(fields, mt)
end
function typeof(v)
  if type(v) == "table" then local mt = getmetatable(v); if mt and mt.__rbxtype then return mt.__rbxtype end end
  return type(v)
end

NumberRange = {new = function(a, b) b = b or a; return mk("NumberRange", {Min = a, Max = b}) end}
NumberSequenceKeypoint = {new = function(t, v, e) return mk("NumberSequenceKeypoint", {Time = t, Value = v, Envelope = e or 0}) end}
ColorSequenceKeypoint = {new = function(t, c) return mk("ColorSequenceKeypoint", {Time = t, Value = c}) end}
NumberSequence = {new = function(a, b)
  if type(a) == "table" then
    assert(#a >= 2, "NumberSequence: requires at least 2 keypoints")
    assert(a[1].Time == 0, "NumberSequence: first Time must be 0")
    assert(a[#a].Time == 1, "NumberSequence: last Time must be 1")
    for i = 2, #a do assert(a[i].Time > a[i-1].Time, "NumberSequence: Times must increase") end
    return mk("NumberSequence", {Keypoints = a})
  end
  b = b or a
  return mk("NumberSequence", {Keypoints = {NumberSequenceKeypoint.new(0, a, 0), NumberSequenceKeypoint.new(1, b, 0)}})
end}
Color3 = {new = function(r, g, b) return mk("Color3", {R = r or 0, G = g or 0, B = b or 0}) end}
ColorSequence = {new = function(a)
  if typeof(a) == "Color3" then a = {ColorSequenceKeypoint.new(0, a), ColorSequenceKeypoint.new(1, a)} end
  assert(#a >= 2, "ColorSequence: requires at least 2 keypoints")
  assert(a[1].Time == 0 and a[#a].Time == 1, "ColorSequence: must span 0..1")
  return mk("ColorSequence", {Keypoints = a})
end}
Vector2 = {new = function(x, y) return mk("Vector2", {X = x or 0, Y = y or 0}) end}
Vector3 = {new = function(x, y, z) return mk("Vector3", {X = x or 0, Y = y or 0, Z = z or 0}) end}

local enum_cache = {}
Enum = setmetatable({}, {__index = function(_, name)
  local members = SNAP.enums[name]
  if not members then error("'" .. tostring(name) .. "' is not a valid Enum") end
  if enum_cache[name] then return enum_cache[name] end
  local etype = setmetatable({}, {__rbxtype = "Enum", __tostring = function() return name end})
  local items = {}
  for i, m in ipairs(members) do
    items[m] = mk("EnumItem", {Name = m, Value = i - 1, EnumType = etype})
  end
  getmetatable(etype).__index = function(_, k)
    if items[k] then return items[k] end
    error("'" .. tostring(k) .. "' is not a valid member of Enum." .. name)
  end
  etype.__items = items
  enum_cache[name] = etype
  return etype
end})

local function default_for(ty)
  if ty == "float" or ty == "int" then return 0 end
  if ty == "boolean" then return false end
  if ty == "string" or ty == "ContentId" or ty == "Content" then return "" end
  if ty == "NumberRange" then return NumberRange.new(0, 0) end
  if ty == "NumberSequence" then return NumberSequence.new(1) end
  if ty == "ColorSequence" then return ColorSequence.new(Color3.new(1, 1, 1)) end
  if ty == "Vector2" then return Vector2.new(0, 0) end
  if ty == "Vector3" then return Vector3.new(0, 0, 0) end
  if ty == "Color3" then return Color3.new(1, 1, 1) end
  if SNAP.enums[ty] then local m = SNAP.enums[ty][1]; return Enum[ty][m] end
  return nil
end

local PART_PROPS = {Anchored = "boolean", CanCollide = "boolean", CanQuery = "boolean", CanTouch = "boolean",
                    Transparency = "float", Size = "Vector3", Position = "Vector3"}
local function class_props(cn)
  local props = {}
  local function merge(c, seen)
    local info = SNAP.classes[c]
    if not info or seen[c] then return end
    seen[c] = true
    for name, p in pairs(info.properties) do if props[name] == nil then props[name] = p.type end end
    for _, parent in ipairs(info.inherits or {}) do merge(parent, seen) end
  end
  if cn == "Part" then
    merge("Instance", {}); for k, v in pairs(PART_PROPS) do props[k] = v end
  elseif cn == "Folder" or cn == "Workspace" or cn == "Model" then
    merge("Instance", {})
  elseif SNAP.classes[cn] and cn ~= "Instance" and cn ~= "Light" then
    merge(cn, {})
  else
    return nil
  end
  return props
end

local function check(ty, v)
  if ty == "float" then return type(v) == "number" end
  if ty == "int" then return type(v) == "number" and math.floor(v) == v end
  if ty == "boolean" then return type(v) == "boolean" end
  if ty == "string" or ty == "ContentId" or ty == "Content" then return type(v) == "string" end
  if ty == "Attachment" then return v == nil or (typeof(v) == "Instance" and v.ClassName == "Attachment") end
  if ty == "Instance" then return v == nil or typeof(v) == "Instance" end
  if SNAP.enums[ty] then return typeof(v) == "EnumItem" and tostring(v.EnumType) == ty end
  return typeof(v) == ty
end

local BASEPART = {Part = true}
local function new_instance(cn)
  local props = class_props(cn)
  if not props then error('Unable to create an Instance of type "' .. tostring(cn) .. '"') end
  local data = {class = cn, store = {}, children = {}, attrs = {}, parent = nil, destroyed = false}
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
  function methods.IsA(_, c) return c == cn or c == "Instance" or (c == "BasePart" and BASEPART[cn] == true) end
  function methods.SetAttribute(_, k, v) data.attrs[k] = v end
  function methods.GetAttribute(_, k) return data.attrs[k] end
  function methods.GetFullName()
    local parts, cur = {}, self
    while cur ~= nil do
      table.insert(parts, 1, cur.Name)
      cur = cur.Parent
    end
    return table.concat(parts, ".")
  end
  function methods.Destroy()
    data.destroyed = true
    if data.parent then
      local siblings = data.parent_data.children
      for i, c in ipairs(siblings) do if c == self then table.remove(siblings, i) break end end
    end
    data.parent = nil
  end
  function methods.Emit(_, n)
    if cn ~= "ParticleEmitter" then error("Emit is only available on ParticleEmitter") end
    assert(type(n) == "number" and n >= 0, "Emit expects a non-negative number")
    emits[data.store.Name] = (emits[data.store.Name] or 0) + n
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
    if not check(ty, v) then
      error("Unable to assign property " .. k .. ". " .. ty .. " expected, got " .. typeof(v))
    end
    data.store[k] = v
  end
  return setmetatable(self, meta)
end

Instance = {new = new_instance}

-- Services
local services = {}
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
    if n == #v then
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
local tags = {}
services.CollectionService = {AddTag = function(_, inst, tag) tags[#tags+1] = {inst.Name, tag} end}
game = setmetatable({}, {__rbxtype = "DataModel", __index = function(_, k)
  if k == "GetService" then return function(_, name)
    if not services[name] then error("'" .. tostring(name) .. "' is not a valid Service name") end
    return services[name] end end
  if k == "FindFirstChild" then return function(_, name) return services[name] end end
  if k == "ClassName" then return "DataModel" end
  error("'" .. tostring(k) .. "' is not a valid member of DataModel")
end})
workspace_inst.__data.parent_name = nil

return {
  run = function(code)
    local f, err = load(code, "=effect")
    if not f then error("syntax error: " .. tostring(err)) end
    return f()
  end,
  printed = function() return json_encode(printed) end,
  emits = function() if next(emits) == nil then return "{}" end return json_encode(emits) end,
  tags = function() return json_encode(tags) end,
  workspace = workspace_inst,
}
"""


class MockRoblox:
    """One mock DataModel. ``run(code)`` executes Luau-compatible Lua and returns what the chunk returns."""

    def __init__(self, snapshot: dict):
        if LuaRuntime is None:
            raise RuntimeError("Simulation needs the optional 'lupa' package: pip install '.[simulate]'")
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        loader = self.lua.eval("function(src, snap) local f = assert(load(src)) return f(snap) end")
        self.env = loader(PRELUDE, self.lua.table_from(snapshot, recursive=True))

    def run(self, code: str):
        return self.env.run(code)

    @property
    def printed(self) -> list[str]:
        return json.loads(self.env.printed())

    @property
    def emits(self) -> dict:
        return json.loads(self.env.emits())

    @property
    def tags(self) -> list:
        return json.loads(self.env.tags())

    def make_folder(self, name: str):
        """Create workspace.<name> (a Folder) so scripts have a target to build under."""
        mk = self.lua.eval("function(ws, name) local f = Instance.new('Folder'); f.Name = name; f.Parent = ws; return f end")
        return mk(self.env.workspace, name)


def simulate(code: str, snapshot: dict, *, preexisting_foreign: str | None = None, target: str | None = None) -> dict:
    """Run ``code`` on a fresh mock DataModel. Returns status, returned JSON, printed lines, emits, tags.

    ``preexisting_foreign`` creates an unrelated Instance with that name under the target first, to prove the
    script refuses to replace things it did not create. ``target`` is a folder name created under workspace.
    """
    mock = MockRoblox(snapshot)
    if target:
        folder = mock.make_folder(target)
        if preexisting_foreign:
            mock.lua.eval("function(parent, name) local f = Instance.new('Folder'); f.Name = name; f.Parent = parent end")(folder, preexisting_foreign)
    elif preexisting_foreign:
        mock.make_folder(preexisting_foreign)
    try:
        returned = mock.run(code)
    except LuaError as exc:
        return {"ok": False, "error": str(exc).splitlines()[0], "printed": mock.printed}
    try:
        report = json.loads(returned) if isinstance(returned, str) else None
    except json.JSONDecodeError:
        report = None
    return {"ok": True, "report": report, "printed": mock.printed, "emits": mock.emits, "tags": mock.tags}
