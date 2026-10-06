--!strict
local A = require(script.Parent.A)

local B = {}

function B.value(): number
	return A.base() * 2
end

return B
