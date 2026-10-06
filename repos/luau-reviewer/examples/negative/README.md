# Negative examples (a bug is planted in each)

Each file has one planted problem. The eval suite requires the review to report exactly the listed rule ids (as defects) and the listed question ids (low confidence). Rows marked KNOWN GAP are planted bugs the heuristic checker does NOT catch: they are counted against recall on purpose. All code is synthetic.

| File | What is wrong | Defect rules | Questions | Mode |
|---|---|---|---|---|
| `sec001_unvalidated_remote.server.luau` | The EquipItem handler uses `itemName` and `slot` straight from the client: no typeof, no range check, no allow-list. | SEC001 | - | default |
| `sec001_unvalidated_remote_function.server.luau` | A RemoteFunction returns `profiles[targetId]` for whatever the client asks for: the argument is never checked. | SEC001 | - | default |
| `sec002_client_price.server.luau` | `price` arrives from the client and is charged as given: an exploiter sends price = 0 (or negative) and gets items free. | SEC002 | - | default |
| `sec002_client_damage.server.luau` | The client decides how much `damage` a hit does; the server just applies it with TakeDamage. | SEC002 | - | default |
| `sec003_no_cooldown.server.luau` | A well validated handler with no cooldown or permission check: anyone can fire it as fast as they like. Raised as a question because the check may live elsewhere. | - | SEC003 | default |
| `sec004_loadstring.server.luau` | Source fetched over HTTP is compiled and run with `loadstring`: remote code execution if the URL or response is ever compromised. | SEC004 | - | default |
| `sec005_require_asset_id.server.luau` | `require(<number>)` pulls a ModuleScript from the catalog by asset id; its owner can change it under you. | SEC005 | - | default |
| `sec006_invokeclient.server.luau` | The server calls `InvokeClient`: a client that never answers leaves the server thread waiting forever. | SEC006 | - | default |
| `dat001_unprotected_getasync.server.luau` | `GetAsync` runs without pcall: a throttle or outage raises an error and the player's data never loads. | DAT001 | - | default |
| `dat001_unprotected_updateasync.server.luau` | The save in PlayerRemoving calls `UpdateAsync` with no pcall. | DAT001 | - | default |
| `dat002_no_retry.server.luau` | The load is in a pcall, but a single failure gives up: one hiccup leaves the player with an empty profile. | DAT002 | - | default |
| `dat003_setasync.server.luau` | Read with GetAsync, modify, write back with SetAsync: two servers (or a quick rejoin) overwrite each other. UpdateAsync is the atomic way. | DAT003 | - | default |
| `dat004_no_bindtoclose.server.luau` | Data is saved on PlayerRemoving only: when the server shuts down, players still inside may not be saved. | DAT004 | - | default |
| `dat005_save_every_few_seconds.server.luau` | A loop saves every player every 5 seconds: it burns the DataStore request budget and causes throttling. | DAT005 | - | default |
| `dat005_save_every_frame.server.luau` | The DataStore write sits inside a Heartbeat handler: dozens of requests per second per player. | DAT005 | - | default |
| `dat006_no_session_lock.server.luau` | Profiles are loaded and saved with no lock of any kind, so a fast rejoin to another server can overwrite progress. Raised as a question: locking may live in a module. | - | DAT006 | default |
| `dat007_receipt_returns_bool.server.luau` | ProcessReceipt returns `true`/`false` instead of an Enum.ProductPurchaseDecision, so Roblox never gets a valid answer. | DAT007 | - | default |
| `dat008_receipt_always_granted.server.luau` | The handler can only answer PurchaseGranted, even if the grant failed or the player left. Raised as a question. | - | DAT008 | default |
| `dat009_key_from_player_name.server.luau` | The data key is `player.Name`: a username change orphans the data (or hands it to whoever takes the old name). | DAT009 | - | default |
| `perf001_busy_loop.server.luau` | `while true do` with no yield: the script freezes until it times out. | PERF001 | - | default |
| `perf002_while_true_wait.server.luau` | A polling loop built on the legacy global `wait` (reported once as PERF002, not again as API001). | PERF002 | - | default |
| `perf003_getdescendants_heartbeat.client.luau` | `workspace:GetDescendants()` is called inside a Heartbeat handler: a full tree walk 60 times a second. | PERF003 | - | default |
| `perf003_findfirstchild_renderstepped.client.luau` | `FindFirstChild` in a RenderStepped handler searches every frame; the result should be cached. | PERF003 | - | default |
| `perf004_instance_new_heartbeat.server.luau` | A new Part is created on every Heartbeat. | PERF004 | - | default |
| `perf005_touched_no_debounce.server.luau` | A Touched handler that heals on every touch event with no debounce. Raised as a question: a guard may exist elsewhere. | - | PERF005 | default |
| `leak001_heartbeat_per_player.server.luau` | Every joining player adds a Heartbeat connection that is never stored or disconnected. | LEAK001 | - | default |
| `leak002_unbounded_log.server.luau` | `samples` gets a new number every frame and is never trimmed. | LEAK002 | - | default |
| `leak003_player_table.server.luau` | `sessions[player]` keeps every Player that ever joined alive: nothing clears it on PlayerRemoving. | LEAK003 | - | default |
| `leak004_player_instances.server.luau` | A marker Part is created for each player and parented to the workspace; nothing destroys it when they leave. Raised as a question. | - | LEAK004 | default |
| `api001_wait.server.luau` | The legacy global `wait`. | API001 | - | default |
| `api002_spawn.server.luau` | The legacy global `spawn`. | API002 | - | default |
| `api003_delay.server.luau` | The legacy global `delay`. | API003 | - | default |
| `api004_instance_new_parent.server.luau` | `Instance.new("Part", workspace)` parents the Part before its properties are set. | API004 | - | default |
| `api005_humanoid_loadanimation.server.luau` | `Humanoid:LoadAnimation` is deprecated; the Animator loads animations. | API005 | - | default |
| `api006_camera_cached.client.luau` | `workspace.CurrentCamera` is cached once at the top of the script. Raised as a question. | - | API006 | default |
| `api007_magic_numbers.server.luau` | Unexplained numbers in comparisons. Only reported in --strict mode, and only as a question. | - | API007 | --strict |
| `api008_lowercase_connect.server.luau` | The lower-case `connect` alias is deprecated. | API008 | - | default |
| `api009_tick.server.luau` | `tick()` is deprecated; elapsed time should use os.clock(). | API009 | - | default |
| `api010_table_getn.server.luau` | `table.getn` is a removed Lua 5.0 function; use `#`. | API010 | - | default |
| `api011_body_mover.server.luau` | BodyVelocity is a deprecated mover; a LinearVelocity constraint replaces it. | API011 | - | default |
| `str001_missing_strict.server.luau` | No `--!strict` header. | STR001 | - | default |
| `str002_untyped_public.luau` | Public functions `M.add` has untyped parameters and no return type. Only reported in --strict mode. | STR002 | - | --strict |
| `cycle/` | Modules A and B require each other. Found by reviewing the folder. | STR003 | - | default |
| `str004_server_api_in_client.client.luau` | A client script reaches into ServerStorage, which does not replicate to clients. | STR004 | - | default |
| `str005_client_api_in_server.server.luau` | A server script reads `Players.LocalPlayer`, which is nil on the server. | STR005 | - | default |
| `cor001_ignored_pcall.server.luau` | `pcall` is called as a bare statement: whatever fails is swallowed without a trace. | COR001 | - | default |
| `gap_varargs_remote.server.luau` | KNOWN GAP. The handler takes `...` and reads `{...}` unvalidated. The checker looks at named parameters, so it cannot see this. | SEC001 (not caught) | - | default |
| `gap_aliased_getasync.server.luau` | KNOWN GAP. `GetAsync` is called through an alias (`local get = store.GetAsync`), not with `:`, and without pcall. Only `obj:Method(` calls are recognised. | DAT001 (not caught) | - | default |
| `gap_require_variable_id.server.luau` | KNOWN GAP. The asset id is held in a constant and `require(MODULE_ID)` is called with the name. There is no constant propagation. | SEC005 (not caught) | - | default |

Backends (`luau-analyze`, `selene`, `stylua`) are not part of these expectations: the evals run the own-rule engine only, so they are deterministic on machines with or without those tools.

