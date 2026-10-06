--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local equip = ReplicatedStorage:WaitForChild("EquipItem") :: RemoteEvent
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

equip.OnServerEvent:Connect(function(player, ...)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	local args = {...}
	local tool = ReplicatedStorage.Items:FindFirstChild(args[1])
	if tool then
		tool:Clone().Parent = player.Backpack
	end
end)

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
