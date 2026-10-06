--!strict
local RunService = game:GetService("RunService")

RunService.Heartbeat:Connect(function()
	local spark = Instance.new("Part")
	spark.Anchored = true
	spark.Parent = workspace
end)
