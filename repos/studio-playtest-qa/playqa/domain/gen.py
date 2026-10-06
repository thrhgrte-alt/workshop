"""Assertion-script generators: one Luau script per check kind.

Every script
* starts with the shared place guard (it stops with ``wrong place`` unless the open place is the named one),
* prints ``PLAYQA_MARK`` / ``PLAYQA_PROBE`` lines so the console can be cut per check and per probe,
* collects ``assertions`` and ``measures`` into ONE table and returns it as a JSON string (``playqa.result/1``, see ``schema.py``),
* contains no network call, no DataStore call, no publishing, no ``require`` and no destruction: it passes the shared Luau lint (``luau_safety.assert_safe``).

The Luau is written in the subset that both Luau and the Lua 5.4 inside the test mock accept (no type annotations, no ``+=``, no ``continue``), so the SAME text is what the tests
run on the mock DataModel with planted faults. Every value that comes from configuration is embedded through ``luau_safety.quote_string`` / ``validate_path`` / numeric
formatting, never by string splicing of raw text.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..guide_adapter import luau_safety as LS
from .checks import setting
from .config import SECTION_OF

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")

HEADER = '''-- playqa generated check script (playqa.result/1). check: @CHECK@  repeat: @REPEAT@  place: @LABEL@
-- Runs only in the open Studio session. No network, no data-store calls, no publishing; it ends by returning one JSON string.
@GUARD@
local CHECK_ID, KIND, REPEAT_NO = @CHECK_Q@, @KIND_Q@, @REPEAT@
local result = {schema = "playqa.result/1", check_id = CHECK_ID, kind = KIND, repeat_no = REPEAT_NO,
  place = {place_id = game.PlaceId, name = game.Name}, assertions = {}, measures = {}}
local function assertion(id, ok, measured, expected, detail, subject)
  result.assertions[#result.assertions + 1] = {id = id, subject = subject, ok = ok and true or false, measured = measured, expected = expected, detail = detail}
end
local function resolve(path, root)
  local node = root or game
  for part in string.gmatch(path, "[^.]+") do
    if node == nil then return nil end
    node = node:FindFirstChild(part)
  end
  return node
end
local function r2(x) return math.floor(x * 100 + 0.5) / 100 end
local function vec(v) return {r2(v.X), r2(v.Y), r2(v.Z)} end
print("PLAYQA_MARK " .. CHECK_ID .. " " .. REPEAT_NO .. " start")
'''

FOOTER = '''print("PLAYQA_MARK " .. CHECK_ID .. " " .. REPEAT_NO .. " end")
local encoded = game:GetService("HttpService"):JSONEncode(result)
print("PLAYQA_RESULT " .. encoded)
return encoded
'''

BOOT = '''local EXPECTED_SERVICES = @SERVICES@
local EXPECTED_PATHS = @PATHS@
local WINDOW = @WINDOW@
local USE_LOG = @USE_LOG@
local missing_services, missing_paths = {}, {}
for _, name in ipairs(EXPECTED_SERVICES) do
  local ok, svc = pcall(function() return game:GetService(name) end)
  if not ok or svc == nil then missing_services[#missing_services + 1] = name end
end
for _, path in ipairs(EXPECTED_PATHS) do
  if resolve(path) == nil then missing_paths[#missing_paths + 1] = path end
end
result.measures.expected_services = #EXPECTED_SERVICES
result.measures.expected_paths = #EXPECTED_PATHS
result.measures.missing_services = missing_services
result.measures.missing_paths = missing_paths
result.measures.window_seconds = WINDOW
assertion("services_exist", #missing_services == 0, #EXPECTED_SERVICES - #missing_services, #EXPECTED_SERVICES,
  #missing_services == 0 and "all present" or ("missing: " .. table.concat(missing_services, ", ")))
assertion("paths_exist", #missing_paths == 0, #EXPECTED_PATHS - #missing_paths, #EXPECTED_PATHS,
  #missing_paths == 0 and "all present" or ("missing: " .. table.concat(missing_paths, ", ")))
if USE_LOG then
  local ok, history = pcall(function() return game:GetService("LogService"):GetLogHistory() end)
  if ok and type(history) == "table" then
    local errors, warnings, first = {}, 0, nil
    for _, entry in ipairs(history) do
      local ts = tonumber(entry.timestamp) or 0
      if first == nil then first = ts end
      if ts - first <= WINDOW then
        local kind = tostring(entry.messageType)
        if string.find(kind, "MessageError") then errors[#errors + 1] = string.sub(tostring(entry.message), 1, 200)
        elseif string.find(kind, "MessageWarning") then warnings = warnings + 1 end
      end
    end
    result.measures.log_errors = errors
    result.measures.log_warnings = warnings
    assertion("log_service_clean", #errors == 0, #errors, 0, #errors == 0 and "no error lines in the LogService history" or errors[1])
  else
    result.measures.log_service = "unavailable"
  end
end
'''

SPAWN = '''local Players = game:GetService("Players")
local TIMEOUT, PROBE_D, PROBE_S, VOID_Y, RAY, MIN_FRAC = @TIMEOUT@, @PROBE_D@, @PROBE_S@, @VOID_Y@, @RAY@, @MIN_FRAC@
local INVALID_NAMES = @INVALID_NAMES@
local HAZARD_ATTR = @HAZARD@
local player = Players.LocalPlayer or Players:GetPlayers()[1]
assertion("player_present", player ~= nil, player ~= nil, true, player and ("player " .. player.Name) or "no player in this play session")
local function body()
  if player == nil then return end
  local elapsed, char = 0, player.Character
  while char == nil and elapsed < TIMEOUT do
    elapsed = elapsed + task.wait(0.1)
    char = player.Character
  end
  assertion("character_spawned", char ~= nil, char ~= nil, true, char and "character found" or ("no character after " .. r2(elapsed) .. " s"))
  if char == nil then return end
  local hum = char:FindFirstChildOfClass("Humanoid")
  local hrp = char:FindFirstChild("HumanoidRootPart")
  if hum == nil or hrp == nil then
    assertion("alive", false, false, true, "the character has no Humanoid or no HumanoidRootPart")
    return
  end
  local pos = hrp.Position
  result.measures.spawn_position = vec(pos)
  result.measures.waited_for_character = r2(elapsed)
  assertion("alive", hum.Health > 0, r2(hum.Health), "above 0", "health " .. r2(hum.Health))
  assertion("above_void", pos.Y > VOID_Y, r2(pos.Y), "above " .. VOID_Y, "y = " .. r2(pos.Y))
  local params = RaycastParams.new()
  params.FilterDescendantsInstances = {char}
  local hit = workspace:Raycast(pos, Vector3.new(0, -RAY, 0), params)
  assertion("floor_found", hit ~= nil, hit ~= nil, true, hit and ("surface " .. hit.Instance:GetFullName()) or ("nothing within " .. RAY .. " studs below the character"))
  if hit ~= nil then
    local inst = hit.Instance
    local bad = INVALID_NAMES[inst.Name] or (inst.Parent ~= nil and INVALID_NAMES[inst.Parent.Name]) or false
    if HAZARD_ATTR ~= nil and inst:GetAttribute(HAZARD_ATTR) then bad = true end
    result.measures.floor = {name = inst.Name, path = inst:GetFullName(), material = tostring(hit.Material)}
    assertion("floor_valid", not bad, inst.Name, "not on the invalid list", bad and ("standing on " .. inst:GetFullName()) or ("standing on " .. inst:GetFullName() .. ", not on the invalid list"))
  end
  local best, tried = 0, {}
  local dirs = {Vector3.new(1, 0, 0), Vector3.new(-1, 0, 0), Vector3.new(0, 0, 1), Vector3.new(0, 0, -1)}
  for _, dir in ipairs(dirs) do
    local start = hrp.Position
    hum:MoveTo(start + dir * PROBE_D)
    local waited = 0
    while waited < PROBE_S do waited = waited + task.wait(0.1) end
    local moved = (hrp.Position - start).Magnitude
    tried[#tried + 1] = r2(moved)
    if moved > best then best = moved end
    if best >= PROBE_D * MIN_FRAC then break end
  end
  result.measures.probe = {distance = PROBE_D, seconds = PROBE_S, best_moved = r2(best), tried = tried}
end
body()
'''

REACH = '''local PathfindingService = game:GetService("PathfindingService")
local AGENT = {AgentRadius = @RADIUS@, AgentHeight = @HEIGHT@, AgentCanJump = true}
local SPAWN_PATH = @SPAWN_PATH@
local AREAS = @AREAS@
local HOPS = @HOPS@
local function position_of(inst)
  if inst == nil then return nil end
  local ok, pivot = pcall(function() return inst:GetPivot() end)
  if ok and pivot ~= nil then return pivot.Position end
  return nil
end
local spawn_inst, spawn_source = nil, "none"
if SPAWN_PATH ~= nil then
  spawn_inst, spawn_source = resolve(SPAWN_PATH), SPAWN_PATH
else
  spawn_inst, spawn_source = workspace:FindFirstChildWhichIsA("SpawnLocation", true), "first SpawnLocation in Workspace"
end
local spawn_pos = position_of(spawn_inst)
assertion("spawn_resolved", spawn_pos ~= nil, spawn_pos ~= nil, true, spawn_pos and ("spawn from " .. spawn_source) or ("no spawn found (" .. spawn_source .. ")"))
local nodes = {}
local unresolved, area_rows = {}, {}
if spawn_pos ~= nil then nodes[#nodes + 1] = {name = "spawn", position = spawn_pos} end
for _, a in ipairs(AREAS) do
  local inst = resolve(a.path)
  local pos = position_of(inst)
  area_rows[#area_rows + 1] = {name = a.name, path = a.path, position = pos and vec(pos) or nil}
  if pos == nil then unresolved[#unresolved + 1] = a.name else nodes[#nodes + 1] = {name = a.name, position = pos} end
end
assertion("areas_resolved", #unresolved == 0, #AREAS - #unresolved, #AREAS, #unresolved == 0 and "every area resolved" or ("not found: " .. table.concat(unresolved, ", ")))
local function path_between(a, b)
  local path = PathfindingService:CreatePath(AGENT)
  local ok, err = pcall(function() path:ComputeAsync(a.position, b.position) end)
  if not ok then return {from = a.name, to = b.name, status = "Error", waypoints = 0, length = 0, end_gap = -1, error = string.sub(tostring(err), 1, 120)} end
  local status = string.match(tostring(path.Status), "(%w+)$") or tostring(path.Status)
  local wps = path:GetWaypoints()
  local n = #wps
  local length, gap = 0, -1
  for i = 2, n do length = length + (wps[i].Position - wps[i - 1].Position).Magnitude end
  if n > 0 then gap = (wps[n].Position - b.position).Magnitude end
  return {from = a.name, to = b.name, status = status, waypoints = n, length = r2(length), end_gap = r2(gap)}
end
local paths = {}
if spawn_pos ~= nil then
  for i, a in ipairs(nodes) do
    for j, b in ipairs(nodes) do
      if i ~= j and (HOPS or a.name == "spawn") and b.name ~= "spawn" then paths[#paths + 1] = path_between(a, b) end
    end
  end
end
result.measures.spawn = {path = spawn_source, position = spawn_pos and vec(spawn_pos) or nil}
result.measures.areas = area_rows
result.measures.unresolved = unresolved
result.measures.paths = paths
result.measures.hops = HOPS
result.measures.agent = {radius = AGENT.AgentRadius, height = AGENT.AgentHeight}
'''

REMOTES = '''local TIMEOUT = @TIMEOUT@
local REMOTES = @REMOTES@
local function summarize(res)
  local t = type(res)
  if t == "table" then
    local out = {kind = "table"}
    for _, k in ipairs({"ok", "success", "error"}) do
      local v = res[k]
      if v ~= nil then out[k] = (type(v) == "string") and string.sub(v, 1, 80) or v end
    end
    return out
  end
  if t == "string" then return {kind = "string", value = string.sub(res, 1, 80)} end
  return {kind = t, value = res}
end
local function looks_rejected(res, reject)
  if res == nil or res == false then return true end
  if type(res) == "table" and (res.ok == false or res.success == false or res.error ~= nil) then return true end
  for _, v in ipairs(reject) do
    if res == v then return true end
  end
  return false
end
local function call(remote, class, args, reject)
  local rec = {ok = false, responded = false, timed_out = false}
  if class == "RemoteFunction" then
    local done = false
    task.spawn(function()
      local ok, res = pcall(function() return remote:InvokeServer(table.unpack(args, 1, args.n)) end)
      done = true
      rec.ok = ok
      if ok then
        rec.responded = true
        rec.value = summarize(res)
        rec.rejected = looks_rejected(res, reject)
      else
        rec.error = string.sub(tostring(res), 1, 160)
      end
    end)
    local waited = 0
    while not done and waited < TIMEOUT do waited = waited + task.wait(0.05) end
    if not done then rec.timed_out = true end
  else
    local ok, err = pcall(function() remote:FireServer(table.unpack(args, 1, args.n)) end)
    rec.ok = ok
    if not ok then rec.error = string.sub(tostring(err), 1, 160) end
    task.wait(0.2)
  end
  return rec
end
local report = {}
for _, spec in ipairs(REMOTES) do
  local remote = resolve(spec.path)
  local class_ok = remote ~= nil and remote.ClassName == spec.class
  local row = {id = spec.id, path = spec.path, class = spec.class, found = remote ~= nil, class_ok = class_ok, valid = {}, bad = {}}
  report[#report + 1] = row
  assertion("remote_found", class_ok, remote ~= nil and remote.ClassName or "missing", spec.class,
    class_ok and (spec.id .. " found") or (spec.id .. (remote == nil and " not found at " or " has another class at ") .. spec.path), spec.id)
  if class_ok then
    for i, args in ipairs(spec.valid) do
      print("PLAYQA_PROBE " .. spec.id .. " valid" .. i)
      local rec = call(remote, spec.class, args, spec.reject)
      rec.label = "valid" .. i
      row.valid[#row.valid + 1] = rec
    end
    for _, b in ipairs(spec.bad) do
      print("PLAYQA_PROBE " .. spec.id .. " " .. b.label)
      local rec = call(remote, spec.class, b.args, spec.reject)
      rec.label = b.label
      row.bad[#row.bad + 1] = rec
    end
  end
end
task.wait(0.5)
result.measures.timeout = TIMEOUT
result.measures.remotes = report
'''

ECONOMY = '''local Players = game:GetService("Players")
local player = Players.LocalPlayer or Players:GetPlayers()[1]
local SETTLE = @SETTLE@
local VALUES = @VALUES@
local STEPS = @STEPS@
local function read_values()
  local out, missing = {}, {}
  for _, v in ipairs(VALUES) do
    local inst = player and resolve(v.path, player) or nil
    if inst ~= nil and inst.Value ~= nil then out[v.id] = inst.Value else missing[#missing + 1] = v.id end
  end
  return out, missing
end
local function body()
  local start_values, missing = read_values()
  result.measures.start_values = start_values
  result.measures.missing_values = missing
  assertion("values_found", #missing == 0, #VALUES - #missing, #VALUES, #missing == 0 and "every watched value found" or ("not found under the player: " .. table.concat(missing, ", ")))
  if #missing > 0 then return end
  local steps = {}
  for _, step in ipairs(STEPS) do
    local before = read_values()
    local rec = {id = step.id, ok = true, calls = 0}
    local fn = resolve(step.path)
    if fn == nil then
      rec.ok = false
      rec.error = "action instance not found: " .. step.path
    else
      for i = 1, step.times do
        print("PLAYQA_PROBE economy " .. step.id)
        local ok, res = pcall(function()
          if step.kind == "bindable" then return fn:Invoke(player, table.unpack(step.args, 1, step.args.n))
          elseif step.kind == "remote_function" then return fn:InvokeServer(table.unpack(step.args, 1, step.args.n)) end
          fn:FireServer(table.unpack(step.args, 1, step.args.n))
          return nil
        end)
        rec.calls = rec.calls + 1
        if not ok then
          rec.ok = false
          rec.error = string.sub(tostring(res), 1, 160)
          break
        end
      end
    end
    task.wait(SETTLE)
    local after = read_values()
    rec.before, rec.after = before, after
    steps[#steps + 1] = rec
    assertion("step_ran", rec.ok, rec.ok, true, rec.ok and ("ran " .. rec.calls .. " call(s)") or rec.error, step.id)
  end
  result.measures.steps = steps
  result.measures.end_values = (read_values())
end
body()
'''

DATA = '''local Players = game:GetService("Players")
local player = Players.LocalPlayer or Players:GetPlayers()[1]
local SWITCH = @SWITCH@
local ACK = @ACK@
local SAVE_HOOK, LOAD_HOOK = @SAVE@, @LOAD@
local SETTLE = @SETTLE@
local WATCH = @WATCH@
local function body()
  local switch = resolve(SWITCH)
  local on = false
  if switch ~= nil then
    if switch:IsA("BoolValue") then on = switch.Value == true else on = switch:GetAttribute("Enabled") == true end
  end
  assertion("test_mode_on", on, on, true, on and "the test-mode switch is on" or "the test-mode switch is missing or off; nothing was touched")
  if not on then
    result.refused = "test-mode switch " .. SWITCH .. " is missing or off; no data hook was called"
    return
  end
  if player == nil then
    result.refused = "no player in this play session; no data hook was called"
    return
  end
  if ACK ~= nil then
    local hook = resolve(ACK)
    local ok, ans = false, nil
    if hook ~= nil then ok, ans = pcall(function() return hook:Invoke(player) end) end
    local acked = ok and ans == true
    assertion("test_mode_acknowledged", acked, acked, true, acked and "the game acknowledged test mode" or "the game did not acknowledge test mode")
    if not acked then
      result.refused = "the game did not acknowledge test mode; no data hook was called"
      return
    end
  end
  local save_hook, load_hook = resolve(SAVE_HOOK), resolve(LOAD_HOOK)
  if save_hook == nil or load_hook == nil then
    assertion("hooks_ran", false, false, true, "hook not found: " .. (save_hook == nil and SAVE_HOOK or LOAD_HOOK))
    return
  end
  local rows, originals = {}, {}
  for _, w in ipairs(WATCH) do
    local inst = resolve(w.path, player)
    local row = {id = w.id, found = inst ~= nil, marker = w.marker, reset = w.reset}
    rows[#rows + 1] = row
    if inst ~= nil then
      row.before = inst.Value
      originals[#originals + 1] = {inst, inst.Value}
      inst.Value = w.marker
    end
  end
  print("PLAYQA_PROBE data save")
  local ok1, err1 = pcall(function() return save_hook:Invoke(player) end)
  task.wait(SETTLE)
  for _, w in ipairs(WATCH) do
    local inst = resolve(w.path, player)
    if inst ~= nil then inst.Value = w.reset end
  end
  print("PLAYQA_PROBE data load")
  local ok2, err2 = pcall(function() return load_hook:Invoke(player) end)
  task.wait(SETTLE)
  local detail = "save and load hooks ran"
  if not ok1 then detail = "save hook raised: " .. string.sub(tostring(err1), 1, 140) elseif not ok2 then detail = "load hook raised: " .. string.sub(tostring(err2), 1, 140) end
  assertion("hooks_ran", ok1 and ok2, ok1 and ok2, true, detail)
  local bad = {}
  for i, w in ipairs(WATCH) do
    local inst = resolve(w.path, player)
    local row = rows[i]
    row.after = inst ~= nil and inst.Value or nil
    row.ok = inst ~= nil and inst.Value == w.marker
    if not row.ok then bad[#bad + 1] = w.id end
  end
  for _, o in ipairs(originals) do o[1].Value = o[2] end
  assertion("values_roundtrip", #bad == 0, #WATCH - #bad, #WATCH, #bad == 0 and "every value returned as saved" or ("did not return as saved: " .. table.concat(bad, ", ")))
  result.measures.values = rows
end
body()
'''

PERF = '''local PHASE = @PHASE@
local SAMPLES = @SAMPLES@
local parts, scripts, modules, instances = 0, 0, 0, 0
for _, d in ipairs(workspace:GetDescendants()) do
  if d:IsA("BasePart") then parts = parts + 1 end
end
for _, d in ipairs(game:GetDescendants()) do
  instances = instances + 1
  if d:IsA("Script") or d:IsA("LocalScript") then
    local ok, disabled = pcall(function() return d.Disabled end)
    if not (ok and disabled == true) then scripts = scripts + 1 end
  elseif d:IsA("ModuleScript") then
    modules = modules + 1
  end
end
local okm, mem = pcall(function() return game:GetService("Stats"):GetTotalMemoryUsageMb() end)
local total, n = 0, 0
local okh, hb = pcall(function() return game:GetService("RunService").Heartbeat end)
if okh and hb ~= nil then
  for i = 1, SAMPLES do
    local okw, dt = pcall(function() return hb:Wait() end)
    if okw and type(dt) == "number" then
      total = total + dt
      n = n + 1
    end
  end
end
result.measures.phase = PHASE
result.measures.part_count = parts
result.measures.script_count = scripts
result.measures.module_count = modules
result.measures.instance_count = instances
result.measures.memory_mb = okm and type(mem) == "number" and r2(mem) or nil
result.measures.frame_ms = n > 0 and r2((total / n) * 1000) or nil
result.measures.frame_samples = n
assertion("snapshot_taken", parts > 0 and result.measures.memory_mb ~= nil and result.measures.frame_ms ~= nil, parts, "part count, memory and frame time measured",
  "parts " .. parts .. ", memory " .. tostring(result.measures.memory_mb) .. " MB, frame " .. tostring(result.measures.frame_ms) .. " ms")
'''


def fill(template: str, **kw: str) -> str:
    """Single-pass substitution of ``@NAME@`` tokens: inserted text is never scanned again, and a token without a value is an error."""
    names = set(re.findall(r"@([A-Z_0-9]+)@", template))
    missing = names - set(kw)
    if missing:
        raise ValueError(f"template placeholders left unfilled: {sorted(missing)}")
    return re.sub(r"@([A-Z_0-9]+)@", lambda m: kw[m.group(1)], template)


# --- literal embedding ------------------------------------------------------------------------------------------------
def num(x: float | int) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or (isinstance(x, float) and not math.isfinite(x)):
        raise ValueError(f"{x!r} is not a finite number")
    return repr(int(x)) if float(x) == int(x) and abs(x) < 1e15 else repr(float(x))


def lv(v: Any, depth: int = 0) -> str:
    """A Luau literal for a JSON-like value (markers ``$repeat`` and ``$number`` supported). Refuses anything that could break out of a literal."""
    if depth > 6:
        raise ValueError("value nested too deeply")
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return num(v)
    if isinstance(v, str):
        return LS.quote_string(v)
    if isinstance(v, list):
        return "{" + ", ".join(lv(x, depth + 1) for x in v) + "}"
    if isinstance(v, dict):
        if "$repeat" in v:
            return f"string.rep({LS.quote_string(v['$repeat'])}, {num(int(v['times']))})"
        if "$number" in v:
            return {"nan": "(0/0)", "inf": "math.huge", "-inf": "(-math.huge)"}[v["$number"]]
        items = []
        for k, x in v.items():
            if not isinstance(k, str) or not _ID.fullmatch(k):
                raise ValueError(f"key {k!r} cannot be embedded")
            items.append(f"[{LS.quote_string(k)}] = {lv(x, depth + 1)}")
        return "{" + ", ".join(items) + "}"
    raise ValueError(f"cannot embed a {type(v).__name__}")


def args_lit(args: list) -> str:
    return "{n = " + str(len(args)) + "".join(", " + lv(a) for a in args) + "}"


def str_list(items: list[str]) -> str:
    return "{" + ", ".join(LS.quote_string(i) for i in items) + "}"


def path_list(items: list[str]) -> str:
    return "{" + ", ".join(LS.quote_string(LS.validate_path(i)) for i in items) + "}"


def opt_path(p: str | None) -> str:
    return "nil" if p is None else LS.quote_string(LS.validate_path(p))


def _ident(x: str, what: str) -> str:
    return LS.validate_name(x, what)


# --- one generator per kind ---------------------------------------------------------------------------------------------
def load_probes(root: Path) -> list[dict]:
    return (yaml.safe_load((Path(root) / "rules" / "remotes.yaml").read_text(encoding="utf-8")) or {}).get("probes", [])


def reset_value(marker: Any) -> Any:
    if isinstance(marker, bool):
        return not marker
    if isinstance(marker, (int, float)):
        return 0 if marker != 0 else 1
    return "" if marker != "" else "x"


def remote_specs(cfg: dict, style: dict, probes: list[dict]) -> list[dict]:
    cap = int(setting(style, "remote_max_probes"))
    out = []
    for r in cfg["remotes"]["items"]:
        bad = [{"label": b["label"], "args": b["args"]} for b in r.get("bad_inputs", [])]
        if r.get("generic_probes", True):
            have = {b["label"] for b in bad}
            bad += [p for p in probes[:cap] if p["label"] not in have]
        valid = r.get("valid_args") or [[]]
        out.append({"id": r["id"], "path": r["path"], "class": r["class"], "valid": valid, "bad": bad, "reject": r.get("reject_values", [])})
    return out


def body_for(kind: str, cfg: dict, style: dict, *, probes: list[dict], phase: str | None) -> str:
    S = lambda n: num(setting(style, n))  # noqa: E731
    if kind == "boot":
        b = cfg["boot"]
        return fill(BOOT, SERVICES=str_list([_ident(s, "service") for s in b.get("expected_services", [])]), PATHS=path_list(b.get("expected_paths", [])),
                    WINDOW=S("boot_window_seconds"), USE_LOG="true" if b.get("use_log_service", True) else "false")
    if kind == "spawn":
        s = cfg["spawn"]
        names = "{" + ", ".join(f"[{LS.quote_string(_ident(n, 'surface name'))}] = true" for n in s.get("invalid_surface_names", [])) + "}"
        hz = s.get("invalid_surface_attribute")
        return fill(SPAWN, TIMEOUT=S("spawn_timeout_seconds"), PROBE_D=S("spawn_probe_distance_studs"), PROBE_S=S("spawn_probe_seconds"), VOID_Y=S("spawn_void_y"),
                    RAY=S("spawn_floor_ray_studs"), MIN_FRAC=num(style["ranges"]["spawn_min_move_fraction"]["min"]), INVALID_NAMES=names,
                    HAZARD="nil" if hz is None else LS.quote_string(_ident(hz, "attribute")))
    if kind == "reachability":
        r = cfg["reachability"]
        areas = "{" + ", ".join("{name = %s, path = %s}" % (LS.quote_string(_ident(a["name"], "area")), LS.quote_string(LS.validate_path(a["path"]))) for a in r["areas"]) + "}"
        if len(r["areas"]) + 1 > int(setting(style, "reach_max_nodes")):
            raise ValueError(f"{len(r['areas'])} areas plus the spawn exceed reach_max_nodes={int(setting(style, 'reach_max_nodes'))}; split the areas over two runs or raise the setting")
        return fill(REACH, RADIUS=S("agent_radius"), HEIGHT=S("agent_height"), SPAWN_PATH=opt_path(r.get("spawn_path")), AREAS=areas, HOPS="true" if r.get("hops") else "false")
    if kind == "remotes":
        rows = []
        for r in remote_specs(cfg, style, probes):
            valid = "{" + ", ".join(args_lit(a) for a in r["valid"]) + "}"
            bad = "{" + ", ".join("{label = %s, args = %s}" % (LS.quote_string(_ident(b["label"], "probe label")), args_lit(b["args"])) for b in r["bad"]) + "}"
            rows.append("{id = %s, path = %s, class = %s, valid = %s, bad = %s, reject = %s}" % (
                LS.quote_string(_ident(r["id"], "remote id")), LS.quote_string(LS.validate_path(r["path"])), LS.quote_string(r["class"]), valid, bad, lv(r["reject"])))
        return fill(REMOTES, TIMEOUT=S("remote_timeout_seconds"), REMOTES="{\n  " + ",\n  ".join(rows) + "\n}")
    if kind == "economy":
        e = cfg["economy"]
        values = "{" + ", ".join("{id = %s, path = %s}" % (LS.quote_string(_ident(k, "value id")), LS.quote_string(LS.validate_path(v["path"]))) for k, v in e["player_values"].items()) + "}"
        steps = []
        for st in e["steps"]:
            a = st["action"]
            steps.append("{id = %s, kind = %s, path = %s, times = %s, args = %s}" % (LS.quote_string(_ident(st["id"], "step id")), LS.quote_string(a["kind"]),
                                                                                       LS.quote_string(LS.validate_path(a["path"])), num(a.get("times", 1)), args_lit(a.get("args", []))))
        return fill(ECONOMY, SETTLE=S("economy_settle_seconds"), VALUES=values, STEPS="{\n  " + ",\n  ".join(steps) + "\n}")
    if kind == "data":
        d = cfg["data"]
        watch = "{" + ", ".join("{id = %s, path = %s, marker = %s, reset = %s}" % (LS.quote_string(_ident(k, "value id")), LS.quote_string(LS.validate_path(v["path"])), lv(v["marker"]),
                                                                                 lv(reset_value(v["marker"]))) for k, v in d["values"].items()) + "}"
        return fill(DATA, SWITCH=LS.quote_string(LS.validate_path(d["test_mode"]["switch"])), ACK=opt_path(d["test_mode"].get("ack_hook")), SAVE=LS.quote_string(LS.validate_path(d["save_hook"])),
                    LOAD=LS.quote_string(LS.validate_path(d["load_hook"])), SETTLE=S("data_settle_seconds"), WATCH=watch)
    if kind == "perf":
        if phase not in ("start", "end"):
            raise ValueError("a performance script needs phase 'start' or 'end'")
        return fill(PERF, PHASE=LS.quote_string(phase), SAMPLES=S("perf_frame_samples"))
    raise ValueError(f"unknown check kind '{kind}'")


def require_data_config(cfg: dict) -> None:
    d = cfg.get("data")
    if not d:
        raise ValueError("refusing to generate a data check: this place has no 'data' section in its playtest.yaml")
    tm = d.get("test_mode") or {}
    if not tm.get("switch"):
        raise ValueError("refusing to generate a data check: the test-mode switch (data.test_mode.switch) is missing. A data check runs only in TEST MODE; declare the switch (a BoolValue "
                         "or an 'Enabled' attribute that your data layer honours by using a test store) and turn it on in the Studio session. No script was generated.")
    missing = [k for k in ("save_hook", "load_hook", "values") if not d.get(k)]
    if missing:
        raise ValueError(f"refusing to generate a data check: data.{', data.'.join(missing)} missing. Nothing was generated.")


@dataclass(frozen=True)
class Script:
    check_id: str
    kind: str
    repeat_no: int
    phase: str | None
    luau: str
    sha256: str
    context: str
    lint: list

    @property
    def lines(self) -> int:
        return self.luau.count("\n") + 1


def generate(check: dict, cfg: dict, style: dict, place: Any, label: str, *, repeat_no: int = 1, phase: str | None = None, probes: list[dict] | None = None) -> Script:
    """The Luau for one run of one check. Raises ``ValueError`` (a refusal) when the config cannot support it; the lint always runs on the result."""
    kind = check["kind"]
    sec = SECTION_OF[kind]
    if sec not in cfg:
        raise ValueError(f"this place has no '{sec}' section in its playtest.yaml, so check '{check['id']}' cannot be generated")
    if kind == "data" or check.get("requires_test_mode"):
        require_data_config(cfg)
    if not isinstance(repeat_no, int) or isinstance(repeat_no, bool) or not 1 <= repeat_no <= 99:
        raise ValueError("repeat_no must be a whole number from 1 to 99")
    name = getattr(place, "studio_name", None)
    pid = getattr(place, "roblox_place_id", None) or 0
    if not name:
        raise ValueError("this place has no studio_name in projects.yaml, so the place guard cannot be embedded; add it. Nothing was generated.")
    lab = re.sub(r"[^A-Za-z0-9_/]", "_", label)[:90]
    guard = "\n".join(LS.place_guard(int(pid), name, lab))
    head = fill(HEADER, CHECK=check["id"], REPEAT=str(repeat_no), LABEL=lab, GUARD=guard, CHECK_Q=LS.quote_string(_ident(check["id"], "check id")), KIND_Q=LS.quote_string(kind))
    code = head + body_for(kind, cfg, style, probes=probes if probes is not None else [], phase=phase) + FOOTER
    LS.assert_safe(code)
    return Script(check["id"], kind, repeat_no, phase, code, hashlib.sha256(code.encode("utf-8")).hexdigest(), check["context"], LS.lint(code))
