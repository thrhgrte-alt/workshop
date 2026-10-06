--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")

Players.PlayerAdded:Connect(function(player)
	local data = store:GetAsync("Player_" .. player.UserId)
	print(player.Name, data)
end)
