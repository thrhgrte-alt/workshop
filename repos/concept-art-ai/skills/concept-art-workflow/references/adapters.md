# Image adapters

| Adapter | Writes files | Use |
|---|---|---|
| `dryrun` | no | The default. Shows what would be sent. |
| `placeholder` | yes | Draws a labelled synthetic image from the direction palette. Pipeline fixture: for tests and demos only. Never promoted to the library. |
| `command` | yes | Runs the program in `CONCEPTAI_IMAGE_COMMAND` (JSON argv list or shell-style string, split without a shell). |

`command` tokens: `{prompt_file}` `{negative_file}` `{out}` `{seed}` `{width}` `{height}` `{index}` `{reference_dir}` (a folder with copies of the reference images the run was given).
The program must exit 0 and write a regular image file at `{out}` (a link elsewhere is refused). Timeout: `CONCEPTAI_IMAGE_TIMEOUT` seconds (default 600). Declared model: `CONCEPTAI_IMAGE_MODEL`.

Example wrapper skeleton (`my_generate.py`) - fill in the call to your own generator; this repository does not know any provider API:
```python
import argparse, pathlib
ap = argparse.ArgumentParser()
for a in ("prompt-file", "negative-file", "out", "seed", "width", "height"):
    ap.add_argument(f"--{a}")
args = ap.parse_args()
prompt = pathlib.Path(args.prompt_file).read_text()
negative = pathlib.Path(args.negative_file).read_text()
# image = your_generator(prompt, negative, int(args.seed), int(args.width), int(args.height))   # <- yours
# image.save(args.out)
```
To add a Python adapter instead, subclass `ImageAdapter` in `conceptai/domain/adapters.py`, implement `available()` and `generate()`, and add it to `registry()`.
