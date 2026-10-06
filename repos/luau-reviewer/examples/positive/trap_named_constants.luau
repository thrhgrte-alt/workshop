--!strict
local AGGRO_RANGE = 37
local MAX_HEALTH = 100
local RESPAWN_DELAY = 5

local function shouldChase(distance: number): boolean
	return distance < AGGRO_RANGE
end

local function isDead(health: number): boolean
	return health <= 0 or health > MAX_HEALTH
end

task.delay(RESPAWN_DELAY, function()
	print(shouldChase(10), isDead(50))
end)
