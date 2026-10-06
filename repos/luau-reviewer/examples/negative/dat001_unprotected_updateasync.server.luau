--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local SESSION_OWNER = game.JobId

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
