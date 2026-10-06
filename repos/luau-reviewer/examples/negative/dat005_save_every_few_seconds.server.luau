--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local SESSION_OWNER = game.JobId

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

game:BindToClose(function()
	for _, player in Players:GetPlayers() do
		task.spawn(save, player)
	end
	task.wait(3)
end)
