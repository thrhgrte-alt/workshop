#!/usr/bin/env python3
"""Generate the SYNTHETIC places used by the tests and evals (examples/places/*.collect.json, in the collector's placemap-collect/1 format) and the synthetic samples.

Everything here is invented by the builder. The names (Bob, Thing1, ShopSign ...) are fixtures, not data from any real game, and no role definition may depend on them.
Run:  python scripts/make_examples.py          (rewrites examples/places and samples/synthetic; --check exits 1 if the files would change)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from placemap.domain.util import fingerprint  # noqa: E402

SERVICE_CLASS = {"Workspace": "Workspace", "ReplicatedStorage": "ReplicatedStorage", "ServerScriptService": "ServerScriptService", "ServerStorage": "ServerStorage",
                 "StarterGui": "StarterGui", "StarterPack": "StarterPack", "StarterPlayer": "StarterPlayer", "ReplicatedFirst": "ReplicatedFirst"}


class Place:
    def __init__(self, name: str, place_id: int, taken_unix: int):
        self.name, self.place_id, self.taken = name, place_id, taken_unix
        self.nodes: dict[str, dict] = {}

    def add(self, path: str, cls: str, attrs: dict | None = None, pos: tuple | None = None, src: str | None = None, rc: str | None = None, tags: list | None = None) -> None:
        parts = path.split("/")
        for i in range(1, len(parts)):
            anc = "/".join(parts[:i])
            if anc not in self.nodes:
                self.nodes[anc] = {"p": anc, "c": SERVICE_CLASS.get(anc) if i == 1 and anc in SERVICE_CLASS else "Folder"}
        node: dict = {"p": path, "c": cls}
        if attrs:
            node["a"] = attrs
        if tags:
            node["t"] = tags
        if pos:
            node["v"] = [float(x) for x in pos]
        if src is not None:
            node["s"] = {"fp": fingerprint(src), "len": len(src.encode("utf-8")), "src": src}
            if rc:
                node["rc"] = rc
        self.nodes[path] = node

    def script(self, path: str, src: str, cls: str = "Script") -> None:
        self.add(path, cls, src=src.strip("\n") + "\n", rc="Server" if cls == "Script" else None)

    def module(self, path: str, src: str) -> None:
        self.add(path, "ModuleScript", src=src.strip("\n") + "\n")

    def local(self, path: str, src: str) -> None:
        self.add(path, "LocalScript", src=src.strip("\n") + "\n")

    def collect(self) -> dict:
        paths = sorted(self.nodes)
        kids: dict[str, int] = {}
        for p in paths:
            if "/" in p:
                par = p.rsplit("/", 1)[0]
                kids[par] = kids.get(par, 0) + 1
        nodes = []
        for p in paths:
            n = dict(self.nodes[p])
            n["n"] = kids.get(p, 0)
            nodes.append(n)
        roots = sorted({p.split("/")[0] for p in paths})
        return {"format": "placemap-collect/1", "collector_version": 1, "place": {"name": self.name, "place_id": self.place_id}, "taken_unix": self.taken, "roots": roots, "full": True,
                "truncated": False, "skipped": {"instances": 0, "sources": 0, "plain_leaves": 0, "secrets": 0}, "nodes": nodes}

    def n_scripts(self) -> int:
        return sum(1 for n in self.nodes.values() if n["c"] in ("Script", "LocalScript", "ModuleScript"))


RS = 'local RS = game:GetService("ReplicatedStorage")\n'
REMOTES = 'local Remotes = RS:WaitForChild("Remotes")\n'


def modreq(*names: str) -> str:
    return "".join(f'local {n} = require(RS.Modules.{n})\n' for n in names)


def svcreq(*names: str) -> str:
    return "".join(f'local {n} = require(script.Parent.{n})\n' for n in names)


def build_mine(variant: str = "main") -> Place:
    """The synthetic mining game: 40 scripts, 12 remotes-and-roles fixtures. variant: main | v2 (changed since main) | hardcore (a sibling place with a drifted shared module)."""
    name = {"main": "Dive and Mine (SYNTHETIC)", "v2": "Dive and Mine (SYNTHETIC)", "hardcore": "Dive and Mine Hardcore (SYNTHETIC)"}[variant]
    pid = 9000000002 if variant == "hardcore" else 0
    taken = {"main": 1790000000, "v2": 1790086400, "hardcore": 1790000600}[variant]
    P = Place(name, pid, taken)
    # --- remotes ------------------------------------------------------------------------------------------------------------------
    for r in ("PurchaseItem", "SellOre", "EquipTool", "UpgradeTool", "MineNode", "CollectItem", "UpdateCash", "UnlockGate", "EnterZone", "RequestSpawn", "LegacyPing"):
        P.add(f"ReplicatedStorage/Remotes/{r}", "RemoteEvent")
    P.add("ReplicatedStorage/Remotes/GetStock", "RemoteFunction")
    if variant == "v2":
        P.add("ReplicatedStorage/Remotes/ClaimDaily", "RemoteEvent")
    if variant == "hardcore":
        P.add("ReplicatedStorage/Remotes/Revive", "RemoteEvent")
    # --- modules ------------------------------------------------------------------------------------------------------------------
    P.module("ReplicatedStorage/Modules/Util", "-- Small helpers shared by server and client.\nlocal Util = {}\nfunction Util.clamp(x, lo, hi) return math.max(lo, math.min(hi, x)) end\nreturn Util")
    P.module("ReplicatedStorage/Modules/Signal", "-- Minimal signal class.\nlocal Signal = {}\nfunction Signal.new() return setmetatable({}, Signal) end\nreturn Signal")
    P.module("ReplicatedStorage/Modules/Config", "-- Game configuration constants.\nreturn {StartingCash = 100, MaxLevel = 50}")
    P.module("ReplicatedStorage/Modules/Formatter", RS + modreq("Util") + "-- Formats numbers for the HUD.\nlocal Formatter = {}\nfunction Formatter.short(n) return tostring(Util.clamp(n, 0, 1e12)) end\nreturn Formatter")
    P.module("ReplicatedStorage/Modules/ShopData", RS + modreq("Config") + "-- Item catalogue.\nreturn {Items = {Basic = {Price = Config.StartingCash}}}")
    P.module("ReplicatedStorage/Modules/ToolStats", RS + modreq("Config") + "-- Per level tool stats.\nreturn {Levels = Config.MaxLevel}")
    P.module("ReplicatedStorage/Modules/OreData", RS + modreq("Config") + "-- Ore values.\nreturn {Common = 1, Rare = 10}")
    P.module("ReplicatedStorage/Modules/ZoneData", RS + modreq("Config") + "-- Zone rules.\nreturn {Shallow = {MinLevel = 1}}")
    P.module("ReplicatedStorage/Modules/GateData", RS + modreq("Config") + "-- Gate requirements.\nreturn {Door1 = {Level = 5}}")
    if variant == "hardcore":  # the SAME module name and a different body: drift
        eco = RS + modreq("Config", "Util") + "-- Currency maths (hardcore: double costs).\nlocal Economy = {}\nfunction Economy.cost(base, level) return base * 2 * level end\nfunction Economy.sell(v) return v end\nreturn Economy"
    elif variant == "v2":
        eco = RS + modreq("Config", "Util") + "-- Currency maths.\nlocal Economy = {}\nfunction Economy.cost(base, level) return base * level end\nfunction Economy.sell(v) return v end\nfunction Economy.refund(v) return v / 2 end\nreturn Economy"
    else:
        eco = RS + modreq("Config", "Util") + "-- Currency maths.\nlocal Economy = {}\nfunction Economy.cost(base, level) return base * level end\nfunction Economy.sell(v) return v end\nreturn Economy"
    P.module("ReplicatedStorage/Modules/Economy", eco)
    # --- server services -----------------------------------------------------------------------------------------------------------
    P.module("ServerScriptService/Services/DataService", RS + modreq("Util", "Config") + '-- Saves and loads player data.\nlocal DSS = game:GetService("DataStoreService")\nlocal store = DSS:GetDataStore("PlayerData")\n'
             'local D = {}\nfunction D.load(player) return store:GetAsync("Player_" .. player.UserId) end\nfunction D.save(player, data) store:UpdateAsync("Player_" .. player.UserId, function() return data end) end\nreturn D')
    P.module("ServerScriptService/Services/ShopService", RS + REMOTES + modreq("ShopData", "Economy") + svcreq("DataService") + '-- Sells items to players.\nlocal Shop = {}\n'
             'local Cash = Remotes:WaitForChild("UpdateCash")\nlocal Stock = Remotes:WaitForChild("GetStock")\nStock.OnServerInvoke = function(player) return ShopData.Items end\n'
             'function Shop.purchase(player, item)\n\tCash:FireClient(player, Economy.cost(1, 1))\nend\nreturn Shop')
    P.module("ServerScriptService/Services/MiningService", RS + REMOTES + modreq("OreData", "Economy") + svcreq("DataService") + '-- Handles mining results.\nlocal M = {}\nfunction M.mine(player, node) return OreData.Common end\nreturn M')
    P.module("ServerScriptService/Services/SellService", RS + REMOTES + modreq("Economy") + svcreq("DataService") + '-- Sells ore for cash.\nlocal S = {}\nlocal Cash = Remotes:WaitForChild("UpdateCash")\n'
             'function S.sell(player, amount) Cash:FireClient(player, Economy.sell(amount)) end\nreturn S')
    P.module("ServerScriptService/Services/UpgradeService", RS + REMOTES + modreq("ToolStats", "Economy") + svcreq("DataService") + '-- Upgrades and equips tools.\nlocal U = {}\n'
             'local Up = Remotes:WaitForChild("UpgradeTool")\nfunction U.upgrade(player) local tool = game:GetService("StarterPack"):FindFirstChild("Drill") Up:FireClient(player, ToolStats.Levels) end\nreturn U')
    P.module("ServerScriptService/Services/RebirthService", RS + modreq("Economy", "Config") + svcreq("DataService") + '-- Rebirth logic.\nreturn {}')
    P.module("ServerScriptService/Services/LeaderboardService", RS + modreq("Util") + '-- Top cash leaderboard.\nlocal ds = game:GetService("DataStoreService"):GetOrderedDataStore("TopCash")\nreturn {}')
    P.module("ServerScriptService/Services/AntiExploit", RS + modreq("Config", "Util") + '-- Rate limit checks.\nreturn {}')
    P.script("ServerScriptService/Main", "--!strict\n-- Server entry point: starts every service.\n" + RS.replace("local RS = ", "local RS = ") + "".join(f"local {n} = require(script.Parent.Services.{n})\n" for n in
             ("DataService", "ShopService", "MiningService", "SellService", "UpgradeService", "RebirthService", "LeaderboardService")))
    handlers = {"PurchaseHandler": ("ShopService", ["PurchaseItem"]), "SellHandler": ("SellService", ["SellOre"]), "MineHandler": ("MiningService", ["MineNode"]),
                "EquipHandler": ("UpgradeService", ["EquipTool", "UpgradeTool"])}
    for hn, (svc, rems) in handlers.items():
        body = RS + REMOTES + f'local Service = require(script.Parent.Parent.Services.{svc})\n' + "".join(f'Remotes:WaitForChild("{r}").OnServerEvent:Connect(function(player, ...) end)\n' for r in rems)
        P.script(f"ServerScriptService/Handlers/{hn}", "-- Wires remotes to the " + svc + ".\n" + body)
    P.script("ServerScriptService/Handlers/CollectHandler", "-- Awards collectibles.\n" + RS + REMOTES + 'local DataService = require(script.Parent.Parent.Services.DataService)\nlocal Config = require(RS.Modules.Config)\n'
             'local things = workspace:WaitForChild("Things")\nRemotes:WaitForChild("CollectItem").OnServerEvent:Connect(function(player, item) end)')
    P.script("ServerScriptService/Handlers/ZoneHandler", "-- Checks zone entry.\n" + RS + REMOTES + 'local ZoneData = require(RS.Modules.ZoneData)\nlocal DataService = require(script.Parent.Parent.Services.DataService)\n'
             'Remotes:WaitForChild("EnterZone").OnServerEvent:Connect(function(player, zone) end)')
    P.script("ServerScriptService/Handlers/GateHandler", "-- Opens progression gates.\n" + RS + REMOTES + 'local gates = workspace:WaitForChild("Gates")\nlocal GateData = require(RS.Modules.GateData)\nlocal DataService = require(script.Parent.Parent.Services.DataService)\n'
             'Remotes:WaitForChild("UnlockGate").OnServerEvent:Connect(function(player, gate) end)')
    P.script("ServerScriptService/Handlers/SpawnHandler", "-- Places players at spawns.\n" + RS + REMOTES + 'local Config = require(RS.Modules.Config)\nRemotes:WaitForChild("RequestSpawn").OnServerEvent:Connect(function(player) end)')
    # --- client -------------------------------------------------------------------------------------------------------------------
    P.local("StarterPlayer/StarterPlayerScripts/ClientMain", RS + REMOTES + modreq("Formatter", "Signal") + 'Remotes.RequestSpawn:FireServer()')
    P.local("StarterPlayer/StarterPlayerScripts/ShopUI", RS + REMOTES + modreq("ShopData", "Formatter") + 'local Buy = Remotes:WaitForChild("PurchaseItem")\nlocal Stock = Remotes:WaitForChild("GetStock")\n'
            'local items = Stock:InvokeServer()\nBuy:FireServer("Basic")')
    P.local("StarterPlayer/StarterPlayerScripts/HudController", RS + REMOTES + modreq("Formatter") + 'Remotes.UpdateCash.OnClientEvent:Connect(function(cash) print(Formatter.short(cash)) end)')
    P.local("StarterPlayer/StarterPlayerScripts/MineClient", RS + REMOTES + 'Remotes.MineNode:FireServer("Node1")')
    P.local("StarterPlayer/StarterPlayerScripts/InputController", RS + modreq("Signal") + '-- Keyboard input.')
    P.local("StarterPlayer/StarterPlayerScripts/CollectClient", RS + REMOTES + 'Remotes.CollectItem:FireServer("Thing1")')
    P.local("ReplicatedFirst/Loading", '-- Loading screen.\ngame:GetService("ReplicatedFirst"):RemoveDefaultLoadingScreen()')
    P.add("StarterGui/Hud/CashLabel", "TextLabel", attrs={"Currency": "Cash"})
    P.local("StarterGui/Hud/CashLabel/CashUpdater", RS + REMOTES + 'Remotes.UpdateCash.OnClientEvent:Connect(function(cash) script.Parent.Text = tostring(cash) end)')
    P.add("StarterGui/Hud/TitleLabel", "TextLabel")
    # --- world: vendors, decoys, nodes, collectibles, spawns, zone, gate, tool -------------------------------------------------------
    for vname, pos in (("Bob", (10, 0, 5)), ("Zed", (500, 0, 500))):
        P.add(f"Workspace/Vendors/{vname}", "Model", attrs={"Stock": 10, "Price": 50}, pos=pos)
        P.add(f"Workspace/Vendors/{vname}/Humanoid", "Humanoid")
        P.add(f"Workspace/Vendors/{vname}/Prompt", "ProximityPrompt", attrs={"ActionText": "Talk"})
        P.script(f"Workspace/Vendors/{vname}/Brain", "-- Opens the shop for the player who triggers the prompt.\n" + RS + REMOTES + 'local Buy = Remotes:WaitForChild("PurchaseItem")\n'
                 'script.Parent.Prompt.Triggered:Connect(function(player) Buy:FireClient(player, "open") end)')
    P.add("Workspace/Vendors/ShopSign", "Part", pos=(12, 5, 5))        # decoy: name matches, no interaction
    P.add("Workspace/Vendors/VendorStatue", "Model", pos=(14, 0, 5))   # decoy: name matches, nothing else
    P.add("Workspace/Vendors/VendorStatue/Head", "Part", pos=(14, 3, 5))
    P.script("Workspace/Deposits/DepositController", "-- Regenerates deposits.\nlocal folder = workspace:WaitForChild(\"Deposits\")\nfolder.ChildRemoved:Connect(function() end)")
    for i in range(1, 13):
        P.add(f"Workspace/Deposits/Node{i}", "Part", attrs={"Health": 100, "Value": 5}, pos=(100 + 6 * i, -20, 40))
        P.add(f"Workspace/Deposits/Node{i}/ClickDetector", "ClickDetector")
    for i in range(1, 9):
        P.add(f"Workspace/Things/Thing{i}", "Part", attrs={"Value": 1}, pos=(-30 - 4 * i, 2, -10))
        P.add(f"Workspace/Things/Thing{i}/ClickDetector", "ClickDetector")
    P.add("Workspace/Spawns/Lobby", "SpawnLocation", pos=(0, 0, 0))
    P.add("Workspace/Spawns/CheckpointA", "SpawnLocation", pos=(200, -10, 40), attrs={"Checkpoint": 1})
    P.add("Workspace/Zones/MineZone", "Part", attrs={"ZoneName": "Shallow", "MinLevel": 1}, pos=(120, -20, 40))
    P.script("Workspace/Zones/MineZone/ZoneScript", "-- Reports entry into the zone.\n" + RS + REMOTES + 'local zone = script.Parent\nzone.Touched:Connect(function(hit) Remotes.EnterZone:FireServer(zone.Name) end)')
    P.add("Workspace/Gates/Door1", "Part", attrs={"RequiredLevel": 5, "Locked": True}, pos=(300, 0, 0))
    P.add("Workspace/Gates/Door1/ProximityPrompt", "ProximityPrompt")
    P.add("Workspace/Gates/Door1/Barrier", "Part", pos=(300, 0, 1))
    P.add("StarterPack/Drill", "Tool", attrs={"Level": 1, "Damage": 3})
    P.add("StarterPack/Drill/Handle", "Part")
    P.script("ServerStorage/Tools/ToolGiver", "-- Gives the starter tool.\nlocal tool = game:GetService(\"StarterPack\"):FindFirstChild(\"Drill\")")
    if variant == "v2":
        del P.nodes["Workspace/Vendors/Zed"], P.nodes["Workspace/Vendors/Zed/Humanoid"], P.nodes["Workspace/Vendors/Zed/Prompt"], P.nodes["Workspace/Vendors/Zed/Brain"]
        P.nodes["Workspace/Vendors/Bob"]["v"] = [40.0, 0.0, 5.0]
        del P.nodes["ServerScriptService/Services/AntiExploit"]
        P.script("ServerScriptService/Handlers/DailyHandler", "-- Daily reward.\n" + RS + REMOTES + 'Remotes:WaitForChild("ClaimDaily").OnServerEvent:Connect(function(player) end)')
        P.nodes["Workspace/Vendors/Bob"]["a"] = {"Stock": 10, "Price": 60}
    if variant == "hardcore":
        del P.nodes["ServerScriptService/Services/LeaderboardService"], P.nodes["ServerScriptService/Handlers/SpawnHandler"]
        P.script("ServerScriptService/Handlers/ReviveHandler", "-- Revives players.\n" + RS + REMOTES + 'Remotes:WaitForChild("Revive").OnServerEvent:Connect(function(player) end)')
    return P


def build_role_lab() -> Place:
    """Oddly named objects that must be found by STRUCTURE (a vendor called Bob, a collectible called Thing1) and decoys with matching names but no interaction."""
    P = Place("Role Lab (SYNTHETIC)", 0, 1790000000)
    P.add("ReplicatedStorage/Remotes/BuyStuff", "RemoteEvent")
    P.add("ReplicatedStorage/Remotes/GrabIt", "RemoteEvent")
    P.add("ReplicatedStorage/Remotes/Chatter", "RemoteEvent")
    P.add("Workspace/Npcs/Bob", "Model", attrs={"Price": 20, "Stock": 3}, pos=(0, 0, 0))
    P.add("Workspace/Npcs/Bob/Humanoid", "Humanoid")
    P.add("Workspace/Npcs/Bob/Talk", "ProximityPrompt")
    P.script("Workspace/Npcs/Bob/Logic", "-- Sells.\nlocal Remotes = game:GetService(\"ReplicatedStorage\").Remotes\nRemotes.BuyStuff.OnServerEvent:Connect(function(player) end)")
    P.add("Workspace/Npcs/Carl", "Model", pos=(600, 0, 0))              # a vendor with no price attributes: borderline on purpose
    P.add("Workspace/Npcs/Carl/Humanoid", "Humanoid")
    P.add("Workspace/Npcs/Carl/Talk", "ProximityPrompt")
    P.script("Workspace/Npcs/Carl/Logic", "-- Sells too.\nlocal Remotes = game:GetService(\"ReplicatedStorage\").Remotes\nRemotes.BuyStuff:FireClient(nil, \"open\")")
    P.add("Workspace/Npcs/Alice", "Model", pos=(300, 0, 0))             # a talker, not a vendor: interaction + humanoid, but chatter only
    P.add("Workspace/Npcs/Alice/Humanoid", "Humanoid")
    P.add("Workspace/Npcs/Alice/Talk", "ProximityPrompt")
    P.script("Workspace/Npcs/Alice/Logic", "-- Chats.\nlocal Remotes = game:GetService(\"ReplicatedStorage\").Remotes\nRemotes.Chatter:FireClient(nil, \"hi\")")
    P.add("Workspace/Npcs/ShopSign", "Part", pos=(2, 4, 0))            # decoy: 'shop' in the name, no interaction
    P.add("Workspace/Npcs/VendorStatue", "Model", pos=(4, 0, 0))       # decoy: 'vendor' in the name, no interaction
    P.add("Workspace/Npcs/VendorStatue/Base", "Part", pos=(4, 0, 0))
    P.script("ServerScriptService/Pickup", "-- Collect handling.\nlocal loose = workspace:WaitForChild(\"Loose\")\nlocal Remotes = game:GetService(\"ReplicatedStorage\").Remotes\nRemotes.GrabIt.OnServerEvent:Connect(function(player, item) end)")
    for i in range(1, 7):
        P.add(f"Workspace/Loose/Thing{i}", "Part", attrs={"Value": 2}, pos=(50 + i, 1, 0))
        P.add(f"Workspace/Loose/Thing{i}/Click", "ClickDetector")
    for i in range(1, 4):
        P.add(f"Workspace/Decor/CoinPile{i}", "Part", pos=(70 + i, 1, 0))   # decoys: 'coin' in the name, repeated, but NO interaction
    P.add("Workspace/Start/Pad", "SpawnLocation", pos=(1, 0, 0))
    P.add("Workspace/Start/Entry", "Part", attrs={"Checkpoint": 1}, pos=(0, 0, 3))
    return P


def build_tycoon() -> Place:
    P = Place("Demo Tycoon (SYNTHETIC)", 9000000001, 1790000000)
    P.add("ReplicatedStorage/Remotes/BuyDropper", "RemoteEvent")
    P.module("ReplicatedStorage/Modules/Util", "-- Small helpers shared by server and client.\nlocal Util = {}\nfunction Util.clamp(x, lo, hi) return math.max(lo, math.min(hi, x)) end\nreturn Util")
    P.module("ReplicatedStorage/Modules/Economy", "-- Tycoon currency maths.\nlocal E = {}\nfunction E.income(n) return n * 5 end\nreturn E")
    P.script("ServerScriptService/Plots", "-- Assigns plots.\nlocal Economy = require(game:GetService(\"ReplicatedStorage\").Modules.Economy)")
    P.add("Workspace/Plots/Plot1", "Model", pos=(0, 0, 0))
    P.add("Workspace/Plots/Plot1/Buy", "Part")
    P.add("Workspace/Plots/Plot1/Buy/ClickDetector", "ClickDetector")
    return P


# the intended answers, authored by hand (what a person who built these places would label). Evals compare the tool's output against THIS, not against its own output.
GROUND_TRUTH = {
    "mine-main": {"vendor": ["Workspace/Vendors/Bob", "Workspace/Vendors/Zed"], "resource_node": [f"Workspace/Deposits/Node{i}" for i in range(1, 13)],
                  "collectible": [f"Workspace/Things/Thing{i}" for i in range(1, 9)], "spawn": ["Workspace/Spawns/Lobby", "Workspace/Spawns/CheckpointA"], "zone": ["Workspace/Zones/MineZone"],
                  "currency_display": ["StarterGui/Hud/CashLabel"], "progression_gate": ["Workspace/Gates/Door1"], "upgradable_tool": ["StarterPack/Drill"],
                  "decoys": ["Workspace/Vendors/ShopSign", "Workspace/Vendors/VendorStatue", "StarterGui/Hud/TitleLabel", "Workspace/Gates/Door1/Barrier"]},
    "role-lab": {"vendor": ["Workspace/Npcs/Bob", "Workspace/Npcs/Carl"], "collectible": [f"Workspace/Loose/Thing{i}" for i in range(1, 7)], "spawn": ["Workspace/Start/Pad"],
                 "decoys": ["Workspace/Npcs/ShopSign", "Workspace/Npcs/VendorStatue", "Workspace/Npcs/Alice", "Workspace/Decor/CoinPile1", "Workspace/Decor/CoinPile2", "Workspace/Decor/CoinPile3"]},
}


def outputs() -> dict[str, str]:
    files: dict[str, str] = {}
    main, v2, hard = build_mine("main"), build_mine("v2"), build_mine("hardcore")
    assert main.n_scripts() == 40, main.n_scripts()
    for key, place in (("mine-main", main), ("mine-main.v2", v2), ("mine-hardcore", hard), ("role-lab", build_role_lab()), ("tycoon-main", build_tycoon())):
        files[f"examples/places/{key}.collect.json"] = json.dumps(place.collect(), indent=1, sort_keys=True) + "\n"
    files["examples/places/ground_truth.json"] = json.dumps(GROUND_TRUTH, indent=1, sort_keys=True) + "\n"
    # synthetic samples for the parsers other than the collector's own format (SYNTHETIC: written by the builder, NOT captured from the hub)
    small = build_tycoon().collect()
    files["samples/synthetic/collect-tycoon.json"] = json.dumps(small, indent=1, sort_keys=True) + "\n"
    files["samples/synthetic/tree-generic.json"] = json.dumps({"result": {"Name": "Workspace", "ClassName": "Workspace", "Children": [
        {"Name": "Vendors", "ClassName": "Folder", "Children": [{"Name": "Bob", "ClassName": "Model", "Attributes": {"Price": 5}, "Children": [{"Name": "Prompt", "ClassName": "ProximityPrompt"}]}]}]}}, indent=1) + "\n"
    files["samples/synthetic/tree-outline.txt"] = ("# SYNTHETIC text outline (path | Class | key=value; key=value)\nWorkspace | Workspace\nWorkspace/Vendors | Folder\nWorkspace/Vendors/Bob | Model | Price=5; Stock=2\n"
                                                    "Workspace/Vendors/Bob/Prompt | ProximityPrompt\n")
    files["samples/synthetic/script-read.json"] = json.dumps([{"path": "ServerScriptService/Plots", "source": "-- Assigns plots.\nreturn 1\n"}], indent=1) + "\n"
    files["samples/synthetic/script-read.txt"] = "=== ServerScriptService/Plots ===\n-- Assigns plots.\nreturn 1\n"
    return files


def main() -> int:
    check = "--check" in sys.argv
    changed = []
    for rel, text in outputs().items():
        f = ROOT / rel
        if check:
            if not f.exists() or f.read_text(encoding="utf-8") != text:
                changed.append(rel)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
    if check:
        print(json.dumps({"out_of_date": changed}))
        return 1 if changed else 0
    print(f"wrote {len(outputs())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
