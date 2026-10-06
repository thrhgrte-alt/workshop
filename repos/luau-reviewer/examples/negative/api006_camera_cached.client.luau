--!strict
local camera = workspace.CurrentCamera

local function zoomIn()
	if camera then
		camera.FieldOfView = 50
	end
end

zoomIn()
