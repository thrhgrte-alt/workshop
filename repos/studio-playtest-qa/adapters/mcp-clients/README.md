# MCP client configuration examples

Replace `/ABSOLUTE/PATH/TO/studio-playtest-qa` with this checkout, and `python` with the interpreter of the
environment where you ran `pip install -e .` (a full path is safest, e.g. `.venv/bin/python`).

| Client | File | Where it goes |
|---|---|---|
| Claude Code | `claude-code.mcp.json` | project `.mcp.json`, or `claude mcp add studio-playtest-qa -e PLAYQA_ROOT=/ABS/PATH -- python -m playqa.server` |
| Claude Desktop | `claude-desktop.json` | merge into `claude_desktop_config.json` |
| Cursor | `cursor.mcp.json` | `.cursor/mcp.json` |
| Gemini CLI | `gemini-settings.json` | merge `mcpServers` into Gemini's `settings.json` |
| VS Code / GitHub Copilot | `vscode.mcp.json` | `.vscode/mcp.json` |
| Codex CLI | `codex.config.toml` | merge into `~/.codex/config.toml` |

These follow each client's documented configuration shape but were **not** tested against live
clients while writing this repository. If a client rejects a file, check its current MCP docs: the
server itself is a standard stdio MCP server (`python -m playqa.server`).

The server speaks stdio and runs **locally**, as your user, with access to the directories in
`PLAYQA_ALLOWED_PATHS` (default: `workspace/`). Do not expose it on a network: it has no authentication
(`--http` binds 127.0.0.1 only and refuses anything else).
