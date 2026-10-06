# Roblox Studio's built-in MCP server

Documented by Roblox ("Connect to the Roblox Studio MCP server"): Studio exposes an MCP server over stdio. Enable it
in Studio under **Assistant > ... > Manage MCP Servers > Enable Studio as MCP server**, then connect your client
(quick connect lists Claude Code, Codex CLI, Gemini CLI, Cursor, VS Code and others). Every tool takes a `studio_id`;
call `list_roblox_studios` first when more than one Studio window is open.

Tools you will use with this repository (names from Roblox's documentation):

| Tool | Use |
|---|---|
| `list_roblox_studios` | find the `studio_id` |
| `execute_luau` | run generated Luau; requires `datamodel_type` (`Edit` for building effects) |
| `inspect_instance`, `search_game_tree` | explore what exists (alternative to `inspect_vfx`) |
| `screen_capture` | viewport capture; optional custom camera position and look-at target |
| `start_stop_play`, `get_console_output` | playtest and read errors |

This repository's server never calls these. The agent does, with code this repository generated. The exact argument
name that carries the Luau, and the argument names for the camera, come from the tool schemas Studio reports; read
them instead of guessing. Roblox's own server is separate from this one: configure both in your client.

Safety: Studio's MCP lets a connected client read and modify the open place. Only connect clients you trust, work on a
copy of the place when experimenting, and keep generated effects inside an agreed Folder (`target_path`).
