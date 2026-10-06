--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local claim = ReplicatedStorage:WaitForChild("ClaimChest") :: RemoteEvent
local CHESTS: {[string]: number} = { wooden = 10, iron = 50 }

claim.OnServerEvent:Connect(function(player, chestId)
	if typeof(chestId) ~= "string" or not CHESTS[chestId] then
		return
	end
	player:SetAttribute("Gems", (player:GetAttribute("Gems") or 0) + CHESTS[chestId])
end)
