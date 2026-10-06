"""The collector Luau: a read-only script the hub runs (execute_luau) to dump the instance tree and script sources as JSON ``placemap-collect/1``.

It reads (GetChildren, GetAttributes, Source, positions, tags) and returns or prints JSON. It never writes, creates, destroys, saves, publishes or touches the network (the shared
Luau lint enforces that), embeds a place guard (runs only in the registered place), skips attributes that look like keys or tokens, caps its own work (instances, source
characters) and says so when it truncates. Everything user-supplied (root names, known fingerprints) is embedded through ``quote_string``.

The output shape this repository's parser expects::

    {"format": "placemap-collect/1", "collector_version": 1, "place": {"name", "place_id", "game_id"}, "taken_unix": N, "roots": ["Workspace/Vendors"], "full": bool,
     "truncated": bool, "skipped": {"instances", "sources", "plain_leaves", "secrets"}, "missing_roots": [...],
     "nodes": [{"p": "Workspace/Vendors/Bob", "c": "Model", "a": {attrs}, "t": [tags], "v": [x, y, z], "n": childCount, "rc": "Client",
                "s": {"fp": "<bytes>:<hex>", "len": bytes, "src": "<source, omitted when the fingerprint is already known>", "cut": true}}]}
"""

from __future__ import annotations

import re

from ..guide_adapter import luau_safety as LS
from .secrets import SecretFilter, luau_key_patterns
from .util import split_path, unescape_segment

COLLECTOR_VERSION = 1
MAX_KNOWN = 6000
_SEG_OK = re.compile(r"[^\x00-\x1f]{1,100}")

TEMPLATE = r'''-- place-map collector v@@VERSION@@: READ-ONLY. Reads the instance tree and script sources and returns JSON (placemap-collect/1). Run it in Studio edit mode with execute_luau.
@@GUARD@@
local MAX_INSTANCES = @@MAX_INSTANCES@@
local MAX_SOURCE = @@MAX_SOURCE@@
local MAX_TOTAL_SOURCE = @@MAX_TOTAL@@
local LEAF_CAP = @@LEAF_CAP@@
local ATTR_MAX = @@ATTR_MAX@@
local EMIT_PRINT = @@EMIT_PRINT@@
local CHUNK = @@CHUNK@@
local FULL = @@FULL@@
local ROOTS = @@ROOTS@@
local SKIP = @@SKIP@@
local KNOWN = @@KNOWN@@
local SECRET_TOKENS = @@SECRET_TOKENS@@
local SECRET_EXACT = @@SECRET_EXACT@@

local skipped = {instances = 0, sources = 0, plain_leaves = 0, secrets = 0}
local nodes = {}
local totalSource = 0
local truncated = false

local function esc(name)
	name = string.gsub(name, "%%", "%%25")
	name = string.gsub(name, "/", "%%2F")
	return name
end

local function fingerprint(s)
	local h = 0
	for i = 1, #s do
		h = (h * 31 + string.byte(s, i)) % 4294967291
	end
	return tostring(#s) .. ":" .. string.format("%x", h)
end

local function keyIsSecret(key)
	local k = string.lower(tostring(key))
	k = string.gsub(k, "[^a-z0-9]", "")
	for _, e in ipairs(SECRET_EXACT) do
		if k == e then return true end
	end
	for _, t in ipairs(SECRET_TOKENS) do
		if string.find(k, t, 1, true) then return true end
	end
	return false
end

local function valueIsSecret(v)
	if type(v) ~= "string" then return false end
	local low = string.lower(v)
	if string.find(low, "bearer ", 1, true) or string.find(low, "webhook", 1, true) or string.find(low, "token=", 1, true) or string.find(low, "secret=", 1, true) then return true end
	for word in string.gmatch(v, "%S+") do
		if #word >= 32 and not string.find(word, "[^%w%+/=_%-]") and string.find(word, "%d") and string.find(word, "%a") then return true end
	end
	return false
end

local function readAttrs(inst)
	local ok, attrs = pcall(function() return inst:GetAttributes() end)
	if not ok or type(attrs) ~= "table" then return nil end
	local out, any, n = {}, false, 0
	for k, v in pairs(attrs) do
		if keyIsSecret(k) or valueIsSecret(v) then
			skipped.secrets = skipped.secrets + 1
		elseif n < 60 then
			local tv = typeof(v)
			if tv == "number" then
				if v ~= v or v == math.huge or v == -math.huge then v = tostring(v) end
			elseif tv == "string" then
				if #v > ATTR_MAX then v = string.sub(v, 1, ATTR_MAX) end
			elseif tv ~= "boolean" then
				v = string.sub(tostring(v), 1, 80)
			end
			out[tostring(k)] = v
			any = true
			n = n + 1
		end
	end
	if any then return out end
	return nil
end

local function readTags(inst)
	local ok, svc = pcall(function() return game:GetService("CollectionService") end)
	if not ok or not svc then return nil end
	local ok2, tags = pcall(function() return svc:GetTags(inst) end)
	if ok2 and type(tags) == "table" and #tags > 0 then return tags end
	return nil
end

local function readPos(inst)
	local ok, isPart = pcall(function() return inst:IsA("BasePart") end)
	if ok and isPart then
		local ok2, p = pcall(function() return inst.Position end)
		if ok2 and p then return {math.floor(p.X * 10 + 0.5) / 10, math.floor(p.Y * 10 + 0.5) / 10, math.floor(p.Z * 10 + 0.5) / 10} end
	end
	local ok3, isModel = pcall(function() return inst:IsA("Model") end)
	if ok3 and isModel then
		local ok4, p = pcall(function() return inst:GetPivot().Position end)
		if ok4 and p then return {math.floor(p.X * 10 + 0.5) / 10, math.floor(p.Y * 10 + 0.5) / 10, math.floor(p.Z * 10 + 0.5) / 10} end
	end
	return nil
end

local function isScript(inst)
	local c = inst.ClassName
	return c == "Script" or c == "LocalScript" or c == "ModuleScript"
end

local function readScript(inst, path, node)
	local ok, src = pcall(function() return inst.Source end)
	if not ok or type(src) ~= "string" then
		skipped.sources = skipped.sources + 1
		node.s = {skip = true}
		return
	end
	local cut = false
	if #src > MAX_SOURCE then
		src = string.sub(src, 1, MAX_SOURCE)
		cut = true
	end
	local fp = fingerprint(src)
	local s = {fp = fp, len = #src}
	if cut then s.cut = true end
	if KNOWN[path] ~= fp then
		if totalSource + #src > MAX_TOTAL_SOURCE then
			skipped.sources = skipped.sources + 1
			s.skip = true
			truncated = true
		else
			totalSource = totalSource + #src
			s.src = src
		end
	end
	node.s = s
	if inst.ClassName == "Script" then
		local okc, rc = pcall(function() return inst.RunContext end)
		if okc and rc ~= nil then node.rc = tostring(rc) end
	end
end

local function isPlainLeaf(inst, kids)
	if #kids > 0 then return false end
	local ok, isPart = pcall(function() return inst:IsA("BasePart") end)
	if not (ok and isPart) then return false end
	local ok2, attrs = pcall(function() return inst:GetAttributes() end)
	if ok2 and type(attrs) == "table" and next(attrs) ~= nil then return false end
	if readTags(inst) ~= nil then return false end
	return true
end

local function walk(root, rootPath)
	local stack = {{root, rootPath}}
	while #stack > 0 do
		local item = table.remove(stack)
		local inst, path = item[1], item[2]
		if #nodes >= MAX_INSTANCES then
			skipped.instances = skipped.instances + 1
			truncated = true
		else
			local kids = inst:GetChildren()
			local node = {p = path, c = inst.ClassName, n = #kids}
			local a = readAttrs(inst)
			if a then node.a = a end
			local t = readTags(inst)
			if t then node.t = t end
			local v = readPos(inst)
			if v then node.v = v end
			if isScript(inst) then readScript(inst, path, node) end
			nodes[#nodes + 1] = node
			local order = {}
			for i, k in ipairs(kids) do order[i] = {k, i, k.Name, k.ClassName} end
			table.sort(order, function(x, y)
				if x[3] ~= y[3] then return x[3] < y[3] end
				if x[4] ~= y[4] then return x[4] < y[4] end
				return x[2] < y[2]
			end)
			local seen, leaves, pending = {}, 0, {}
			for _, o in ipairs(order) do
				local kid = o[1]
				local skipKid = false
				for _, sc in ipairs(SKIP) do
					if kid.ClassName == sc then skipKid = true end
				end
				if not skipKid then
					local grand = kid:GetChildren()
					if isPlainLeaf(kid, grand) then
						leaves = leaves + 1
						if leaves > LEAF_CAP then
							skipped.plain_leaves = skipped.plain_leaves + 1
							skipKid = true
						end
					end
				end
				if not skipKid then
					local nm = esc(o[3])
					seen[nm] = (seen[nm] or 0) + 1
					if seen[nm] > 1 then nm = nm .. "#" .. tostring(seen[nm]) end
					pending[#pending + 1] = {kid, path .. "/" .. nm}
				end
			end
			for i = #pending, 1, -1 do stack[#stack + 1] = pending[i] end
		end
	end
end

local function resolve(segments)
	local cur = game
	for i, seg in ipairs(segments) do
		local nxt = nil
		if i == 1 then
			local ok, svc = pcall(function() return game:GetService(seg) end)
			if ok and svc then nxt = svc end
		end
		if not nxt then nxt = cur:FindFirstChild(seg) end
		if not nxt then return nil end
		cur = nxt
	end
	return cur
end

local rootPaths, missing = {}, {}
for _, segs in ipairs(ROOTS) do
	local parts = {}
	for i, s in ipairs(segs) do parts[i] = esc(s) end
	local rp = table.concat(parts, "/")
	rootPaths[#rootPaths + 1] = rp
	local inst = resolve(segs)
	if inst then
		walk(inst, rp)
	else
		missing[#missing + 1] = rp
	end
end

local out = {
	format = "placemap-collect/1",
	collector_version = @@VERSION@@,
	place = {name = game.Name, place_id = game.PlaceId, game_id = game.GameId},
	taken_unix = os.time(),
	roots = rootPaths,
	full = FULL,
	truncated = truncated,
	skipped = skipped,
	nodes = nodes,
}
if #missing > 0 then out.missing_roots = missing end
local json = game:GetService("HttpService"):JSONEncode(out)
if EMIT_PRINT then
	local n = math.ceil(#json / CHUNK)
	print("PLACEMAP_BEGIN " .. tostring(n))
	for i = 1, n do
		print("PLACEMAP_CHUNK " .. tostring(i) .. " " .. string.sub(json, (i - 1) * CHUNK + 1, i * CHUNK))
	end
	print("PLACEMAP_END")
	return nil
end
return json
'''


def _lua_list(items: list[str]) -> str:
    return "{" + ", ".join(LS.quote_string(i) for i in items) + "}"


def validate_root(root: str) -> list[str]:
    """A refresh root such as ``Workspace/Vendors`` -> its segments (names as they appear in the tree, ``%2F`` for a slash)."""
    segs = [unescape_segment(s) for s in split_path(root)]
    if not segs or len(segs) > 20 or any(not _SEG_OK.fullmatch(s) for s in segs):
        raise ValueError(f"refresh root {root!r} must be a path of 1-20 instance names such as Workspace/Vendors (printable characters, at most 100 per name)")
    return segs


def build_collector(*, place_number: int, place_name: str | None, label: str, limits: dict, roots: list[str], full: bool, known_fps: dict[str, str] | None = None,
                    emit: str = "return", skip_classes: list[str] = (), flt: SecretFilter) -> str:
    if emit not in ("return", "print"):
        raise ValueError("emit must be 'return' or 'print'")
    known = known_fps or {}
    if len(known) > MAX_KNOWN:
        raise ValueError(f"at most {MAX_KNOWN} known fingerprints can be embedded; refresh a smaller root")
    seg_lists = [validate_root(r) for r in roots]
    name = place_name or ""
    safe_label = re.sub(r"[^A-Za-z0-9_/]", "_", label)[:90] or "place"
    if not LS.PLACE_NAME_RE.fullmatch(name):
        if place_number:
            name = "place"  # the guard compares PlaceId first when it is known, so the name is not used
        else:
            raise ValueError("the registered studio_name has characters that cannot be embedded in the place guard and the place has no Roblox place id; "
                             "use letters, digits, spaces and _ . ( ) - in studio_name or set roblox_place_id")
    guard = "\n".join(LS.place_guard(int(place_number), name, safe_label))
    roots_lua = "{" + ", ".join(_lua_list(s) for s in seg_lists) + "}"
    known_lua = "{" + ", ".join(f"[{LS.quote_string(k)}] = {LS.quote_string(v)}" for k, v in sorted(known.items())) + "}"
    toks, exact = luau_key_patterns(flt)
    nums = {k: int(limits[k]) for k in ("max_instances", "max_source_chars", "max_total_source_chars", "plain_leaf_cap", "attr_value_max_chars", "print_chunk_chars")}
    code = TEMPLATE
    for key, val in {"VERSION": str(COLLECTOR_VERSION), "GUARD": guard, "MAX_INSTANCES": str(nums["max_instances"]), "MAX_SOURCE": str(nums["max_source_chars"]),
                     "MAX_TOTAL": str(nums["max_total_source_chars"]), "LEAF_CAP": str(nums["plain_leaf_cap"]), "ATTR_MAX": str(nums["attr_value_max_chars"]),
                     "CHUNK": str(nums["print_chunk_chars"]), "EMIT_PRINT": "true" if emit == "print" else "false", "FULL": "true" if full else "false", "ROOTS": roots_lua,
                     "SKIP": _lua_list(list(skip_classes)), "KNOWN": known_lua, "SECRET_TOKENS": _lua_list(toks), "SECRET_EXACT": _lua_list(exact)}.items():
        code = code.replace(f"@@{key}@@", val)
    if "@@" in code:
        raise ValueError("internal error: an unfilled template placeholder is left in the collector")
    return LS.assert_safe(code)
