--!strict
local function grade(score: number): string
	return if score >= 90 then "A" elseif score >= 80 then "B" else "C"
end

local function pick(a: number, b: number): number
	local bigger = if a > b then a else b
	return bigger
end

local total = 0
for i = 1, 3 do
	local label = if i % 2 == 0 then "even" else "odd"
	total += #label
end

local n = 0
repeat
	n += 1
	task.wait()
until n >= 3

local function outer()
	local function inner()
		return if n > 1 then "many" else if n == 1 then "one" else "none"
	end
	return inner()
end

print(grade(total), pick(1, 2), outer())
