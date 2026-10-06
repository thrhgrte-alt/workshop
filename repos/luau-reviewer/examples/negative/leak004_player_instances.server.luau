--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

Players.PlayerAdded:Connect(function(player)
	local marker = Instance.new("Part")
	marker.Name = player.Name .. "_Marker"
	marker.Anchored = true
	marker.Parent = workspace
end)
