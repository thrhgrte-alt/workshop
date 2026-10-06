--!strict
local door = workspace:FindFirstChild("Door")

pcall(function()
	if door then
		door:Destroy()
	end
end)
