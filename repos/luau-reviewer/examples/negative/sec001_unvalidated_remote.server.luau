--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local equip = ReplicatedStorage:WaitForChild("EquipItem") :: RemoteEvent
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

equip.OnServerEvent:Connect(function(player, itemName, slot)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	local backpack = player:FindFirstChild("Backpack")
	local tool = ReplicatedStorage.Items:FindFirstChild(itemName)
	if backpack and tool then
		tool:Clone().Parent = backpack
		print("equipped in slot", slot)
	end
end)

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
