--!strict
local detector = workspace:WaitForChild("Door"):WaitForChild("ClickDetector") :: ClickDetector

detector.MouseClick:connect(function(player)
	print(player.Name, "clicked")
end)
