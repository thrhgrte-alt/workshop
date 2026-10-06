--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

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
