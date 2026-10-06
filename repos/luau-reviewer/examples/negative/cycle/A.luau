--!strict
local B = require(script.Parent.B)

local A = {}

function A.run(): number
	return B.value() + 1
end

function A.base(): number
	return 1
end

return A
