--!strict
local Cache = {}
Cache.__index = Cache

export type Cache = typeof(setmetatable({} :: { entries: { [string]: any } }, Cache))

function Cache.new(): Cache
	return setmetatable({ entries = {} }, Cache)
end

function Cache.GetAsync(self: Cache, key: string): any
	return self.entries[key]
end

function Cache.SetAsync(self: Cache, key: string, value: any)
	self.entries[key] = value
end

local cache = Cache.new()
cache:SetAsync("a", 1)
local value = cache:GetAsync("a")
print(value)

return Cache
