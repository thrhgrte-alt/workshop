--!strict
local RunService = game:GetService("RunService")

RunService.Heartbeat:Connect(function()
	for _, part in workspace:GetDescendants() do
		if part:IsA("BasePart") and part.Name == "Glow" then
			part.Transparency = 0.5
		end
	end
end)
