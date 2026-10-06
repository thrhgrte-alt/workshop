--!strict
local Inventory = {}
Inventory.__index = Inventory

export type Inventory = { items: { [string]: number } }

function Inventory.new(): Inventory
	return setmetatable({ items = {} }, Inventory) :: any
end

function Inventory.add(self: Inventory, name: string, count: number): ()
	self.items[name] = (self.items[name] or 0) + count
end

function Inventory.count(self: Inventory, name: string): number
	return self.items[name] or 0
end

return Inventory
