--!strict
local MarketplaceService = game:GetService("MarketplaceService")
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")

local purchases = DataStoreService:GetDataStore("Purchases")
local SESSION_OWNER = game.JobId

local function withRetry(fn: () -> ()): boolean
	for attempt = 1, 3 do
		local ok, err = pcall(fn)
		if ok then
			return true
		end
		warn("purchase save attempt", attempt, "failed:", err)
		task.wait(attempt)
	end
	return false
end

MarketplaceService.ProcessReceipt = function(receipt): Enum.ProductPurchaseDecision
	local player = Players:GetPlayerByUserId(receipt.PlayerId)
	if not player then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	local recorded = withRetry(function()
		purchases:UpdateAsync("Receipt_" .. receipt.PurchaseId, function(old)
			if old then
				return old
			end
			return { granted = os.time(), owner = SESSION_OWNER }
		end)
	end)
	if not recorded then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end
	return Enum.ProductPurchaseDecision.PurchaseGranted
end
