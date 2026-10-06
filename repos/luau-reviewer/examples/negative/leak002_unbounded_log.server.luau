--!strict
local RunService = game:GetService("RunService")

local samples: {number} = {}

RunService.Heartbeat:Connect(function(dt)
	table.insert(samples, dt)
end)
