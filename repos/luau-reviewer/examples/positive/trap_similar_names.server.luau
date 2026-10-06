--!strict
local ServerScriptService = game:GetService("ServerScriptService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local loadstringEnabled = ServerScriptService.LoadStringEnabled
local tickRate = 1 / 30
local Config = require(ReplicatedStorage:WaitForChild("Config"))
local Util = require(script.Parent:WaitForChild("Util"))

print(string.format("loadstring enabled: %s, tick: %.3f", tostring(loadstringEnabled), tickRate), Config, Util)
