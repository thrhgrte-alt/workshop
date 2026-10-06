--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local buy = ReplicatedStorage:WaitForChild("BuyItem") :: RemoteEvent
local function grant(player: Player, itemId: string) end
local COOLDOWN = 0.5
local lastUse: {[Player]: number} = {}

buy.OnServerEvent:Connect(function(player, itemId, price)
	local now = os.clock()
	if now - (lastUse[player] or 0) < COOLDOWN then
		return
	end
	lastUse[player] = now
	if typeof(itemId) ~= "string" or typeof(price) ~= "number" then
		return
	end
	local stats = player:FindFirstChild("leaderstats")
	local coins = stats and stats:FindFirstChild("Coins")
	if coins and coins:IsA("IntValue") and coins.Value >= price then
		coins.Value -= price
		grant(player, itemId)
	end
end)

Players.PlayerRemoving:Connect(function(player)
	lastUse[player] = nil
end)
