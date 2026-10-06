#!/usr/bin/env python3
"""Regenerate samples/synthetic/*: hand-written stand-ins for what the hub's screen_capture result MIGHT look like. They are SYNTHETIC and labelled so: they were
not captured from anything. Use --check to verify the tracked files are current (a test does)."""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visualverify import evalgen as G  # noqa: E402


def png() -> bytes:
    buf = io.BytesIO()
    Image.fromarray(G.build({"gen": "bands", "size": [24, 16], "colors": [[60, 120, 200], [240, 240, 240], [20, 20, 20]], "axis": "x"}), "RGB").save(buf, format="PNG", compress_level=6, optimize=False)
    return buf.getvalue()


def files() -> dict[str, bytes]:
    b64 = base64.b64encode(png()).decode()
    note = "SYNTHETIC sample written by scripts/make_samples.py: NOT a real hub capture; the shape is an assumption (schema_unverified)"
    return {
        "samples/synthetic/screen_capture.mcp_image_block.synthetic.json": (json.dumps({"_note": note, "content": [{"type": "text", "text": "captured"}, {"type": "image", "data": b64, "mimeType": "image/png"}]}, indent=1) + "\n").encode(),
        "samples/synthetic/screen_capture.json_field.synthetic.json": (json.dumps({"_note": note, "image": b64}, indent=1) + "\n").encode(),
        "samples/synthetic/screen_capture.raw.synthetic.png": png(),
    }


def main() -> int:
    check = "--check" in sys.argv
    bad = []
    for rel, data in files().items():
        f = ROOT / rel
        if check:
            same = f.exists() and (f.read_bytes() == data if f.suffix != ".png" else np.array_equal(np.asarray(Image.open(f)), np.asarray(Image.open(io.BytesIO(data)))))
            if not same:
                bad.append(rel)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
    if check:
        print("out of date: " + ", ".join(bad) if bad else "samples are current")
        return 1 if bad else 0
    print("wrote samples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
