--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local hit = ReplicatedStorage:WaitForChild("DealDamage") :: RemoteEvent
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

hit.OnServerEvent:Connect(function(player, target, damage)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	if typeof(target) ~= "Instance" or not target:IsA("Model") or typeof(damage) ~= "number" then
		return
	end
	local humanoid = target:FindFirstChildOfClass("Humanoid")
	if humanoid then
		humanoid:TakeDamage(damage)
	end
end)

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
