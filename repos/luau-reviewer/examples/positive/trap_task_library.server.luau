--!strict
local RunService = game:GetService("RunService")

local config = { wait = 2, delay = 0.5, spawn = "Spawn1" }

local function waitForReady(): boolean
	local deadline = os.clock() + config.wait
	while os.clock() < deadline do
		task.wait(0.1)
	end
	return true
end

local function spawnEnemy(name: string)
	local enemy = Instance.new("Part")
	enemy.Name = name
	enemy.Anchored = true
	enemy.Parent = workspace
	task.delay(10, function()
		enemy:Destroy()
	end)
end

task.spawn(function()
	waitForReady()
	spawnEnemy(config.spawn)
end)

local ready = Instance.new("BindableEvent")
task.defer(function()
	ready:Fire()
end)
ready.Event:Wait()
RunService.Heartbeat:Wait()
