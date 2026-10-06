--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local ask = ReplicatedStorage:WaitForChild("AskClient") :: RemoteFunction

Players.PlayerAdded:Connect(function(player)
	local answer = ask:InvokeClient(player, "ready?")
	print(player.Name, answer)
end)
