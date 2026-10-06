--!strict
local crates = workspace:WaitForChild("Crates")

local function respawnAll()
	for _, crate in crates:GetChildren() do
		crate:SetAttribute("Full", true)
	end
end

task.spawn(function()
	while true do
		task.wait(30)
		respawnAll()
		local boss = workspace:FindFirstChild("Boss")
		if boss then
			print(#boss:GetDescendants())
		end
	end
end)
