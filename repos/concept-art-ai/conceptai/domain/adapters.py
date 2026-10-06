"""Replaceable image-generation adapters.

An adapter takes one request (prompt, negative prompt, size, seed, count, optional reference images, free-form settings) and returns
files. Three ship with the repo, none of which call a hosted provider:

* ``dryrun``      writes nothing; shows exactly what *would* be sent (the default everywhere).
* ``placeholder`` draws a labelled synthetic image from the direction's palette (Pillow). A pipeline fixture, NOT generated art.
* ``command``     runs a program YOU configure (``CONCEPTAI_IMAGE_COMMAND``, a JSON argv list) with no shell; use it to wrap any local
                  or hosted generator. The program must write the file named by ``{out}``.

No provider API is hard-coded because none could be verified here. To add a provider, subclass ``ImageAdapter`` (or write a small wrapper
script for the ``command`` adapter) and register it. The model name an adapter reports is whatever you declare; it is recorded, not verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from . import synthetic

ALLOWED_OUTPUT = {".png", ".jpg", ".jpeg", ".webp"}
COMMAND_TOKENS = ("{prompt_file}", "{negative_file}", "{out}", "{seed}", "{width}", "{height}", "{index}", "{reference_dir}")
MAX_SIDE = 4096
MAX_COUNT = 8


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_request(req: dict) -> dict:
    r = {"count": 1, "seed": 0, "reference_images": [], "settings": {}, "negative": "", **req}
    if not str(r.get("prompt", "")).strip():
        raise ValueError("request needs a prompt")
    for k in ("width", "height"):
        if not isinstance(r.get(k), int) or not 64 <= r[k] <= MAX_SIDE:
            raise ValueError(f"{k} must be an integer between 64 and {MAX_SIDE}")
    if not isinstance(r["count"], int) or not 1 <= r["count"] <= MAX_COUNT:
        raise ValueError(f"count must be between 1 and {MAX_COUNT}")
    if not isinstance(r["seed"], int):
        raise ValueError("seed must be an integer")
    return r


class ImageAdapter:
    name = "base"
    model = "unspecified"
    writes_files = True

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    def describe(self) -> dict:
        ok, why = self.available()
        return {"name": self.name, "model": self.model, "available": ok, "detail": why, "writes_files": self.writes_files}

    def generate(self, req: dict, out_dir: Path) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError


class DryRunAdapter(ImageAdapter):
    name, model, writes_files = "dryrun", "none", False

    def generate(self, req: dict, out_dir: Path) -> list[dict]:
        check_request(req)
        return []


class PlaceholderAdapter(ImageAdapter):
    name, model = "placeholder", "placeholder-renderer (synthetic, not a model)"

    def available(self):
        return (synthetic.Image is not None, "needs Pillow" if synthetic.Image is None else "ok")

    def generate(self, req: dict, out_dir: Path) -> list[dict]:
        r = check_request(req)
        direction = r["settings"].get("direction")
        if not direction:
            raise ValueError("the placeholder adapter needs settings.direction (a direction dict) to take its palette from")
        out = []
        for i in range(r["count"]):
            path = Path(out_dir) / f"{r.get('stem', 'image')}_{i:02d}.png"
            synthetic.save(synthetic.render(direction, r["width"], r["height"], r["seed"] + i), path)
            out.append({"path": str(path), "seed": r["seed"] + i, "sha256": sha256_file(path)})
        return out


class CommandAdapter(ImageAdapter):
    """Runs a user-configured program. ``argv`` is a list containing the tokens in COMMAND_TOKENS; no shell is used."""

    name = "command"
    writes_files = True

    def __init__(self, argv: list[str] | None = None, model: str | None = None, timeout: int = 600):
        self.argv, self.model, self.timeout = argv, model or "declared-by-user (unverified)", timeout

    @classmethod
    def from_env(cls) -> "CommandAdapter":
        raw = os.environ.get("CONCEPTAI_IMAGE_COMMAND")
        argv = None
        if raw:
            try:
                argv = json.loads(raw)
            except json.JSONDecodeError:
                argv = shlex.split(raw)
        return cls(argv, os.environ.get("CONCEPTAI_IMAGE_MODEL"), int(os.environ.get("CONCEPTAI_IMAGE_TIMEOUT", "600")))

    def available(self):
        if not self.argv:
            return False, "not configured: set CONCEPTAI_IMAGE_COMMAND to a JSON argv list using {prompt_file} {out} {seed} {width} {height}"
        if not any("{out}" in a for a in self.argv):
            return False, "the command must contain {out} (the file it should write)"
        return True, "configured (not verified until run)"

    def generate(self, req: dict, out_dir: Path) -> list[dict]:
        r = check_request(req)
        ok, why = self.available()
        if not ok:
            raise ValueError(f"command adapter {why}")
        out_dir = Path(out_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        with tempfile.TemporaryDirectory() as td:
            pf, nf, rd = Path(td) / "prompt.txt", Path(td) / "negative.txt", Path(td) / "refs"
            pf.write_text(r["prompt"], encoding="utf-8")
            nf.write_text(r["negative"], encoding="utf-8")
            rd.mkdir()
            for k, ref in enumerate(r["reference_images"]):
                (rd / f"ref_{k:02d}{Path(ref).suffix}").write_bytes(Path(ref).read_bytes())
            for i in range(r["count"]):
                out = out_dir / f"{r.get('stem', 'image')}_{i:02d}.png"
                subs = {"{prompt_file}": str(pf), "{negative_file}": str(nf), "{out}": str(out), "{seed}": str(r["seed"] + i), "{width}": str(r["width"]),
                        "{height}": str(r["height"]), "{index}": str(i), "{reference_dir}": str(rd)}
                argv = []
                for a in self.argv:
                    for k, v in subs.items():
                        a = a.replace(k, v)
                    argv.append(a)
                try:
                    proc = subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=self.timeout, cwd=td)
                except FileNotFoundError:
                    raise ValueError(f"command not found: {argv[0]!r}")
                except subprocess.TimeoutExpired:
                    raise ValueError(f"image command timed out after {self.timeout}s")
                if proc.returncode != 0:
                    raise ValueError(f"image command failed (exit {proc.returncode}): {(proc.stderr or proc.stdout).strip()[:300]}")
                if not out.exists():
                    raise ValueError(f"image command did not write {out.name}; it must write the {{out}} path")
                if out.is_symlink() or out.resolve().parent != out_dir:
                    raise ValueError("image command wrote an unexpected file (the output must be a regular file inside the output folder, not a link)")
                results.append({"path": str(out), "seed": r["seed"] + i, "sha256": sha256_file(out)})
        return results


def registry() -> dict[str, ImageAdapter]:
    return {a.name: a for a in (DryRunAdapter(), PlaceholderAdapter(), CommandAdapter.from_env())}


def get(name: str) -> ImageAdapter:
    reg = registry()
    if name not in reg:
        raise ValueError(f"unknown adapter '{name}'. Available: {sorted(reg)}")
    return reg[name]
