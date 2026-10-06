"""A small fake Roblox world for running the generated scripts in tests and evals, with PLANTED FAULTS.

It extends the shared mock DataModel (``guide_core.mock.MockRoblox``, lupa) with what these scripts need and the shared mock does not model: a player and character, a humanoid
that walks, a downward raycast, pathfinding between regions, remotes with server handlers, bindable test hooks, ``LogService`` history, ``Stats``, ``RunService`` and a fake
``task`` clock. It is a MODEL written by this repository, not Studio: it shows that the generated scripts are coherent against it and that the evaluators catch the planted faults,
nothing more. In particular it assumes (and real Studio may differ): that an erroring RemoteFunction handler logs an error line AND raises in the caller; that
``Heartbeat:Wait()`` returns the frame time; that ``Stats:GetTotalMemoryUsageMb()`` exists.

Planted faults (``faults=`` set of names):
    unreachable_room    the Vault region is not connected to the hub: pathfinding says NoPath
    path_stops_short    the path to Shop reports Success but ends 9 studs from the target
    remote_errors       BuyUpgrade's handler indexes a missing table entry and raises (valid calls and bad input)
    remote_accepts_bad  BuyUpgrade's handler accepts any payload
    event_errors        EquipTool's handler raises on a non-string
    boot_error          a script error is logged at boot
    boot_warning        an "Infinite yield possible" warning is logged at boot
    missing_folder      ServerScriptService.Systems does not exist
    currency_bug        buying an upgrade does not deduct the price
    sell_doubles        selling pays twice
    negative_balance    buying does not check the balance
    spawn_stuck         the character cannot move
    spawn_lava          the spawn stands on a part named Lava
    spawn_void          the character spawns below the world with no floor
    no_character        no character ever appears
    data_loses_value    the save hook skips the Ore value
"""

from __future__ import annotations

import json
from typing import Any

from ..guide_adapter import mock as _mock

FAULTS = ("unreachable_room", "path_stops_short", "remote_errors", "remote_accepts_bad", "event_errors", "boot_error", "boot_warning", "missing_folder", "currency_bug",
          "sell_doubles", "negative_balance", "spawn_stuck", "spawn_lava", "spawn_void", "no_character", "data_loses_value")

LUA = r'''
FAULTS = FAULTS or {}
local WORLD = {time = 0, log = {}, floors = {}, humanoids = {}, links = {}, regions = {}, saved = {}, memory_mb = 410, leak_mb_per_min = 0, frame_ms = 16.6}
__WORLD = WORLD

Enum = {
  Material = {Air = "Enum.Material.Air", Grass = "Enum.Material.Grass", Slate = "Enum.Material.Slate"},
  PathStatus = {Success = "Enum.PathStatus.Success", NoPath = "Enum.PathStatus.NoPath", ClosestNoPath = "Enum.PathStatus.ClosestNoPath",
                ClosestOutOfRange = "Enum.PathStatus.ClosestOutOfRange", FailStartNotEmpty = "Enum.PathStatus.FailStartNotEmpty", FailFinishNotEmpty = "Enum.PathStatus.FailFinishNotEmpty"},
  MessageType = {MessageOutput = "Enum.MessageType.MessageOutput", MessageInfo = "Enum.MessageType.MessageInfo", MessageWarning = "Enum.MessageType.MessageWarning",
                 MessageError = "Enum.MessageType.MessageError"},
}

local Vmt = {}
Vmt.__index = function(t, k)
  if k == "Magnitude" then return math.sqrt(t.X * t.X + t.Y * t.Y + t.Z * t.Z) end
  return nil
end
Vector3 = {}
function Vector3.new(x, y, z) return setmetatable({X = x or 0, Y = y or 0, Z = z or 0}, Vmt) end
Vmt.__add = function(a, b) return Vector3.new(a.X + b.X, a.Y + b.Y, a.Z + b.Z) end
Vmt.__sub = function(a, b) return Vector3.new(a.X - b.X, a.Y - b.Y, a.Z - b.Z) end
Vmt.__mul = function(a, b)
  if type(a) == "number" then a, b = b, a end
  return Vector3.new(a.X * b, a.Y * b, a.Z * b)
end
Vmt.__eq = function(a, b) return a.X == b.X and a.Y == b.Y and a.Z == b.Z end
Vmt.__tostring = function(a) return a.X .. ", " .. a.Y .. ", " .. a.Z end

function WORLD.add_log(msg, kind, at)
  WORLD.log[#WORLD.log + 1] = {message = msg, messageType = Enum.MessageType[kind or "MessageOutput"], timestamp = at or WORLD.time}
end
local orig_print = print
print = function(...)
  orig_print(...)
  local t = {}
  for i = 1, select("#", ...) do t[#t + 1] = tostring(select(i, ...)) end
  WORLD.add_log(table.concat(t, " "), "MessageOutput")
end

local PARENT = {Part = "BasePart", MeshPart = "BasePart", SpawnLocation = "Part", BasePart = "PVInstance", Model = "PVInstance", PVInstance = "Instance",
  Script = "LuaSourceContainer", LocalScript = "LuaSourceContainer", ModuleScript = "LuaSourceContainer", LuaSourceContainer = "Instance",
  NumberValue = "ValueBase", IntValue = "ValueBase", StringValue = "ValueBase", BoolValue = "ValueBase", ValueBase = "Instance"}
local M = {}
local Node_mt = {__index = function(t, k) return M[k] end}
local ROOT
local function mk(class, name, props, parent)
  local n = setmetatable({ClassName = class, Name = name, _kids = {}, _attrs = {}}, Node_mt)
  for k, v in pairs(props or {}) do n[k] = v end
  if parent then n.Parent = parent; parent._kids[#parent._kids + 1] = n end
  return n
end
function M.FindFirstChild(self, name, recursive)
  for _, c in ipairs(self._kids) do if c.Name == name then return c end end
  if recursive then
    for _, c in ipairs(self._kids) do
      local r = M.FindFirstChild(c, name, true)
      if r then return r end
    end
  end
  return nil
end
function M.FindFirstChildOfClass(self, cls)
  for _, c in ipairs(self._kids) do if c.ClassName == cls then return c end end
  return nil
end
function M.IsA(self, cls)
  local c = self.ClassName
  while c do
    if c == cls then return true end
    c = PARENT[c]
  end
  return cls == "Instance"
end
function M.FindFirstChildWhichIsA(self, cls, recursive)
  for _, c in ipairs(self._kids) do if M.IsA(c, cls) then return c end end
  if recursive then
    for _, c in ipairs(self._kids) do
      local r = M.FindFirstChildWhichIsA(c, cls, true)
      if r then return r end
    end
  end
  return nil
end
function M.GetChildren(self) local o = {} for i, c in ipairs(self._kids) do o[i] = c end return o end
function M.GetDescendants(self)
  local o = {}
  local function rec(n) for _, c in ipairs(n._kids) do o[#o + 1] = c; rec(c) end end
  rec(self)
  return o
end
function M.GetAttribute(self, k) return self._attrs[k] end
function M.SetAttribute(self, k, v) self._attrs[k] = v end
function M.GetFullName(self)
  local parts, n = {}, self
  while n and n ~= ROOT do table.insert(parts, 1, n.Name); n = n.Parent end
  return table.concat(parts, ".")
end
function M.GetPivot(self) return {Position = self.PivotPosition or self.Position or Vector3.new(0, 0, 0)} end
function M.GetPlayers(self) local o = {} for _, c in ipairs(self._kids) do if c.ClassName == "Player" then o[#o + 1] = c end end return o end
function M.MoveTo(self, target) self._target = target end
function M.Invoke(self, ...) return self._fn(...) end
local function fail_log(self, err)
  WORLD.add_log(tostring(err), "MessageError")
  WORLD.add_log("Stack Begin", "MessageInfo")
  WORLD.add_log("Script '" .. self:GetFullName() .. "', Line 1", "MessageInfo")
  WORLD.add_log("Stack End", "MessageInfo")
end
function M.InvokeServer(self, ...)
  local ok, res = pcall(self._handler, WORLD.player, ...)
  if not ok then fail_log(self, res); error(res, 0) end
  return res
end
function M.FireServer(self, ...)
  local ok, res = pcall(self._handler, WORLD.player, ...)
  if not ok then fail_log(self, res) end
end

-- the data model ------------------------------------------------------------------------------------------------------------
local orig_game = game
ROOT = mk("DataModel", "Game")
ROOT.PlaceId = orig_game.PlaceId
ROOT.Name = orig_game.Name
local services = {}
local function service(name) local s = mk("Folder", name, nil, ROOT); s.ClassName = name; return s end
local Workspace = service("Workspace")
local ReplicatedStorage = service("ReplicatedStorage")
local ServerScriptService = service("ServerScriptService")
service("ServerStorage")
local Players = service("Players")
local LogService = {GetLogHistory = function(self) local o = {} for i, e in ipairs(WORLD.log) do o[i] = e end return o end}
local Stats = {GetTotalMemoryUsageMb = function(self) return WORLD.memory_mb + WORLD.leak_mb_per_min * ((WORLD.time - WORLD.t0) / 60) end}
local Heartbeat = {Wait = function(self) return WORLD.frame_ms / 1000 end}
local RunService = {Heartbeat = Heartbeat}
local PathfindingService = {}
local pseudo = {LogService = LogService, Stats = Stats, RunService = RunService, PathfindingService = PathfindingService, HttpService = orig_game:GetService("HttpService")}
ROOT.GetService = function(self, name)
  local c = M.FindFirstChild(ROOT, name)
  if c then return c end
  if pseudo[name] then return pseudo[name] end
  error("'" .. tostring(name) .. "' is not a valid Service name", 0)
end
game = ROOT
workspace = Workspace

-- time and walking ----------------------------------------------------------------------------------------------------------
function WORLD.advance(dt)
  WORLD.time = WORLD.time + dt
  for _, h in ipairs(WORLD.humanoids) do
    if h._target and not h._stuck then
      local hrp = h._hrp
      local d = h._target - hrp.Position
      local dist = d.Magnitude
      local step = math.min(16 * dt, dist)
      if dist > 0 then hrp.Position = hrp.Position + d * (step / dist) end
      if dist <= step then h._target = nil end
    end
  end
end
task = {
  wait = function(dt) dt = dt or 0.03; WORLD.advance(dt); return dt end,
  spawn = function(f, ...)
    local co = coroutine.create(f)
    local ok, err = coroutine.resume(co, ...)
    if not ok then WORLD.add_log("task.spawn error: " .. tostring(err), "MessageError") end
    return co
  end,
}
RaycastParams = {new = function() return {FilterDescendantsInstances = {}} end}
Workspace.Raycast = function(self, origin, dir, params)
  local best
  for _, f in ipairs(WORLD.floors) do
    if origin.X >= f.x0 and origin.X <= f.x1 and origin.Z >= f.z0 and origin.Z <= f.z1 and f.top <= origin.Y + 0.001 and f.top >= origin.Y + dir.Y then
      if not best or f.top > best.top then best = f end
    end
  end
  if not best then return nil end
  return {Instance = best.inst, Position = Vector3.new(origin.X, best.top, origin.Z), Material = Enum.Material.Grass}
end

-- pathfinding between named regions -----------------------------------------------------------------------------------------
local function key(v) return string.format("%d,%d,%d", math.floor(v.X + 0.5), math.floor(v.Y + 0.5), math.floor(v.Z + 0.5)) end
PathfindingService.CreatePath = function(self, params)
  local p = {Status = Enum.PathStatus.NoPath, _wps = {}}
  function p.ComputeAsync(self2, a, b)
    local ra, rb = WORLD.regions[key(a)], WORLD.regions[key(b)]
    if ra == nil or rb == nil then self2.Status = Enum.PathStatus.NoPath; return end
    if ra == rb or WORLD.links[ra .. ">" .. rb] then
      local end_pos = b
      if FAULTS.path_stops_short and rb == "shop" then end_pos = b - Vector3.new(9, 0, 0) end
      self2.Status = Enum.PathStatus.Success
      self2._wps = {{Position = a}, {Position = (a + b) * 0.5}, {Position = end_pos}}
    else
      self2.Status = Enum.PathStatus.NoPath
    end
  end
  function p.GetWaypoints(self2) return self2._wps end
  return p
end

-- the map -------------------------------------------------------------------------------------------------------------------
local Map = mk("Folder", "Map", nil, Workspace)
local function floor(name, x0, x1, z0, z1, top, parent)
  local part = mk("Part", name, {Position = Vector3.new((x0 + x1) / 2, top - 0.5, (z0 + z1) / 2), Anchored = true}, parent or Map)
  WORLD.floors[#WORLD.floors + 1] = {inst = part, x0 = x0, x1 = x1, z0 = z0, z1 = z1, top = top}
  return part
end
local ground = floor(FAULTS.spawn_lava and "Lava" or "Ground", -100, 100, -100, 100, 1)
local spawn = mk("SpawnLocation", "Spawn", {Position = Vector3.new(0, 1.5, 0)}, Map)
local function area(name, x, y, z, region)
  local m = mk("Model", name, {PivotPosition = Vector3.new(x, y, z)}, Map)
  mk("Part", "Target", {Position = Vector3.new(x, y, z)}, m)
  WORLD.regions[string.format("%d,%d,%d", x, y, z)] = region
  return m
end
WORLD.regions["0,2,0"] = "hub"
spawn.PivotPosition = Vector3.new(0, 2, 0)
area("Shop", 20, 2, 0, "shop")
area("MineEntrance", 40, 2, 10, "mine")
area("Vault", 60, 2, -30, "vault")
for _, r in ipairs({"shop", "mine"}) do WORLD.links["hub>" .. r] = true; WORLD.links[r .. ">hub"] = true end
if not FAULTS.unreachable_room then WORLD.links["hub>vault"] = true; WORLD.links["vault>hub"] = true end
WORLD.links["shop>mine"] = true; WORLD.links["mine>shop"] = true
if not FAULTS.missing_folder then mk("Folder", "Systems", nil, ServerScriptService) end
mk("Script", "Boot", {Disabled = false}, ServerScriptService)
mk("Script", "Economy", {Disabled = false}, ServerScriptService)
mk("ModuleScript", "Prices", nil, ServerScriptService)
mk("LocalScript", "Hud", {Disabled = false}, ReplicatedStorage)

-- a player ----------------------------------------------------------------------------------------------------------------
local player = mk("Player", "Tester", nil, Players)
local stats = mk("Folder", "leaderstats", nil, player)
local coins = mk("IntValue", "Coins", {Value = START_COINS or 0}, stats)
local ore = mk("IntValue", "Ore", {Value = 0}, stats)
WORLD.player = player
if CONTEXT == "client" then Players.LocalPlayer = player end
if not FAULTS.no_character then
  local char = mk("Model", "Tester", nil, Workspace)
  local y = FAULTS.spawn_void and -80 or 3
  local hrp = mk("Part", "HumanoidRootPart", {Position = Vector3.new(0, y, 0)}, char)
  local hum = mk("Humanoid", "Humanoid", {Health = 100, _hrp = hrp, _stuck = FAULTS.spawn_stuck and true or false}, char)
  WORLD.humanoids[#WORLD.humanoids + 1] = hum
  player.Character = char
  if FAULTS.spawn_void then WORLD.floors = {} end
end

-- remotes and QA hooks ----------------------------------------------------------------------------------------------------
local PRICES = {PickaxeII = 10, DiamondPick = 40}
local Remotes = mk("Folder", "Remotes", nil, ReplicatedStorage)
local buy = mk("RemoteFunction", "BuyUpgrade", nil, Remotes)
local TYPO = {PickaxeI = {Cost = 10}}
buy._handler = function(pl, item)
  if FAULTS.remote_accepts_bad then return true end
  if FAULTS.remote_errors then
    local entry = TYPO[item]
    if entry == nil then error("ServerScriptService.Remotes.BuyUpgrade:12: attempt to index nil with 'Cost'", 0) end
  end
  if type(item) ~= "string" or PRICES[item] == nil then return {ok = false, error = "unknown item"} end
  if coins.Value < PRICES[item] then return {ok = false, error = "funds"} end
  coins.Value = coins.Value - PRICES[item]
  return true
end
local ping = mk("RemoteFunction", "Ping", nil, Remotes)
ping._handler = function(pl, msg)
  if msg ~= "hello" then return false end
  return "pong"
end
local equip = mk("RemoteEvent", "EquipTool", nil, Remotes)
equip._handler = function(pl, tool)
  if FAULTS.event_errors and type(tool) ~= "string" then
    error("ServerScriptService.Remotes.EquipTool:7: attempt to get length of a " .. type(tool) .. " value", 0)
  end
  if type(tool) ~= "string" then return end
end
local QA = mk("Folder", "QA", nil, ServerScriptService)
local RQA = mk("Folder", "QA", nil, ReplicatedStorage)
mk("BoolValue", "TestMode", {Value = TEST_MODE ~= false}, RQA)
local function hook(name, fn) local h = mk("BindableFunction", name, nil, QA); h._fn = fn; return h end
hook("Mine", function(pl) ore.Value = ore.Value + 1; return true end)
hook("Sell", function(pl)
  local per = FAULTS.sell_doubles and 10 or 5
  coins.Value = coins.Value + ore.Value * per
  ore.Value = 0
  return true
end)
hook("Buy", function(pl, item)
  local price = PRICES[item]
  if price == nil then return false end
  if not FAULTS.negative_balance and coins.Value < price then return false end
  if not FAULTS.currency_bug then coins.Value = coins.Value - price end
  return true
end)
hook("SaveData", function(pl)
  WORLD.saved = {Coins = coins.Value}
  if not FAULTS.data_loses_value then WORLD.saved.Ore = ore.Value end
  return true
end)
hook("LoadData", function(pl)
  if WORLD.saved.Coins ~= nil then coins.Value = WORLD.saved.Coins end
  if WORLD.saved.Ore ~= nil then ore.Value = WORLD.saved.Ore end
  return true
end)
hook("DataIsTestMode", function(pl) return M.FindFirstChild(RQA, "TestMode").Value == true end)

-- boot log ---------------------------------------------------------------------------------------------------------------
WORLD.add_log("Server started", "MessageOutput", 0.1)
WORLD.add_log("ServerScriptService.Economy loaded 2 upgrades", "MessageOutput", 0.4)
if FAULTS.boot_error then
  WORLD.add_log("ServerScriptService.Boot:12: attempt to index nil with 'Spawn'", "MessageError", 1.2)
  WORLD.add_log("Stack Begin", "MessageInfo", 1.2)
  WORLD.add_log("Script 'ServerScriptService.Boot', Line 12", "MessageInfo", 1.2)
  WORLD.add_log("Stack End", "MessageInfo", 1.2)
end
if FAULTS.boot_warning then WORLD.add_log("Infinite yield possible on 'Workspace.Map:WaitForChild(\"Gate\")'", "MessageWarning", 2.0) end
WORLD.time = 3.0
WORLD.t0 = 3.0

function WORLD.set_perf(memory_mb, frame_ms, leak) if memory_mb then WORLD.memory_mb = memory_mb end if frame_ms then WORLD.frame_ms = frame_ms end if leak then WORLD.leak_mb_per_min = leak end end
function WORLD.add_parts(n) local f = M.FindFirstChild(Workspace, "Extra") or mk("Folder", "Extra", nil, Workspace) for i = 1, n do mk("Part", "Extra" .. i, {Position = Vector3.new(i, 1, 1)}, f) end end
function WORLD.value(name) local v = M.FindFirstChild(stats, name) return v and v.Value or nil end
function WORLD.set_value(name, v) M.FindFirstChild(stats, name).Value = v end
function WORLD.set_test_mode(on) M.FindFirstChild(RQA, "TestMode").Value = on end
function WORLD.log_json()
  local out = {}
  for i, e in ipairs(WORLD.log) do out[i] = {message = e.message, messageType = e.messageType, timestamp = e.timestamp} end
  return game:GetService("HttpService"):JSONEncode(out)
end
'''


class World:
    """One fake place. ``run(luau)`` executes a generated script and returns its JSON string (or raises ``RuntimeError`` carrying the Luau error text)."""

    def __init__(self, faults=(), *, context: str = "server", start_coins: int = 0, test_mode: bool = True, name: str = "Demo Mine Main (SYNTHETIC)", place_id: int = 0):
        bad = set(faults) - set(FAULTS)
        if bad:
            raise ValueError(f"unknown fault(s) {sorted(bad)}; known: {list(FAULTS)}")
        if context not in ("server", "client"):
            raise ValueError("context must be server or client")
        if not _mock.available():
            raise RuntimeError("the mock world needs the optional 'lupa' package (pip install 'guide-core[mock]')")
        self.faults = frozenset(faults)
        self.m = _mock.MockRoblox()
        self.m.set_place(name, place_id)
        header = "FAULTS = {" + ", ".join(f"{f} = true" for f in sorted(self.faults)) + "}\n" + f"START_COINS = {int(start_coins)}\nCONTEXT = \"{context}\"\nTEST_MODE = {'true' if test_mode else 'false'}\n"
        self.m.run(header + LUA)
        self._world = self.m.lua.globals()["__WORLD"]

    def run(self, luau: str) -> str:
        try:
            return self.m.run(luau)
        except Exception as exc:  # lupa raises LuaError; surface the Luau message like the hub would
            raise RuntimeError(str(exc)) from exc

    def run_json(self, luau: str) -> dict:
        return json.loads(self.run(luau))

    def advance(self, seconds: float) -> None:
        self._world["advance"](seconds)

    def set_perf(self, memory_mb: float | None = None, frame_ms: float | None = None, leak_mb_per_min: float | None = None) -> None:
        self._world["set_perf"](memory_mb, frame_ms, leak_mb_per_min)

    def add_parts(self, n: int) -> None:
        self._world["add_parts"](int(n))

    def value(self, name: str) -> Any:
        return self._world["value"](name)

    def set_value(self, name: str, v: Any) -> None:
        self._world["set_value"](name, v)

    def set_test_mode(self, on: bool) -> None:
        self._world["set_test_mode"](bool(on))

    def console_entries(self) -> list[dict]:
        """The log as a list of ``{message, messageType, timestamp}`` (the LogService shape)."""
        return json.loads(self._world["log_json"]())

    def console_text(self, base: str = "10:00:00") -> str:
        """The log as Studio-like text lines ``HH:MM:SS.mmm  message`` with NO level tag (levels must be inferred from the message)."""
        h, mi, s = (int(x) for x in base.split(":"))
        t0 = h * 3600 + mi * 60 + s
        out = []
        for e in self.console_entries():
            t = t0 + float(e["timestamp"])
            hh, rem = divmod(int(t), 3600)
            mm, ss = divmod(rem, 60)
            ms = int(round((t - int(t)) * 1000))
            out.append(f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}  {e['message']}")
        return "\n".join(out)
