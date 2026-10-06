--!strict
local lamp = workspace:WaitForChild("Lamp") :: BasePart
while true do
	wait(1)
	lamp.Transparency = 1 - lamp.Transparency
end
