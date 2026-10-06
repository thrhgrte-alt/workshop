#!/usr/bin/env python3
"""Rebuild references/roblox_api_snapshot.json (blockout classes) from Roblox's public creator-docs repository.

The snapshot (class properties + types, enum members) is what the validator trusts. Rebuild it when Roblox
adds properties you want to use. Needs network access to raw.githubusercontent.com.
"""
import datetime
import json
import sys
import urllib.request
from pathlib import Path

import yaml

BASE = "https://raw.githubusercontent.com/Roblox/creator-docs/main/content/en-us/reference/engine"
CLASSES = ["Instance", "PVInstance", "BasePart", "FormFactorPart", "Part", "Folder", "Model"]
ENUMS = ["Material", "PartType"]
KEEP_INSTANCE = {"Name", "Parent", "Archivable"}
CLASS_DOCS_NOTE = "Layout/blockout classes only"


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return yaml.safe_load(r.read().decode("utf-8"))


def main() -> None:
    snap = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "source": "https://github.com/Roblox/creator-docs (content/en-us/reference/engine, branch main)",
            "classes": {}, "enums": {}}
    for cls in CLASSES:
        doc = fetch(f"{BASE}/classes/{cls}.yaml")
        props = {}
        for p in doc.get("properties", []):
            name = p["name"].split(".")[-1]
            if cls == "Instance" and name not in KEEP_INSTANCE:
                continue
            props[name] = {"type": p["type"], "deprecated": bool(p.get("deprecation_message")),
                           "summary": " ".join((p.get("summary") or "").split())[:160]}
        methods = [m["name"].split(":")[-1] for m in doc.get("methods", [])]
        snap["classes"][cls] = {"properties": props, "methods": methods, "inherits": doc.get("inherits", [])}
    for enum in ENUMS:
        doc = fetch(f"{BASE}/enums/{enum}.yaml")
        snap["enums"][enum] = [i["name"] for i in doc["items"] if not i.get("deprecation_message")]
    out = Path(__file__).resolve().parents[1] / "references" / "roblox_api_snapshot.json"
    out.write_text(json.dumps(snap, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(snap['classes'])} classes, {len(snap['enums'])} enums")


if __name__ == "__main__":
    sys.exit(main())
