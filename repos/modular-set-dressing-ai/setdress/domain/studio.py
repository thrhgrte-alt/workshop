"""Parse what generated Luau returned or printed in Roblox Studio (JSON, possibly mixed with log lines)."""

from __future__ import annotations

import json


def parse_report(text: str) -> dict:
    for candidate in [text.strip()] + [ln for ln in text.splitlines() if ln.strip().startswith("{")]:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON report found in the Studio output. Paste the execute_luau result (or the console output) as-is.")
