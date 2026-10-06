--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ping = ReplicatedStorage:WaitForChild("PingTarget") :: RemoteEvent
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

local function isValidTarget(target: any): boolean
	return typeof(target) == "Instance" and target:IsA("BasePart") and target:IsDescendantOf(workspace)
end

ping.OnServerEvent:Connect(function(player, target)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	if not isValidTarget(target) then
		return
	end
	print(player.Name, "pinged", target)
end)

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
