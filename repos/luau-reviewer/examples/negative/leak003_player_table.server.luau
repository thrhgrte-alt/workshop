--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local sessions: {[Player]: {joined: number}} = {}

Players.PlayerAdded:Connect(function(player)
	sessions[player] = {joined = os.time()}
end)
