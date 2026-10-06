--!strict
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local store = DataStoreService:GetDataStore("PlayerData")
local get = store.GetAsync

Players.PlayerAdded:Connect(function(player)
	local data = get(store, "Player_" .. player.UserId)
	print(data)
end)
