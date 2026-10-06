--!strict
local Config = require(script.Parent.Config)

local Util = {}

function Util.scaled(value: number): number
	return value * Config.get("speed")
end

return Util
