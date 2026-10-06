--!strict
local LIMIT = 100
local ready = false
task.delay(5, function()
	ready = true
end)

local count = 0
while true do
	count += 1
	if count > LIMIT then
		break
	end
end

local polls = 0
repeat
	polls += 1
	task.wait(0.25)
until ready or polls >= LIMIT

task.spawn(function()
	while true do
		task.wait(1)
		print("tick", os.clock())
	end
end)
