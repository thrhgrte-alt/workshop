"""The fixture table: every Luau example, the bug planted in it (or why it is clean), and the rule ids a correct review must report.

This file is the ground truth for the evals. `scripts/make_examples.py` turns it into examples/*.luau, the READMEs and evals/tasks/*.yaml.
The expectations were written from the planted bug, not from what the detectors print.

Fields: name (file name), dir (negative|positive), why, expect (defect rule ids), questions (low-confidence rule ids that a correct review
raises as questions), strict (review in --strict mode), code, gap (negative only: a planted bug the heuristics are known NOT to catch).
"""

from __future__ import annotations

PLAYERS_HEADER = """--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
"""

COOL_DECL = """local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}
"""

COOL_CHECK = """    local now = os.clock()
    if now - (lastUse[player] or 0) < COOLDOWN then
        return
    end
    lastUse[player] = now
"""

COOL_CLEANUP = """
Players.PlayerRemoving:Connect(function(player)
    lastUse[player] = nil
end)
"""

DS_HEADER = """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local SESSION_OWNER = game.JobId
"""

RETRY = """
local function withRetry(fn: () -> ()): boolean
    for attempt = 1, 3 do
        local ok, err = pcall(fn)
        if ok then
            return true
        end
        warn("DataStore attempt", attempt, "failed:", err)
        task.wait(attempt)
    end
    return false
end

local function key(player: Player): string
    return "Player_" .. player.UserId
end
"""

CLOSE_ALL = """
game:BindToClose(function()
    for _, player in Players:GetPlayers() do
        task.spawn(save, player)
    end
    task.wait(3)
end)
"""

FIXTURES: list[dict] = []


def add(name, dir, why, code, expect=(), questions=(), strict=False, gap=None, files=None, folder=None):
    FIXTURES.append(dict(name=name, dir=dir, why=why, code=code, expect=list(expect), questions=list(questions), strict=strict, gap=gap,
                         files=files, folder=folder))


# =====================================================================================================================
# NEGATIVE: a planted bug each
# =====================================================================================================================
# ---- security ----
add("sec001_unvalidated_remote.server.luau", "negative",
    "The EquipItem handler uses `itemName` and `slot` straight from the client: no typeof, no range check, no allow-list.",
    PLAYERS_HEADER + """
local equip = ReplicatedStorage:WaitForChild("EquipItem") :: RemoteEvent
""" + COOL_DECL + """
equip.OnServerEvent:Connect(function(player, itemName, slot)
""" + COOL_CHECK + """    local backpack = player:FindFirstChild("Backpack")
    local tool = ReplicatedStorage.Items:FindFirstChild(itemName)
    if backpack and tool then
        tool:Clone().Parent = backpack
        print("equipped in slot", slot)
    end
end)
""" + COOL_CLEANUP, expect=["SEC001"])

add("sec001_unvalidated_remote_function.server.luau", "negative",
    "A RemoteFunction returns `profiles[targetId]` for whatever the client asks for: the argument is never checked.",
    PLAYERS_HEADER + """
local getInfo = ReplicatedStorage:WaitForChild("GetPlayerInfo") :: RemoteFunction
local profiles: {[number]: {level: number}} = {}
""" + COOL_DECL + """
getInfo.OnServerInvoke = function(player, targetId)
""" + COOL_CHECK + """    return profiles[targetId]
end
""" + COOL_CLEANUP, expect=["SEC001"])

add("sec002_client_price.server.luau", "negative",
    "`price` arrives from the client and is charged as given: an exploiter sends price = 0 (or negative) and gets items free.",
    PLAYERS_HEADER + """
local buy = ReplicatedStorage:WaitForChild("BuyItem") :: RemoteEvent
local function grant(player: Player, itemId: string) end
""" + COOL_DECL + """
buy.OnServerEvent:Connect(function(player, itemId, price)
""" + COOL_CHECK + """    if typeof(itemId) ~= "string" or typeof(price) ~= "number" then
        return
    end
    local stats = player:FindFirstChild("leaderstats")
    local coins = stats and stats:FindFirstChild("Coins")
    if coins and coins:IsA("IntValue") and coins.Value >= price then
        coins.Value -= price
        grant(player, itemId)
    end
end)
""" + COOL_CLEANUP, expect=["SEC002"])

add("sec002_client_damage.server.luau", "negative",
    "The client decides how much `damage` a hit does; the server just applies it with TakeDamage.",
    PLAYERS_HEADER + """
local hit = ReplicatedStorage:WaitForChild("DealDamage") :: RemoteEvent
""" + COOL_DECL + """
hit.OnServerEvent:Connect(function(player, target, damage)
""" + COOL_CHECK + """    if typeof(target) ~= "Instance" or not target:IsA("Model") or typeof(damage) ~= "number" then
        return
    end
    local humanoid = target:FindFirstChildOfClass("Humanoid")
    if humanoid then
        humanoid:TakeDamage(damage)
    end
end)
""" + COOL_CLEANUP, expect=["SEC002"])

add("sec003_no_cooldown.server.luau", "negative",
    "A well validated handler with no cooldown or permission check: anyone can fire it as fast as they like. Raised as a question because the check may live elsewhere.",
    PLAYERS_HEADER + """
local claim = ReplicatedStorage:WaitForChild("ClaimChest") :: RemoteEvent
local CHESTS: {[string]: number} = { wooden = 10, iron = 50 }

claim.OnServerEvent:Connect(function(player, chestId)
    if typeof(chestId) ~= "string" or not CHESTS[chestId] then
        return
    end
    player:SetAttribute("Gems", (player:GetAttribute("Gems") or 0) + CHESTS[chestId])
end)
""", questions=["SEC003"])

add("sec004_loadstring.server.luau", "negative",
    "Source fetched over HTTP is compiled and run with `loadstring`: remote code execution if the URL or response is ever compromised.",
    """--!strict
local HttpService = game:GetService("HttpService")

local ok, source = pcall(function()
    return HttpService:GetAsync("https://example.invalid/hotfix.lua")
end)
if ok then
    local fn = loadstring(source)
    if fn then
        fn()
    end
end
""", expect=["SEC004"])

add("sec005_require_asset_id.server.luau", "negative",
    "`require(<number>)` pulls a ModuleScript from the catalog by asset id; its owner can change it under you.",
    """--!strict
local Lib = require(6134521089)
Lib.start()
""", expect=["SEC005"])

add("sec006_invokeclient.server.luau", "negative",
    "The server calls `InvokeClient`: a client that never answers leaves the server thread waiting forever.",
    PLAYERS_HEADER + """
local ask = ReplicatedStorage:WaitForChild("AskClient") :: RemoteFunction

Players.PlayerAdded:Connect(function(player)
    local answer = ask:InvokeClient(player, "ready?")
    print(player.Name, answer)
end)
""", expect=["SEC006"])

# ---- data ----
add("dat001_unprotected_getasync.server.luau", "negative",
    "`GetAsync` runs without pcall: a throttle or outage raises an error and the player's data never loads.",
    """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")

Players.PlayerAdded:Connect(function(player)
    local data = store:GetAsync("Player_" .. player.UserId)
    print(player.Name, data)
end)
""", expect=["DAT001"])

add("dat001_unprotected_updateasync.server.luau", "negative",
    "The save in PlayerRemoving calls `UpdateAsync` with no pcall.",
    DS_HEADER + """
Players.PlayerRemoving:Connect(function(player)
    store:UpdateAsync("Player_" .. player.UserId, function(old)
        local data = old or {coins = 0}
        data.lockedBy = SESSION_OWNER
        return data
    end)
end)

game:BindToClose(function()
    task.wait(3)
end)
""", expect=["DAT001"])

add("dat002_no_retry.server.luau", "negative",
    "The load is in a pcall, but a single failure gives up: one hiccup leaves the player with an empty profile.",
    """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")

Players.PlayerAdded:Connect(function(player)
    local ok, data = pcall(function()
        return store:GetAsync("Player_" .. player.UserId)
    end)
    if not ok then
        warn("load failed:", data)
    end
end)
""", expect=["DAT002"])

add("dat003_setasync.server.luau", "negative",
    "Read with GetAsync, modify, write back with SetAsync: two servers (or a quick rejoin) overwrite each other. UpdateAsync is the atomic way.",
    DS_HEADER + RETRY + """
local function save(player: Player)
    local current: {coins: number}? = nil
    withRetry(function()
        current = store:GetAsync(key(player))
    end)
    local data = current or {coins = 0}
    data.coins += 10
    withRetry(function()
        store:SetAsync(key(player), data)
    end)
end

Players.PlayerRemoving:Connect(save)
""" + CLOSE_ALL, expect=["DAT003"])

add("dat004_no_bindtoclose.server.luau", "negative",
    "Data is saved on PlayerRemoving only: when the server shuts down, players still inside may not be saved.",
    DS_HEADER + RETRY + """
local function save(player: Player)
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            local data = old or {coins = 0}
            data.lockedBy = SESSION_OWNER
            return data
        end)
    end)
end

Players.PlayerRemoving:Connect(save)
""", expect=["DAT004"])

add("dat005_save_every_few_seconds.server.luau", "negative",
    "A loop saves every player every 5 seconds: it burns the DataStore request budget and causes throttling.",
    DS_HEADER + RETRY + """
local function save(player: Player)
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            return old or {coins = 0, lockedBy = SESSION_OWNER}
        end)
    end)
end

task.spawn(function()
    while true do
        task.wait(5)
        for _, player in Players:GetPlayers() do
            withRetry(function()
                store:UpdateAsync(key(player), function(old)
                    return old or {coins = 0, lockedBy = SESSION_OWNER}
                end)
            end)
        end
    end
end)

Players.PlayerRemoving:Connect(save)
""" + CLOSE_ALL, expect=["DAT005"])

add("dat005_save_every_frame.server.luau", "negative",
    "The DataStore write sits inside a Heartbeat handler: dozens of requests per second per player.",
    DS_HEADER + """local RunService = game:GetService("RunService")
""" + RETRY + """
local function save(player: Player)
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            return old or {coins = 0, lockedBy = SESSION_OWNER}
        end)
    end)
end

RunService.Heartbeat:Connect(function()
    for _, player in Players:GetPlayers() do
        withRetry(function()
            store:UpdateAsync(key(player), function(old)
                return old or {coins = 0, lockedBy = SESSION_OWNER}
            end)
        end)
    end
end)

Players.PlayerRemoving:Connect(save)
""" + CLOSE_ALL, expect=["DAT005"])

add("dat006_no_session_lock.server.luau", "negative",
    "Profiles are loaded and saved with no lock of any kind, so a fast rejoin to another server can overwrite progress. Raised as a question: locking may live in a module.",
    """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
""" + RETRY + """
local function save(player: Player)
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            return old or {coins = 0}
        end)
    end)
end

Players.PlayerRemoving:Connect(save)
""" + CLOSE_ALL, questions=["DAT006"])

add("dat007_receipt_returns_bool.server.luau", "negative",
    "ProcessReceipt returns `true`/`false` instead of an Enum.ProductPurchaseDecision, so Roblox never gets a valid answer.",
    """--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")

local function grant(player: Player, productId: number) end

MarketplaceService.ProcessReceipt = function(receipt)
    local player = Players:GetPlayerByUserId(receipt.PlayerId)
    if not player then
        return false
    end
    grant(player, receipt.ProductId)
    return true
end
""", expect=["DAT007"])

add("dat008_receipt_always_granted.server.luau", "negative",
    "The handler can only answer PurchaseGranted, even if the grant failed or the player left. Raised as a question.",
    """--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")

local function grant(player: Player, productId: number) end

MarketplaceService.ProcessReceipt = function(receipt)
    local player = Players:GetPlayerByUserId(receipt.PlayerId)
    if player then
        grant(player, receipt.ProductId)
    end
    return Enum.ProductPurchaseDecision.PurchaseGranted
end
""", questions=["DAT008"])

add("dat009_key_from_player_name.server.luau", "negative",
    "The data key is `player.Name`: a username change orphans the data (or hands it to whoever takes the old name).",
    DS_HEADER + RETRY + """
local function load(player: Player)
    withRetry(function()
        store:UpdateAsync(player.Name, function(old)
            local data = old or {coins = 0}
            data.lockedBy = SESSION_OWNER
            return data
        end)
    end)
end

local function save(player: Player)
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            return old or {coins = 0, lockedBy = SESSION_OWNER}
        end)
    end)
end

Players.PlayerAdded:Connect(load)
Players.PlayerRemoving:Connect(save)
""" + CLOSE_ALL, expect=["DAT009"])

# ---- performance ----
add("perf001_busy_loop.server.luau", "negative",
    "`while true do` with no yield: the script freezes until it times out.",
    """--!strict
local counter = 0
while true do
    counter += 1
end
""", expect=["PERF001"])

add("perf002_while_true_wait.server.luau", "negative",
    "A polling loop built on the legacy global `wait` (reported once as PERF002, not again as API001).",
    """--!strict
local lamp = workspace:WaitForChild("Lamp") :: BasePart
while true do
    wait(1)
    lamp.Transparency = 1 - lamp.Transparency
end
""", expect=["PERF002"])

add("perf003_getdescendants_heartbeat.client.luau", "negative",
    "`workspace:GetDescendants()` is called inside a Heartbeat handler: a full tree walk 60 times a second.",
    """--!strict
local RunService = game:GetService("RunService")

RunService.Heartbeat:Connect(function()
    for _, part in workspace:GetDescendants() do
        if part:IsA("BasePart") and part.Name == "Glow" then
            part.Transparency = 0.5
        end
    end
end)
""", expect=["PERF003"])

add("perf003_findfirstchild_renderstepped.client.luau", "negative",
    "`FindFirstChild` in a RenderStepped handler searches every frame; the result should be cached.",
    """--!strict
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local player = Players.LocalPlayer

RunService.RenderStepped:Connect(function()
    local character = player.Character
    local root = character and character:FindFirstChild("HumanoidRootPart")
    if root then
        print(root)
    end
end)
""", expect=["PERF003"])

add("perf004_instance_new_heartbeat.server.luau", "negative",
    "A new Part is created on every Heartbeat.",
    """--!strict
local RunService = game:GetService("RunService")

RunService.Heartbeat:Connect(function()
    local spark = Instance.new("Part")
    spark.Anchored = true
    spark.Parent = workspace
end)
""", expect=["PERF004"])

add("perf005_touched_no_debounce.server.luau", "negative",
    "A Touched handler that heals on every touch event with no debounce. Raised as a question: a guard may exist elsewhere.",
    """--!strict
local pad = workspace:WaitForChild("HealPad") :: BasePart

pad.Touched:Connect(function(hit)
    local humanoid = hit.Parent and hit.Parent:FindFirstChildOfClass("Humanoid")
    if humanoid then
        humanoid.Health += 1
    end
end)
""", questions=["PERF005"])

# ---- leaks ----
add("leak001_heartbeat_per_player.server.luau", "negative",
    "Every joining player adds a Heartbeat connection that is never stored or disconnected.",
    PLAYERS_HEADER + """local RunService = game:GetService("RunService")

Players.PlayerAdded:Connect(function(player)
    RunService.Heartbeat:Connect(function(dt)
        local character = player.Character
        if character then
            character:SetAttribute("Age", (character:GetAttribute("Age") or 0) + dt)
        end
    end)
end)
""", expect=["LEAK001"])

add("leak002_unbounded_log.server.luau", "negative",
    "`samples` gets a new number every frame and is never trimmed.",
    """--!strict
local RunService = game:GetService("RunService")

local samples: {number} = {}

RunService.Heartbeat:Connect(function(dt)
    table.insert(samples, dt)
end)
""", expect=["LEAK002"])

add("leak003_player_table.server.luau", "negative",
    "`sessions[player]` keeps every Player that ever joined alive: nothing clears it on PlayerRemoving.",
    PLAYERS_HEADER + """
local sessions: {[Player]: {joined: number}} = {}

Players.PlayerAdded:Connect(function(player)
    sessions[player] = {joined = os.time()}
end)
""", expect=["LEAK003"])

add("leak004_player_instances.server.luau", "negative",
    "A marker Part is created for each player and parented to the workspace; nothing destroys it when they leave. Raised as a question.",
    PLAYERS_HEADER + """
Players.PlayerAdded:Connect(function(player)
    local marker = Instance.new("Part")
    marker.Name = player.Name .. "_Marker"
    marker.Anchored = true
    marker.Parent = workspace
end)
""", questions=["LEAK004"])

# ---- deprecated / poor API ----
add("api001_wait.server.luau", "negative", "The legacy global `wait`.",
    """--!strict
print("starting")
wait(2)
print("done")
""", expect=["API001"])

add("api002_spawn.server.luau", "negative", "The legacy global `spawn`.",
    """--!strict
local function work()
    print("working")
end

spawn(work)
""", expect=["API002"])

add("api003_delay.server.luau", "negative", "The legacy global `delay`.",
    """--!strict
local function cleanup()
    print("cleanup")
end

delay(5, cleanup)
""", expect=["API003"])

add("api004_instance_new_parent.server.luau", "negative",
    "`Instance.new(\"Part\", workspace)` parents the Part before its properties are set.",
    """--!strict
local part = Instance.new("Part", workspace)
part.Anchored = true
part.Size = Vector3.new(4, 1, 4)
""", expect=["API004"])

add("api005_humanoid_loadanimation.server.luau", "negative", "`Humanoid:LoadAnimation` is deprecated; the Animator loads animations.",
    """--!strict
local function play(humanoid: Humanoid, animation: Animation)
    local track = humanoid:LoadAnimation(animation)
    track:Play()
end

return play
""", expect=["API005"])

add("api006_camera_cached.client.luau", "negative",
    "`workspace.CurrentCamera` is cached once at the top of the script. Raised as a question.",
    """--!strict
local camera = workspace.CurrentCamera

local function zoomIn()
    if camera then
        camera.FieldOfView = 50
    end
end

zoomIn()
""", questions=["API006"])

add("api007_magic_numbers.server.luau", "negative",
    "Unexplained numbers in comparisons. Only reported in --strict mode, and only as a question.",
    """--!strict
local function inRange(distance: number): boolean
    if distance < 37 then
        return true
    end
    return distance == 412
end

print(inRange(10))
""", questions=["API007"], strict=True)

add("api008_lowercase_connect.server.luau", "negative", "The lower-case `connect` alias is deprecated.",
    """--!strict
local detector = workspace:WaitForChild("Door"):WaitForChild("ClickDetector") :: ClickDetector

detector.MouseClick:connect(function(player)
    print(player.Name, "clicked")
end)
""", expect=["API008"])

add("api009_tick.server.luau", "negative", "`tick()` is deprecated; elapsed time should use os.clock().",
    """--!strict
local started = tick()
task.wait(1)
print("elapsed", tick() - started)
""", expect=["API009"])

add("api010_table_getn.server.luau", "negative", "`table.getn` is a removed Lua 5.0 function; use `#`.",
    """--!strict
local list = {1, 2, 3}
local n = table.getn(list)
print(n)
""", expect=["API010"])

add("api011_body_mover.server.luau", "negative", "BodyVelocity is a deprecated mover; a LinearVelocity constraint replaces it.",
    """--!strict
local part = workspace:WaitForChild("Ball") :: BasePart
local mover = Instance.new("BodyVelocity")
mover.Velocity = Vector3.new(0, 50, 0)
mover.MaxForce = Vector3.new(0, 1e5, 0)
mover.Parent = part
""", expect=["API011"])

# ---- structure ----
add("str001_missing_strict.server.luau", "negative", "No `--!strict` header.",
    """print("hello")
""", expect=["STR001"])

add("str002_untyped_public.luau", "negative",
    "Public functions `M.add` has untyped parameters and no return type. Only reported in --strict mode.",
    """--!strict
local M = {}

function M.add(a, b)
    return a + b
end

function M.greet(name: string): string
    return "hi " .. name
end

return M
""", expect=["STR002"], strict=True)

add("str003_module_cycle", "negative",
    "Modules A and B require each other. Found by reviewing the folder.",
    None, expect=["STR003"], folder="cycle",
    files={
        "A.luau": """--!strict
local B = require(script.Parent.B)

local A = {}

function A.run(): number
    return B.value() + 1
end

function A.base(): number
    return 1
end

return A
""",
        "B.luau": """--!strict
local A = require(script.Parent.A)

local B = {}

function B.value(): number
    return A.base() * 2
end

return B
""",
    })

add("str004_server_api_in_client.client.luau", "negative",
    "A client script reaches into ServerStorage, which does not replicate to clients.",
    """--!strict
local ServerStorage = game:GetService("ServerStorage")

local items = ServerStorage:WaitForChild("Items")
print(items)
""", expect=["STR004"])

add("str005_client_api_in_server.server.luau", "negative",
    "A server script reads `Players.LocalPlayer`, which is nil on the server.",
    """--!strict
local Players = game:GetService("Players")

local player = Players.LocalPlayer
print(player)
""", expect=["STR005"])

add("cor001_ignored_pcall.server.luau", "negative",
    "`pcall` is called as a bare statement: whatever fails is swallowed without a trace.",
    """--!strict
local door = workspace:FindFirstChild("Door")

pcall(function()
    if door then
        door:Destroy()
    end
end)
""", expect=["COR001"])

# ---- known gaps: planted bugs the heuristics do not catch (documented, counted in recall) ----
add("gap_varargs_remote.server.luau", "negative",
    "KNOWN GAP. The handler takes `...` and reads `{...}` unvalidated. The checker looks at named parameters, so it cannot see this.",
    PLAYERS_HEADER + """
local equip = ReplicatedStorage:WaitForChild("EquipItem") :: RemoteEvent
""" + COOL_DECL + """
equip.OnServerEvent:Connect(function(player, ...)
""" + COOL_CHECK + """    local args = {...}
    local tool = ReplicatedStorage.Items:FindFirstChild(args[1])
    if tool then
        tool:Clone().Parent = player.Backpack
    end
end)
""" + COOL_CLEANUP, expect=["SEC001"], gap="varargs handler")

add("gap_aliased_getasync.server.luau", "negative",
    "KNOWN GAP. `GetAsync` is called through an alias (`local get = store.GetAsync`), not with `:`, and without pcall. Only `obj:Method(` calls are recognised.",
    """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local get = store.GetAsync

Players.PlayerAdded:Connect(function(player)
    local data = get(store, "Player_" .. player.UserId)
    print(data)
end)
""", expect=["DAT001"], gap="aliased method")

add("gap_require_variable_id.server.luau", "negative",
    "KNOWN GAP. The asset id is held in a constant and `require(MODULE_ID)` is called with the name. There is no constant propagation.",
    """--!strict
local MODULE_ID = 6134521089
local Lib = require(MODULE_ID)
Lib.start()
""", expect=["SEC005"], gap="no constant propagation")

# =====================================================================================================================
# POSITIVE: clean files and false-positive traps. Zero findings expected.
# =====================================================================================================================
add("trap_strings_and_comments.server.luau", "positive",
    "TRAP: every bug-looking snippet here is inside a comment, a string, a long string, a long comment or an interpolated string. Nothing here is code.",
    """--!strict
-- Help text for the admin console. wait(1), spawn(f), loadstring(code) and tick() below are documentation only.
local HELP = [[
Do not use wait() or while true do loops.
store:SetAsync(key, value) overwrites data. require(123456) loads an asset.
]]

--[==[
    Old implementation, kept for reference:
    while true do end
    remote.OnServerEvent:Connect(function(player, price) player.Coins.Value -= price end)
]==]

local EXAMPLES = {
    'game:GetService("DataStoreService"):GetDataStore("x"):GetAsync(k)',
    "Instance.new('Part', workspace)",
    `tick() is {"deprecated"} and spawn(f) too`,
    "humanoid:LoadAnimation(anim) -- not a comment, a string",
}

local function describe(index: number): string
    return EXAMPLES[index] or HELP -- humanoid:LoadAnimation(x) wait(1)
end

print(describe(1)) -- Touched:connect(f) pcall(f)
""")

add("trap_pcall_datastore.server.luau", "positive",
    "TRAP: the full, correct DataStore pattern. Every call is in a retrying pcall helper, saves use UpdateAsync, there is a session owner, per-player cleanup and BindToClose.",
    """--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local SESSION_OWNER = game.JobId
local RETRIES = 3
local profiles: {[Player]: {coins: number}} = {}

local function key(player: Player): string
    return "Player_" .. player.UserId
end

local function withRetry(fn: () -> ()): boolean
    for attempt = 1, RETRIES do
        local ok, err = pcall(fn)
        if ok then
            return true
        end
        warn("DataStore attempt", attempt, "failed:", err)
        task.wait(attempt)
    end
    return false
end

local function load(player: Player)
    local loaded: {coins: number}? = nil
    local ok = withRetry(function()
        loaded = store:UpdateAsync(key(player), function(old)
            local data = old or {coins = 0}
            data.lockedBy = SESSION_OWNER
            return data
        end)
    end)
    profiles[player] = if ok and loaded then loaded else {coins = 0}
end

local function save(player: Player)
    local profile = profiles[player]
    if not profile then
        return
    end
    withRetry(function()
        store:UpdateAsync(key(player), function(old)
            return profile
        end)
    end)
end

Players.PlayerAdded:Connect(load)
Players.PlayerRemoving:Connect(function(player)
    save(player)
    profiles[player] = nil
end)

game:BindToClose(function()
    for _, player in Players:GetPlayers() do
        task.spawn(save, player)
    end
    task.wait(3)
end)
""")

add("trap_validated_remote.server.luau", "positive",
    "TRAP: a remote handler that validates type and range, looks the price up on the server and has a per-player cooldown that is cleaned up.",
    PLAYERS_HEADER + """
local buy = ReplicatedStorage:WaitForChild("BuyItem") :: RemoteEvent
local PRICES: {[string]: number} = { sword = 100, shield = 80 }
local COOLDOWN = 1
local lastBuy: {[Player]: number} = {}

buy.OnServerEvent:Connect(function(player, itemId, quantity)
    local now = os.clock()
    if now - (lastBuy[player] or 0) < COOLDOWN then
        return
    end
    if typeof(itemId) ~= "string" or typeof(quantity) ~= "number" then
        return
    end
    local price = PRICES[itemId]
    if not price then
        return
    end
    local count = math.clamp(math.floor(quantity), 1, 10)
    local stats = player:FindFirstChild("leaderstats")
    local coins = stats and stats:FindFirstChild("Coins")
    if coins and coins:IsA("IntValue") and coins.Value >= price * count then
        lastBuy[player] = now
        coins.Value -= price * count
    end
end)

Players.PlayerRemoving:Connect(function(player)
    lastBuy[player] = nil
end)
""")

add("trap_task_library.server.luau", "positive",
    "TRAP: only the task library, plus names that merely contain `wait`, `spawn` or `delay` (config fields, a local function `spawnEnemy`, `signal:Wait()`).",
    """--!strict
local RunService = game:GetService("RunService")

local config = { wait = 2, delay = 0.5, spawn = "Spawn1" }

local function waitForReady(): boolean
    local deadline = os.clock() + config.wait
    while os.clock() < deadline do
        task.wait(0.1)
    end
    return true
end

local function spawnEnemy(name: string)
    local enemy = Instance.new("Part")
    enemy.Name = name
    enemy.Anchored = true
    enemy.Parent = workspace
    task.delay(10, function()
        enemy:Destroy()
    end)
end

task.spawn(function()
    waitForReady()
    spawnEnemy(config.spawn)
end)

local ready = Instance.new("BindableEvent")
task.defer(function()
    ready:Fire()
end)
ready.Event:Wait()
RunService.Heartbeat:Wait()
""")

add("trap_loops_that_yield.server.luau", "positive",
    "TRAP: `while true do` loops that yield with task.wait or leave with `break`, and a repeat/until that yields. None freezes the script.",
    """--!strict
local LIMIT = 100
local ready = false
task.delay(5, function()
    ready = true
end)

local count = 0
while true do
    count += 1
    if count > LIMIT then
        break
    end
end

local polls = 0
repeat
    polls += 1
    task.wait(0.25)
until ready or polls >= LIMIT

task.spawn(function()
    while true do
        task.wait(1)
        print("tick", os.clock())
    end
end)
""")

add("trap_heartbeat_cached.client.luau", "positive",
    "TRAP: a Heartbeat handler that does no lookups (the list is built once outside), and a connection that is stored and disconnected.",
    """--!strict
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local player = Players.LocalPlayer
local lamps = workspace:WaitForChild("Lamps"):GetChildren()
local connection: RBXScriptConnection? = nil

local function update(dt: number)
    for _, lamp in lamps do
        if lamp:IsA("BasePart") then
            lamp.Transparency = math.sin(os.clock()) * 0.5 + 0.5
        end
    end
end

local function start()
    if connection then
        return
    end
    connection = RunService.Heartbeat:Connect(update)
end

local function stop()
    if connection then
        connection:Disconnect()
        connection = nil
    end
end

player.CharacterAdded:Connect(start)
player.CharacterRemoving:Connect(stop)
""")

add("trap_datastore_lookalike.luau", "positive",
    "TRAP: a plain in-memory Cache class with methods named GetAsync/SetAsync. There is no DataStore anywhere in the file.",
    """--!strict
local Cache = {}
Cache.__index = Cache

export type Cache = typeof(setmetatable({} :: { entries: { [string]: any } }, Cache))

function Cache.new(): Cache
    return setmetatable({ entries = {} }, Cache)
end

function Cache.GetAsync(self: Cache, key: string): any
    return self.entries[key]
end

function Cache.SetAsync(self: Cache, key: string, value: any)
    self.entries[key] = value
end

local cache = Cache.new()
cache:SetAsync("a", 1)
local value = cache:GetAsync("a")
print(value)

return Cache
""")

add("trap_receipt_correct.server.luau", "positive",
    "TRAP: ProcessReceipt returns the right decisions from the right function. Returns inside the nested UpdateAsync closure are not the handler's returns.",
    """--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local purchases = DataStoreService:GetDataStore("Purchases")
local SESSION_OWNER = game.JobId

local function withRetry(fn: () -> ()): boolean
    for attempt = 1, 3 do
        local ok, err = pcall(fn)
        if ok then
            return true
        end
        warn("purchase save attempt", attempt, "failed:", err)
        task.wait(attempt)
    end
    return false
end

MarketplaceService.ProcessReceipt = function(receipt): Enum.ProductPurchaseDecision
    local player = Players:GetPlayerByUserId(receipt.PlayerId)
    if not player then
        return Enum.ProductPurchaseDecision.NotProcessedYet
    end
    local recorded = withRetry(function()
        purchases:UpdateAsync("Receipt_" .. receipt.PurchaseId, function(old)
            if old then
                return old
            end
            return { granted = os.time(), owner = SESSION_OWNER }
        end)
    end)
    if not recorded then
        return Enum.ProductPurchaseDecision.NotProcessedYet
    end
    return Enum.ProductPurchaseDecision.PurchaseGranted
end
""")

add("trap_typed_module.luau", "positive",
    "TRAP: a fully typed module. Clean in --strict mode as well.",
    """--!strict
local Inventory = {}
Inventory.__index = Inventory

export type Inventory = { items: { [string]: number } }

function Inventory.new(): Inventory
    return setmetatable({ items = {} }, Inventory) :: any
end

function Inventory.add(self: Inventory, name: string, count: number): ()
    self.items[name] = (self.items[name] or 0) + count
end

function Inventory.count(self: Inventory, name: string): number
    return self.items[name] or 0
end

return Inventory
""", strict=True)

add("trap_client_apis_on_client.client.luau", "positive",
    "TRAP: client-only APIs (LocalPlayer, UserInputService, FireServer, OnClientEvent) in a client script, and the camera read inside a function.",
    """--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local UserInputService = game:GetService("UserInputService")

local player = Players.LocalPlayer
local jump = ReplicatedStorage:WaitForChild("RequestDash") :: RemoteEvent
local notice = ReplicatedStorage:WaitForChild("Notice") :: RemoteEvent

UserInputService.InputBegan:Connect(function(input, processed)
    if processed then
        return
    end
    if input.KeyCode == Enum.KeyCode.Q then
        jump:FireServer("dash")
    end
end)

notice.OnClientEvent:Connect(function(text)
    local camera = workspace.CurrentCamera
    print(player.Name, text, camera and camera.FieldOfView)
end)
""")

add("trap_server_apis_on_server.server.luau", "positive",
    "TRAP: server-only APIs (ServerStorage, FireClient, FireAllClients) in a server script.",
    """--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local notice = ReplicatedStorage:WaitForChild("Notice") :: RemoteEvent
local templates = ServerStorage:WaitForChild("Templates")

Players.PlayerAdded:Connect(function(player)
    notice:FireClient(player, "welcome")
    notice:FireAllClients(player.Name .. " joined", #templates:GetChildren())
end)
""")

add("trap_named_constants.luau", "positive",
    "TRAP: tunable numbers are named constants. Clean in --strict mode (no magic numbers).",
    """--!strict
local AGGRO_RANGE = 37
local MAX_HEALTH = 100
local RESPAWN_DELAY = 5

local function shouldChase(distance: number): boolean
    return distance < AGGRO_RANGE
end

local function isDead(health: number): boolean
    return health <= 0 or health > MAX_HEALTH
end

task.delay(RESPAWN_DELAY, function()
    print(shouldChase(10), isDead(50))
end)
""", strict=True)

add("trap_connections_tracked.server.luau", "positive",
    "TRAP: a Heartbeat connection per player, but each one is stored, disconnected and cleared when the player leaves.",
    PLAYERS_HEADER + """local RunService = game:GetService("RunService")

local connections: {[Player]: RBXScriptConnection} = {}

Players.PlayerAdded:Connect(function(player)
    connections[player] = RunService.Heartbeat:Connect(function(dt)
        player:SetAttribute("Age", (player:GetAttribute("Age") or 0) + dt)
    end)
end)

Players.PlayerRemoving:Connect(function(player)
    local connection = connections[player]
    if connection then
        connection:Disconnect()
    end
    connections[player] = nil
end)
""")

add("trap_per_player_cleanup.server.luau", "positive",
    "TRAP: per-player instances and a per-player table, both cleaned up on PlayerRemoving.",
    PLAYERS_HEADER + """
local markers: {[Player]: BasePart} = {}

Players.PlayerAdded:Connect(function(player)
    local marker = Instance.new("Part")
    marker.Name = player.Name .. "_Marker"
    marker.Anchored = true
    marker.Parent = workspace
    markers[player] = marker
end)

Players.PlayerRemoving:Connect(function(player)
    local marker = markers[player]
    if marker then
        marker:Destroy()
    end
    markers[player] = nil
end)
""")

add("trap_if_expressions.luau", "positive",
    "TRAP: Luau if-expressions (no `end`), nested functions, repeat/until and generic for, to prove block matching is not confused.",
    """--!strict
local function grade(score: number): string
    return if score >= 90 then "A" elseif score >= 80 then "B" else "C"
end

local function pick(a: number, b: number): number
    local bigger = if a > b then a else b
    return bigger
end

local total = 0
for i = 1, 3 do
    local label = if i % 2 == 0 then "even" else "odd"
    total += #label
end

local n = 0
repeat
    n += 1
    task.wait()
until n >= 3

local function outer()
    local function inner()
        return if n > 1 then "many" else if n == 1 then "one" else "none"
    end
    return inner()
end

print(grade(total), pick(1, 2), outer())
""")

add("trap_touched_debounced.server.luau", "positive",
    "TRAP: a Touched handler with a debounce table that resets after a cooldown.",
    """--!strict
local pad = workspace:WaitForChild("HealPad") :: BasePart
local debounce: {[Instance]: boolean} = {}

pad.Touched:Connect(function(hit)
    local character = hit.Parent
    if not character or debounce[character] then
        return
    end
    debounce[character] = true
    local humanoid = character:FindFirstChildOfClass("Humanoid")
    if humanoid then
        humanoid.Health += 1
    end
    task.delay(1, function()
        debounce[character] = nil
    end)
end)
""")

add("trap_remote_helper_validation.server.luau", "positive",
    "TRAP: validation done by a helper that is called in a guard (`if not isValidTarget(target)`), plus a cooldown.",
    PLAYERS_HEADER + """
local ping = ReplicatedStorage:WaitForChild("PingTarget") :: RemoteEvent
""" + COOL_DECL + """
local function isValidTarget(target: any): boolean
    return typeof(target) == "Instance" and target:IsA("BasePart") and target:IsDescendantOf(workspace)
end

ping.OnServerEvent:Connect(function(player, target)
""" + COOL_CHECK + """    if not isValidTarget(target) then
        return
    end
    print(player.Name, "pinged", target)
end)
""" + COOL_CLEANUP)

add("trap_lookups_outside_frame_code.server.luau", "positive",
    "TRAP: GetChildren/GetDescendants/FindFirstChild in a slow loop and a one-off setup, not in a per-frame handler.",
    """--!strict
local crates = workspace:WaitForChild("Crates")

local function respawnAll()
    for _, crate in crates:GetChildren() do
        crate:SetAttribute("Full", true)
    end
end

task.spawn(function()
    while true do
        task.wait(30)
        respawnAll()
        local boss = workspace:FindFirstChild("Boss")
        if boss then
            print(#boss:GetDescendants())
        end
    end
end)
""")

add("trap_similar_names.server.luau", "positive",
    "TRAP: names that look like flagged APIs but are not: `loadstringEnabled`, `LoadStringEnabled`, `require` of paths, `string.format`, a `tickRate` variable.",
    """--!strict
local ServerScriptService = game:GetService("ServerScriptService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local loadstringEnabled = ServerScriptService.LoadStringEnabled
local tickRate = 1 / 30
local Config = require(ReplicatedStorage:WaitForChild("Config"))
local Util = require(script.Parent:WaitForChild("Util"))

print(string.format("loadstring enabled: %s, tick: %.3f", tostring(loadstringEnabled), tickRate), Config, Util)
""")

add("trap_pcall_results_used.server.luau", "positive",
    "TRAP: every pcall result is used (assigned, tested, returned), and the HTTP call is protected.",
    """--!strict
local HttpService = game:GetService("HttpService")

local function fetch(url: string): string?
    local ok, body = pcall(function()
        return HttpService:GetAsync(url)
    end)
    if ok then
        return body
    end
    return nil
end

local function safely(fn: () -> ()): boolean
    return pcall(fn)
end

if not pcall(function()
    workspace:WaitForChild("Door", 5)
end) then
    warn("no door")
end

print(fetch("https://example.invalid/data.json"), safely(function() end))
""")

add("trap_module_graph_ok", "positive",
    "TRAP: modules that require each other in a chain (Main -> Util -> Config, Main -> Config) with no cycle. A folder review must stay clean, in --strict mode too.",
    None, strict=True, folder="modules",
    files={
        "Config.luau": """--!strict
local Config = {
    speed = 16,
}

function Config.get(name: string): number
    return (Config :: any)[name]
end

return Config
""",
        "Util.luau": """--!strict
local Config = require(script.Parent.Config)

local Util = {}

function Util.scaled(value: number): number
    return value * Config.get("speed")
end

return Util
""",
        "Main.luau": """--!strict
local Config = require(script.Parent.Config)
local Util = require(script.Parent.Util)

local Main = {}

function Main.run(): number
    return Util.scaled(2) + Config.get("speed")
end

return Main
""",
    })
