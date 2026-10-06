"""Roblox engine API facts, read from references/roblox_api_snapshot.json.

The snapshot is built from Roblox's public creator-docs repository by
``scripts/refresh_api_snapshot.py``. Anything not in the snapshot is rejected by the validator rather
than guessed, so a hallucinated property name fails before it reaches Studio.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "references" / "roblox_api_snapshot.json"

# Classes recipes may create. Everything else needs a deliberate code change plus a snapshot refresh.
ALLOWED_CLASSES = ("Attachment", "ParticleEmitter", "Beam", "Trail", "PointLight")

# Value types the encoder/validator understand. Other snapshot types (CFrame, Content...) are not authored.
SUPPORTED_TYPES = {"float", "int", "boolean", "string", "NumberRange", "NumberSequence", "ColorSequence",
                   "Vector2", "Vector3", "Color3", "ContentId", "Attachment"}


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    return json.loads(Path(path or SNAPSHOT).read_text(encoding="utf-8"))


def properties(cls: str, snapshot: dict | None = None) -> dict[str, dict]:
    """All settable properties of a class including inherited ones (child definitions win)."""
    snap = snapshot or load()
    out: dict[str, dict] = {}
    chain, seen = [cls], set()
    while chain:
        c = chain.pop(0)
        if c in seen or c not in snap["classes"]:
            continue
        seen.add(c)
        for name, info in snap["classes"][c]["properties"].items():
            out.setdefault(name, info)
        chain.extend(snap["classes"][c].get("inherits", []))
    return out


def property_type(cls: str, prop: str, snapshot: dict | None = None) -> str | None:
    info = properties(cls, snapshot).get(prop)
    return info["type"] if info else None


def enum_values(enum: str, snapshot: dict | None = None) -> list[str] | None:
    return (snapshot or load())["enums"].get(enum)


def methods(cls: str, snapshot: dict | None = None) -> list[str]:
    snap = snapshot or load()
    return snap["classes"].get(cls, {}).get("methods", [])
