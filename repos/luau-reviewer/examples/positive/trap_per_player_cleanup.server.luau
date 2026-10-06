--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

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
