--!strict
local HttpService = game:GetService("HttpService")

local ok, source = pcall(function()
	return HttpService:GetAsync("https://example.invalid/hotfix.lua")
end)
if ok then
	local fn = loadstring(source)
	if fn then
		fn()
	end
end
