# References

| Topic | Link | Used for |
|---|---|---|
| Roblox Creator Documentation | https://create.roblox.com/docs | Engine API, character and level design guidance |
| Engine API source (creator-docs repository) | https://github.com/Roblox/creator-docs/tree/main/content/en-us/reference/engine | Where `roblox_api_snapshot.json` comes from |
| Roblox Studio MCP server | https://create.roblox.com/docs/studio/mcp (source: creator-docs `content/en-us/studio/mcp.md`) | `execute_luau`, `screen_capture`, ... |
| Model Context Protocol - tools | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx | Tool schemas, annotations, structured output |
| Model Context Protocol - prompts | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/prompts.mdx | Possible future: user-selectable workflows |
| Agent Skills specification | https://raw.githubusercontent.com/agentskills/agentskills/main/docs/specification.mdx | `SKILL.md` rules enforced by `core/agentfiles.py` |
| Multimodal embeddings | https://huggingface.co/blog/multimodal-sentence-transformers | Optional CLIP-style search |
| CLIP paper / Transformer paper | https://arxiv.org/abs/2103.00020 , https://arxiv.org/abs/1706.03762 | Background |

## Verification log
| Item | How verified | Date |
|---|---|---|
| Part, SpawnLocation, Model, Folder properties and Material/PartType enums | Parsed from Roblox creator-docs YAML into `roblox_api_snapshot.json` | 2026-10 |
| Studio MCP tool names, `datamodel_type`, `studio_id` | Read Roblox's `studio/mcp.md` | 2026-10 |
| Character size, step height, slope limits, jump height | **Assumptions** in `style.yaml`; verify against your game | - |
| Generated Luau inside real Studio | **Not verified**: mock DataModel only | - |
| Argument names of `execute_luau` / `screen_capture` | **Not documented in the page read**; use the tool schema | - |
