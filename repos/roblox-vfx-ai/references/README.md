# References

Official documentation to consult (not copied here). Verify current versions before relying on a detail.

| Topic | Link | Used for |
|---|---|---|
| Roblox Creator Documentation | https://create.roblox.com/docs | Engine API, effects guidance, performance |
| Roblox engine API source (creator-docs repository) | https://github.com/Roblox/creator-docs/tree/main/content/en-us/reference/engine | Where `roblox_api_snapshot.json` comes from |
| Connect to the Roblox Studio MCP server | https://create.roblox.com/docs/studio/mcp (source: creator-docs `content/en-us/studio/mcp.md`) | Studio's built-in tools: `execute_luau`, `screen_capture`, ... |
| Model Context Protocol - tools | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx | Tool schemas, annotations, structured output |
| Model Context Protocol - prompts | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/prompts.mdx | Possible future: user-selectable workflows |
| Agent Skills specification | https://raw.githubusercontent.com/agentskills/agentskills/main/docs/specification.mdx | `SKILL.md` rules enforced by `core/agentfiles.py` |
| Multimodal embeddings | https://huggingface.co/blog/multimodal-sentence-transformers | Optional CLIP-style search |
| CLIP paper | https://arxiv.org/abs/2103.00020 | Background |
| Transformer paper | https://arxiv.org/abs/1706.03762 | Why context content decides behaviour |

## Verification log

| Item | How verified | Date |
|---|---|---|
| ParticleEmitter, Beam, Trail, Attachment, Light/PointLight property names and types; enum members | Parsed from Roblox's creator-docs YAML into `roblox_api_snapshot.json` | 2026-10 |
| Studio MCP tool names, `datamodel_type`, `studio_id`, screen_capture camera option | Read Roblox's `studio/mcp.md` | 2026-10 |
| Property *defaults* | **Not in the snapshot**; the analysis lists assumed defaults | - |
| Exact `execute_luau` / `screen_capture` argument names | **Not documented in the page read**; take them from the tool schema | - |
| Luau runs inside real Studio | **Not verified**: only a mock DataModel was available | - |
| Budget thresholds | **Heuristic defaults**, not Roblox limits | - |
