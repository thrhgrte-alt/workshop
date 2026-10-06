--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local buy = ReplicatedStorage:WaitForChild("BuyItem") :: RemoteEvent
local PRICES: {[string]: number} = { sword = 100, shield = 80 }
local COOLDOWN = 1
local lastBuy: {[Player]: number} = {}

buy.OnServerEvent:Connect(function(player, itemId, quantity)
	local now = os.clock()
	if now - (lastBuy[player] or 0) < COOLDOWN then
		return
	end
	if typeof(itemId) ~= "string" or typeof(quantity) ~= "number" then
		return
	end
	local price = PRICES[itemId]
	if not price then
		return
	end
	local count = math.clamp(math.floor(quantity), 1, 10)
	local stats = player:FindFirstChild("leaderstats")
	local coins = stats and stats:FindFirstChild("Coins")
	if coins and coins:IsA("IntValue") and coins.Value >= price * count then
		lastBuy[player] = now
		coins.Value -= price * count
	end
end)

Players.PlayerRemoving:Connect(function(player)
	lastBuy[player] = nil
end)
