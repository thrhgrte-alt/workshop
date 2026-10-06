# Importing real values

1. `emit_import_luau(project_id, place_id, studios, vendor_path=..., tools_path=..., ores_path=...)` returns a **read-only** script. It reads the child Instances of each named container (Folders, Configurations, ...) and dumps their attributes and Value objects. It refuses unless the open Studio instance is the named place, and the script itself stops in any other place.
2. Run it with Studio MCP's `execute_luau` (Edit datamodel). It prints and returns JSON:

```json
{"format": "econbal-import/1", "truncated": false, "currencies": [{"id": "Coins", "start": 0}],
 "containers": {
   "ores":         {"path": "ReplicatedStorage.Ores",        "items": [{"name": "Copper", "class": "Configuration", "fields": {"Value": 5, "ItemsPerMinute": 4, "Currency": "Coins"}}]},
   "tool_stats":   {"path": "ReplicatedStorage.Tools",       "items": [{"name": "SteelDrill", "class": "Configuration", "fields": {"Tier": 2, "SpeedMult": 1.5, "ValueMult": 1.0}}]},
   "vendor_items": {"path": "ReplicatedStorage.Shop.Items",  "items": [{"name": "Steel Drill", "class": "Configuration", "fields": {"Price": 200, "Tier": 2, "Category": "Drill", "Tool": "SteelDrill"}}]}}}
```
(An empty Lua table encodes as `[]`; the importer accepts that for `fields`.) `currencies` is optional and hand-written.

3. `normalise_import(project_id, place_id, import_json)` maps fields by case-insensitive aliases and returns a DRAFT spec:
   - ores: `value` (value, price, sellvalue, worth) x `rate` (itemsperminute, perminute, rate, spawnrate; missing = assumed 1) = one source `ore_<name>`;
   - vendor items: `price` (price, cost) required; `tier` (tier, level; missing = assigned by ascending price, and said so); `track` (track, category, type, group); `currency`; `ore`; `tool`; `mult` (mult, multiplier, speedmult, valuemult, sellmult, incomemult; all present ones multiplied); `add` (add, bonus, yield, income); `strategy`;
   - tool stats: the same mult/add aliases; a vendor item without its own effect takes its tool's;
   - `field_map={"price": "Gold"}` forces a raw field name.
4. Ids are sanitised and de-duplicated (see `mapping`). Archetypes are ASSUMED defaults marked `assumed: true`; sinks, boosts and rebirths are not imported. **Read every `assumptions` and `unresolved` line to the user**: an upgrade with no known effect imports with none and will be flagged.
