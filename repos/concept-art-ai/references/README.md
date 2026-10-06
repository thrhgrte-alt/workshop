# References

| Topic | Link | Used for |
|---|---|---|
| Model Context Protocol - tools | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2026-07-28/server/tools.mdx | Tool schemas, annotations, structured output |
| Agent Skills specification | https://raw.githubusercontent.com/agentskills/agentskills/main/docs/specification.mdx | `SKILL.md` rules enforced by `core/agentfiles.py` |
| LoRA: Low-Rank Adaptation of Large Language Models | https://arxiv.org/abs/2106.09685 | Background for the optional adapter workflow |
| Learning Transferable Visual Models From Natural Language Supervision (CLIP) | https://arxiv.org/abs/2103.00020 | Optional CLIP-style embedding search |
| Multimodal embeddings (Sentence Transformers) | https://huggingface.co/blog/multimodal-sentence-transformers | Optional `st:` embedder |
| Creative Commons licences | https://creativecommons.org/licenses/ | Meaning of `cc0` and `cc-by-4.0` in the LoRA policy |
| Roblox Creator Documentation | https://create.roblox.com/docs | Context for what the concepts are for; nothing here talks to Roblox |

## Verification log
| Item | How verified | Date |
|---|---|---|
| Directions, prompts, adapters (incl. a real subprocess), run records, measurements, rubric, curation gate, LoRA validation | Exercised by `tests/` and `evals/` | 2026-10 |
| Palette/value/silhouette/novelty measurements | Checked on constructed images with hand-computable answers | 2026-10 |
| Any hosted image-generation API | **Not integrated and not verified.** Use the `command` adapter | - |
| Output quality of any real generator | **Not tested** | - |
| LoRA training | **Not performed.** Hyperparameters in generated configs are generic and unverified | - |
| CLIP-style embeddings | Optional; not run here | - |
| Thresholds in `style.yaml` | **Heuristics**; tune on your own concepts | - |
