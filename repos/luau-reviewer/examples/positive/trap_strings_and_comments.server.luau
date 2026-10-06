--!strict
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
