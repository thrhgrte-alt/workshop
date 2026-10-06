--!strict
local function play(humanoid: Humanoid, animation: Animation)
	local track = humanoid:LoadAnimation(animation)
	track:Play()
end

return play
