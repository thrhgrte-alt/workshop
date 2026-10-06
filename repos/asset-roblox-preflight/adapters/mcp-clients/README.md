# MCP client configuration examples

Replace `/ABSOLUTE/PATH/TO/asset-roblox-preflight` with this checkout, and `python` with the interpreter of the
environment where you ran `pip install -e .` (a full path is safest, e.g. `.venv/bin/python`).

| Client | File | Where it goes |
|---|---|---|
| Claude Code | `claude-code.mcp.json` | project `.mcp.json`, or `claude mcp add asset-roblox-preflight -e PREFLIGHT_ROOT=/ABS/PATH -- python -m preflight.server` |
| Claude Desktop | `claude-desktop.json` | merge into `claude_desktop_config.json` |
| Cursor | `cursor.mcp.json` | `.cursor/mcp.json` |
| Gemini CLI | `gemini-settings.json` | merge `mcpServers` into Gemini's `settings.json` |
| VS Code / GitHub Copilot | `vscode.mcp.json` | `.vscode/mcp.json` |
| Codex CLI | `codex.config.toml` | merge into `~/.codex/config.toml` |

These follow each client's documented configuration shape but were **not** tested against live
clients while writing this repository. If a client rejects a file, check its current MCP docs: the
server itself is a standard stdio MCP server (`python -m preflight.server`).

The server speaks stdio and runs **locally**, as your user, with access to the directories in
`PREFLIGHT_ALLOWED_PATHS` (default: `workspace/`). Do not expose it on a network: it has no authentication
(`--http` binds 127.0.0.1 only and refuses anything else).

Set `PREFLIGHT_DISABLE_RARE=1` in the server's `env` to leave out the `[rare]` tools (apply_safe_fix, save_report, compare_versions, suggest_profile_promotions, record_override, promote_run, search_library)
and save context. Set `PREFLIGHT_ASSET_ROOT` to the folder holding your exports so the server may read them.
