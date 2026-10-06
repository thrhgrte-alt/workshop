--!strict
local Config = require(script.Parent.Config)
local Util = require(script.Parent.Util)

local Main = {}

function Main.run(): number
	return Util.scaled(2) + Config.get("speed")
end

return Main
