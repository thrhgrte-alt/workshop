# Roblox Studio's MCP server

Read from Roblox's documentation (`studio/mcp.md`) when this repo was written: Studio can run an MCP server exposing, among others, `list_roblox_studios`, `execute_luau`
(with `datamodel_type` and `studio_id`), `screen_capture`, `get_console_output` and `start_stop_play`. **Read each tool's schema in your client for the exact argument names; nothing here is a
substitute.** This repo never connects to Studio: generate Luau with `export_scene`, then forward it with your agent.

- Use `datamodel_type: "Edit"`. Work on a copy of the place. Use `target_path` to pick the parent Folder.
- A script is idempotent: it recreates only `AI_SetDress_<id>` folders marked `AIGeneratedBy = "setdress"`.
- `parse_studio_report` turns what came back into `ok`/`errors`. Quote errors verbatim.
- Clone mode needs your kit models at `template_root` (default `ReplicatedStorage.Kit`) named by each module's `template`; it is unverified without them.
