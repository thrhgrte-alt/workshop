--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local UserInputService = game:GetService("UserInputService")

local player = Players.LocalPlayer
local jump = ReplicatedStorage:WaitForChild("RequestDash") :: RemoteEvent
local notice = ReplicatedStorage:WaitForChild("Notice") :: RemoteEvent

UserInputService.InputBegan:Connect(function(input, processed)
	if processed then
		return
	end
	if input.KeyCode == Enum.KeyCode.Q then
		jump:FireServer("dash")
	end
end)

notice.OnClientEvent:Connect(function(text)
	local camera = workspace.CurrentCamera
	print(player.Name, text, camera and camera.FieldOfView)
end)
