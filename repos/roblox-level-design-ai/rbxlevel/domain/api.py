"""Roblox engine API facts for blockouts, read from references/roblox_api_snapshot.json.

The snapshot comes from Roblox's public creator-docs repository (``scripts/refresh_api_snapshot.py``). Blockouts
may create only these classes; anything else is rejected before code is generated.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[2] / "references" / "roblox_api_snapshot.json"
ALLOWED_CLASSES = ("Folder", "Model", "Part", "SpawnLocation")


@lru_cache(maxsize=4)
def load(path: str | None = None) -> dict:
    return json.loads(Path(path or SNAPSHOT).read_text(encoding="utf-8"))


def properties(cls: str, snapshot: dict | None = None) -> dict[str, dict]:
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
