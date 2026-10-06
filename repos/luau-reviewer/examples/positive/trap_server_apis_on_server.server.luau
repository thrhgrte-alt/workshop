--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local notice = ReplicatedStorage:WaitForChild("Notice") :: RemoteEvent
local templates = ServerStorage:WaitForChild("Templates")

Players.PlayerAdded:Connect(function(player)
	notice:FireClient(player, "welcome")
	notice:FireAllClients(player.Name .. " joined", #templates:GetChildren())
end)
