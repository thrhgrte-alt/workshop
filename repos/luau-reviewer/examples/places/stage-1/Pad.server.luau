--!strict
local pad = workspace:WaitForChild("Pad") :: BasePart

spawn(function()
	print("pad ready")
end)

pad.Touched:Connect(function(hit)
	print(hit.Name)
end)
