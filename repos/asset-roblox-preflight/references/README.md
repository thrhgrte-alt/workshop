# References and verification log

**No external documentation was consulted while building this repository** (no web access was used). Everything below that is not "tested locally" is an assumption to verify.

## Sources actually read

| Source | Used for |
|---|---|
| The task brief (`roblox-repo-build-instructions.md`, repo 1 and the shared rules) | Scope, tool names, checks, output format, the spin setup |
| The sibling repositories in this workshop (`concept-art-ai`, `modular-set-dressing-ai`) and the shared kit (then vendored in `preflight/core`, now the installed `guide-core` library) | The repository pattern, shared CLI/MCP/eval machinery |

## Formats implemented from memory, not from a re-read of the specification

glTF 2.0 / GLB (chunk layout, accessor component types, node TRS, skins, animations), Wavefront OBJ/MTL, PNG/JPEG/BMP/TGA headers, the binary FBX magic string and version field.
They are exercised only by files this repository writes itself, so a reader bug that mirrors a writer bug would not be caught. **Test with real exports before trusting it.**

## Verification log

| Item | How verified | Date |
|---|---|---|
| GLB/glTF/OBJ reading, measurements (volume, triangle counts, UV ratios, edge counts) | Unit and eval tests with hand-computed values on synthetic files | 2026-10 |
| Rules, profiles, project layering, overrides, isolation | Tests and evals | 2026-10 |
| Safe-fix script | Executed against a fake `bpy` (`domain/bpystub.py`), linted | 2026-10 |
| MCP server | In-memory session and a real stdio subprocess | 2026-10 |
| **Every numeric limit** (triangles, texture size, bones, file size, formats, fps, uv sets) | **Not verified.** DEFAULTS chosen by the author; `verify_against_current_docs: true` | - |
| 1 export unit = 1 stud (`studs_per_unit`) | **Assumption** | - |
| Roblox accepts FBX/GLB/glTF/OBJ and the texture formats in `TEX_FORMAT` | **Assumption** | - |
| Real Blender exports, `blender_get_objects_summary` / `asset_validate` shapes, `roblox_upload_plan` | **Not available**; `preflight-summary/1` is our own schema | - |
| Generated Blender script in real Blender | **Never run** | - |
| MCP client configuration files | Follow each client's documented shape as remembered; not tested against live clients | - |
