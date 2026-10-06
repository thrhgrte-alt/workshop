--!strict
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
