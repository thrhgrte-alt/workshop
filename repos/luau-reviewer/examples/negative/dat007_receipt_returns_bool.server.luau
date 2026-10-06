--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")

local function grant(player: Player, productId: number) end

MarketplaceService.ProcessReceipt = function(receipt)
	local player = Players:GetPlayerByUserId(receipt.PlayerId)
	if not player then
		return false
	end
	grant(player, receipt.ProductId)
	return true
end
