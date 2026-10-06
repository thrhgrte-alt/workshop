--!strict
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local player = Players.LocalPlayer
local lamps = workspace:WaitForChild("Lamps"):GetChildren()
local connection: RBXScriptConnection? = nil

local function update(dt: number)
	for _, lamp in lamps do
		if lamp:IsA("BasePart") then
			lamp.Transparency = math.sin(os.clock()) * 0.5 + 0.5
		end
	end
end

local function start()
	if connection then
		return
	end
	connection = RunService.Heartbeat:Connect(update)
end

local function stop()
	if connection then
		connection:Disconnect()
		connection = nil
	end
end

player.CharacterAdded:Connect(start)
player.CharacterRemoving:Connect(stop)
