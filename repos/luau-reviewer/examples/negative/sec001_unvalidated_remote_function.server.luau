--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local getInfo = ReplicatedStorage:WaitForChild("GetPlayerInfo") :: RemoteFunction
local profiles: {[number]: {level: number}} = {}
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

getInfo.OnServerInvoke = function(player, targetId)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	return profiles[targetId]
end

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
