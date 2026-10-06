# projects.yaml and playtest.yaml

`projects.yaml` (shared guide-core format): `project_id`, `alias`, `universe_id`, `places: [{place_id, alias, studio_name, roblox_place_id, profiles: {playtest: <path>}}]`. `roblox_place_id` 0 or absent means unpublished: the Studio place NAME is matched instead.
The open place must match by Roblox place id (when the registry has one) or by the exact normalised `studio_name`.

`playtest.yaml` (one per place; all sections optional, a check exists only when its section does):

```yaml
version: 1
flaky: [remotes]            # extra flaky checks for this place        not_flaky: [perf_snapshot]
overrides: {settings: {boot_window_seconds: 15.0}, ranges: {perf_memory_increase_pct: {max: 30.0}}}   # explicit numbers win over learned parameters
log_ignore: ["regex"]       # lines left out of the counts (always reported as ignored)
boot: {expected_services: [Workspace], expected_paths: [Workspace.Map], use_log_service: true}
spawn: {spawn_path: Workspace.Map.Spawn, invalid_surface_names: [Lava], invalid_surface_attribute: Hazard}
reachability: {spawn_path: Workspace.Map.Spawn, hops: false, areas: [{name: Shop, path: Workspace.Map.Shop}]}
remotes: {items: [{id: BuyUpgrade, path: ReplicatedStorage.Remotes.BuyUpgrade, class: RemoteFunction, valid_args: [["PickaxeII"]], bad_inputs: [{label: unknown_item, args: ["NoSuchItem"]}]}]}
economy: {player_values: {coins: {path: leaderstats.Coins, currency: true}}, steps: [{id: sell, action: {kind: bindable, path: ServerScriptService.QA.Sell}, expect: {coins: {delta: 15}}}]}
data: {test_mode: {switch: ReplicatedStorage.QA.TestMode, ack_hook: ServerScriptService.QA.DataIsTestMode}, save_hook: ..., load_hook: ..., values: {coins: {path: leaderstats.Coins, marker: 777}}}
performance: {minutes: 5, baseline: default}
```

Paths are dotted names (letters, digits, underscore, starting with a letter, 40 characters per part): anything else is refused because it would end up inside generated Luau. A hook (Bindable function or remote) named here is something YOUR game provides for QA; this tool cannot create it.
