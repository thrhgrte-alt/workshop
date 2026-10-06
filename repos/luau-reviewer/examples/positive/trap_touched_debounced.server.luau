--!strict
local pad = workspace:WaitForChild("HealPad") :: BasePart
local debounce: {[Instance]: boolean} = {}

pad.Touched:Connect(function(hit)
	local character = hit.Parent
	if not character or debounce[character] then
		return
	end
	debounce[character] = true
	local humanoid = character:FindFirstChildOfClass("Humanoid")
	if humanoid then
		humanoid.Health += 1
	end
	task.delay(1, function()
		debounce[character] = nil
	end)
end)
