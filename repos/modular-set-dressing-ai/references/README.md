# References

| Topic | Link | Used for |
|---|---|---|
| Roblox Creator Documentation | https://create.roblox.com/docs | Engine API, Studio |
| Engine API source (creator-docs repository) | https://github.com/Roblox/creator-docs/tree/main/content/en-us/reference/engine | Where `roblox_api_snapshot.json` comes from |
| Roblox Studio MCP server | https://create.roblox.com/docs/studio/mcp (source: creator-docs `content/en-us/studio/mcp.md`) | `execute_luau`, `screen_capture`, ... |
| Model Context Protocol - tools | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx | Tool schemas, annotations, structured output |
| Agent Skills specification | https://raw.githubusercontent.com/agentskills/agentskills/main/docs/specification.mdx | `SKILL.md` rules enforced by `core/agentfiles.py` |
| Separating axis theorem | https://en.wikipedia.org/wiki/Hyperplane_separation_theorem | Footprint overlap in `domain/geom.py` |

## Verification log
| Item | How verified | Date |
|---|---|---|
| Part, Model, Folder, CFrame, Vector3 members used by the Luau | Parsed from Roblox creator-docs YAML into `roblox_api_snapshot.json` | 2026-10 |
| Studio MCP tool names, `datamodel_type`, `studio_id` | Read Roblox's `studio/mcp.md` | 2026-10 |
| Yaw convention vs `CFrame.Angles(0, rad(yaw), 0)` | Derived and unit-tested against the mock; **confirm visually in Studio** | - |
| Heights, path height (5), eye height (4.5) | **Assumptions** in `style.yaml`/`scene.py`; verify against your game | - |
| Generated Luau inside real Studio | **Not verified**: mock DataModel only | - |
