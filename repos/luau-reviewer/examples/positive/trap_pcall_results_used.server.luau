--!strict
local HttpService = game:GetService("HttpService")

local function fetch(url: string): string?
	local ok, body = pcall(function()
		return HttpService:GetAsync(url)
	end)
	if ok then
		return body
	end
	return nil
end

local function safely(fn: () -> ()): boolean
	return pcall(fn)
end

if not pcall(function()
	workspace:WaitForChild("Door", 5)
end) then
	warn("no door")
end

print(fetch("https://example.invalid/data.json"), safely(function() end))
