# Roblox Studio's built-in MCP server

Documented by Roblox ("Connect to the Roblox Studio MCP server"). Enable it in Studio under **Assistant > ... > Manage MCP Servers**, then connect your client
alongside this repository's server. Every tool takes a `studio_id` (see `list_roblox_studios`). Used here: `execute_luau` (requires `datamodel_type`; `Edit` to build),
`screen_capture` (optional custom camera position and look-at target), `get_console_output`, `start_stop_play`, `inspect_instance`, `search_game_tree`.

This repository only generates and validates code; the agent forwards it. Read each tool's input schema for the exact argument names. Studio's MCP can read and modify
the open place: work on a copy, build into an agreed Folder (`target_path`), and keep generated blockouts inside it.
