"""Reading a screenshot the hub's ``screen_capture`` tool returned (SCHEMA_UNVERIFIED).

No real ``screen_capture`` result was available while this was written, so the shape below is an ASSUMPTION built from the MCP specification's image content
block and the field names other repositories in this suite guessed. Treat every result of this parser as ``schema_unverified`` until a real capture has been
saved in ``samples/hub/`` and the parser has been run on it (see samples/README.md).

Accepted inputs (a file the user saved from the hub's result):

1. a raw image file (PNG, JPEG ...): used as is;
2. JSON holding an MCP image content block ``{"type": "image", "data": "<base64>", "mimeType": "image/png"}`` anywhere in the common wrappers
   ``{"content": [...]}``, ``{"result": {"content": [...]}}`` or a bare list of blocks;
3. JSON with one of the keys ``image``, ``data``, ``base64``, ``screenshot`` or ``png`` holding base64 (optionally a ``data:image/...;base64,`` URL).

Anything else is refused with the list of keys that were found, so the mismatch is visible rather than guessed around.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from typing import Any

from PIL import Image

SCHEMA_STATUS = "schema_unverified"
KEYS = ("image", "data", "base64", "screenshot", "png")
_DATA_URL = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,", re.I)


class CaptureRefused(ValueError):
    pass


def _b64(s: str) -> bytes:
    s = _DATA_URL.sub("", s.strip(), count=1)
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CaptureRefused("the image field is not valid base64") from exc


def _blocks(obj: Any):
    if isinstance(obj, list):
        for x in obj:
            yield from _blocks(x)
    elif isinstance(obj, dict):
        if obj.get("type") == "image" and isinstance(obj.get("data"), str):
            yield obj
        for k in ("content", "result", "output", "images"):
            if k in obj:
                yield from _blocks(obj[k])


def extract(raw: bytes, max_bytes: int) -> tuple[bytes, dict]:
    """``(image_bytes, info)`` from the bytes of a saved hub result. ``info`` always carries ``input_schema: schema_unverified``."""
    if len(raw) > max_bytes * 2:
        raise CaptureRefused(f"the saved capture is {len(raw)} bytes, above the limit")
    info: dict = {"input_schema": SCHEMA_STATUS}
    head = raw.lstrip()[:1]
    if head not in (b"{", b"["):
        info["form"] = "raw image file"
        return raw, info
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureRefused("the file is neither an image nor JSON") from exc
    blocks = list(_blocks(obj))
    if blocks:
        info.update(form="MCP image content block", mime=blocks[0].get("mimeType"), blocks_found=len(blocks))
        return _b64(blocks[0]["data"]), info
    if isinstance(obj, dict):
        for k in KEYS:
            if isinstance(obj.get(k), str) and len(obj[k]) > 16:
                info.update(form=f"JSON field '{k}'")
                return _b64(obj[k]), info
        keys = sorted(obj)[:12]
    else:
        keys = [type(obj).__name__]
    raise CaptureRefused(f"no image found in the saved result (schema_unverified). Top-level keys: {keys}. Save the hub's full screen_capture result as described in samples/README.md")


def decode(raw: bytes, max_bytes: int, max_pixels: int) -> tuple[Image.Image, dict]:
    data, info = extract(raw, max_bytes)
    if len(data) > max_bytes:
        raise CaptureRefused(f"the image is {len(data)} bytes, above the limit {max_bytes}")
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > max_pixels:
            raise CaptureRefused(f"the image has {img.width * img.height} pixels, above the limit {max_pixels}")
        img.load()
    except CaptureRefused:
        raise
    except Exception as exc:
        raise CaptureRefused(f"the image data could not be decoded: {type(exc).__name__}") from exc
    info.update(format=img.format, size=list(img.size), mode=img.mode)
    return img, info
