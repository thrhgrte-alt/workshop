--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

Players.PlayerAdded:Connect(function(player)
	RunService.Heartbeat:Connect(function(dt)
		local character = player.Character
		if character then
			character:SetAttribute("Age", (character:GetAttribute("Age") or 0) + dt)
		end
	end)
end)
