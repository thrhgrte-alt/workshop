--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")

local function grant(player: Player, productId: number) end

MarketplaceService.ProcessReceipt = function(receipt)
	local player = Players:GetPlayerByUserId(receipt.PlayerId)
	if player then
		grant(player, receipt.ProductId)
	end
	return Enum.ProductPurchaseDecision.PurchaseGranted
end
