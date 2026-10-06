# Backends

| Tool | Invoked as | What it adds | Output understood |
|---|---|---|---|
| `luau-analyze` | `luau-analyze <files>` | syntax and type errors, Luau lints | `path(line,col): Kind: message` |
| `selene` | `selene --display-style=json2 <files>` | lints | one JSON diagnostic per line, or the one-line quiet style |
| `stylua` | `stylua --check <files>` | format differences | `Diff in <file> at line N:` |

- Found with `shutil.which`; run without a shell, with a timeout (20 s per batch, `LUAUREV_BACKEND_TIMEOUT`), no stdin, output size capped.
- Statuses: `ran`, `missing`, `error` (could not start or crashed), `timeout`, `unparsed` (non-zero exit but output not understood), `not_requested`. None of the failures is reported as "clean".
- `backends=[]` runs only the own rules; a subset such as `["selene"]` runs just those.
- The output formats above come from the author's knowledge of the tools and were checked only against stub programs in this repository's tests. Treat a parse problem as a bug in the wrapper and report it.
- selene may need its own configuration (`selene.toml`, a Roblox standard library); stylua reads `stylua.toml`. They are run from the reviewed folder so they find it.
