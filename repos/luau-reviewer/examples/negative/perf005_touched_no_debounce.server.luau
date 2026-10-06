--!strict
local pad = workspace:WaitForChild("HealPad") :: BasePart

pad.Touched:Connect(function(hit)
	local humanoid = hit.Parent and hit.Parent:FindFirstChildOfClass("Humanoid")
	if humanoid then
		humanoid.Health += 1
	end
end)
