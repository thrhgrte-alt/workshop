--!strict
local function inRange(distance: number): boolean
	if distance < 37 then
		return true
	end
	return distance == 412
end

print(inRange(10))
