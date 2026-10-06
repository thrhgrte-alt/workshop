# References

Official documentation to consult (not copied here). Verify current versions before relying on a detail.

| Topic | Link | Used for |
|---|---|---|
| Substance 3D Designer scripting | https://experienceleague.adobe.com/en/docs/substance-3d-designer/using/scripting/scripting | Python API shape (`sd`, `SDGraph.newNode`, connections, values) |
| Scripting API reference | https://experienceleague.adobe.com/en/docs/substance-3d-designer/using/scripting/scripting-api-reference | Class and method names for `scriptgen.py` |
| Substance 3D Automation Toolkit (pysbs, sbscooker, sbsrender) | https://helpx.adobe.com/substance-3d-sat/pysbs-python-api.chromeless.html | CLI used by `render_preview` |
| Model Context Protocol - tools | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx | Tool schemas, annotations, structured output, error handling |
| Model Context Protocol - prompts | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/prompts.mdx | Possible future: reusable user-selectable workflows |
| Agent Skills specification | https://raw.githubusercontent.com/agentskills/agentskills/main/docs/specification.mdx | `SKILL.md` frontmatter rules enforced by `core/agentfiles.py` |
| Multimodal embeddings (Sentence Transformers) | https://huggingface.co/blog/multimodal-sentence-transformers | Optional CLIP-style image/text search |
| CLIP paper | https://arxiv.org/abs/2103.00020 | Background on text/image representations (not a style-learning system) |
| Transformer paper | https://arxiv.org/abs/1706.03762 | Why context content, not memory, decides model behaviour |
| Hugging Face LLM course | https://huggingface.co/learn/llm-course/ | Background |

## Verification log

| Item | How verified | Date |
|---|---|---|
| Agent Skills frontmatter limits (name 64, description 1024, compatibility 500) | Read the specification text | 2026-10 |
| MCP tool annotations and `structuredContent` | Read the 2026-07-28 tools specification; exercised with the SDK in tests | 2026-10 |
| Designer `newNode('sbs::compositing::...')` general API shape | Adobe docs search results only; **no installation available** | 2026-10 |
| Node ids, parameter ids, library graph labels | **Not verified** - draft catalog; run the probe | - |
| `sbscooker` / `sbsrender` flags | **Not verified** | - |
