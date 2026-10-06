# Positive examples (clean, and false-positive traps)

A correct review reports NOTHING for these files (zero defects and zero questions). Most are traps: code that looks like a bug to a naive text search. All code is synthetic.

| File | Why it must stay clean | Mode |
|---|---|---|
| `trap_strings_and_comments.server.luau` | TRAP: every bug-looking snippet here is inside a comment, a string, a long string, a long comment or an interpolated string. Nothing here is code. | default |
| `trap_pcall_datastore.server.luau` | TRAP: the full, correct DataStore pattern. Every call is in a retrying pcall helper, saves use UpdateAsync, there is a session owner, per-player cleanup and BindToClose. | default |
| `trap_validated_remote.server.luau` | TRAP: a remote handler that validates type and range, looks the price up on the server and has a per-player cooldown that is cleaned up. | default |
| `trap_task_library.server.luau` | TRAP: only the task library, plus names that merely contain `wait`, `spawn` or `delay` (config fields, a local function `spawnEnemy`, `signal:Wait()`). | default |
| `trap_loops_that_yield.server.luau` | TRAP: `while true do` loops that yield with task.wait or leave with `break`, and a repeat/until that yields. None freezes the script. | default |
| `trap_heartbeat_cached.client.luau` | TRAP: a Heartbeat handler that does no lookups (the list is built once outside), and a connection that is stored and disconnected. | default |
| `trap_datastore_lookalike.luau` | TRAP: a plain in-memory Cache class with methods named GetAsync/SetAsync. There is no DataStore anywhere in the file. | default |
| `trap_receipt_correct.server.luau` | TRAP: ProcessReceipt returns the right decisions from the right function. Returns inside the nested UpdateAsync closure are not the handler's returns. | default |
| `trap_typed_module.luau` | TRAP: a fully typed module. Clean in --strict mode as well. | --strict |
| `trap_client_apis_on_client.client.luau` | TRAP: client-only APIs (LocalPlayer, UserInputService, FireServer, OnClientEvent) in a client script, and the camera read inside a function. | default |
| `trap_server_apis_on_server.server.luau` | TRAP: server-only APIs (ServerStorage, FireClient, FireAllClients) in a server script. | default |
| `trap_named_constants.luau` | TRAP: tunable numbers are named constants. Clean in --strict mode (no magic numbers). | --strict |
| `trap_connections_tracked.server.luau` | TRAP: a Heartbeat connection per player, but each one is stored, disconnected and cleared when the player leaves. | default |
| `trap_per_player_cleanup.server.luau` | TRAP: per-player instances and a per-player table, both cleaned up on PlayerRemoving. | default |
| `trap_if_expressions.luau` | TRAP: Luau if-expressions (no `end`), nested functions, repeat/until and generic for, to prove block matching is not confused. | default |
| `trap_touched_debounced.server.luau` | TRAP: a Touched handler with a debounce table that resets after a cooldown. | default |
| `trap_remote_helper_validation.server.luau` | TRAP: validation done by a helper that is called in a guard (`if not isValidTarget(target)`), plus a cooldown. | default |
| `trap_lookups_outside_frame_code.server.luau` | TRAP: GetChildren/GetDescendants/FindFirstChild in a slow loop and a one-off setup, not in a per-frame handler. | default |
| `trap_similar_names.server.luau` | TRAP: names that look like flagged APIs but are not: `loadstringEnabled`, `LoadStringEnabled`, `require` of paths, `string.format`, a `tickRate` variable. | default |
| `trap_pcall_results_used.server.luau` | TRAP: every pcall result is used (assigned, tested, returned), and the HTTP call is protected. | default |
| `modules/` | TRAP: modules that require each other in a chain (Main -> Util -> Config, Main -> Config) with no cycle. A folder review must stay clean, in --strict mode too. | --strict |
