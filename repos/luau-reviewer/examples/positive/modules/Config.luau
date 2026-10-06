--!strict
local Config = {
	speed = 16,
}

function Config.get(name: string): number
	return (Config :: any)[name]
end

return Config
