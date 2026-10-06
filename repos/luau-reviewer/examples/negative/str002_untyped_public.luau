--!strict
local M = {}

function M.add(a, b)
	return a + b
end

function M.greet(name: string): string
	return "hi " .. name
end

return M
