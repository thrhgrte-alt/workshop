"""Helpers for handing generated Luau to Roblox Studio's built-in MCP server.

Roblox Studio ships its own MCP server (enable it under Assistant > Manage MCP Servers). Per Roblox's
documentation it exposes, among others, ``execute_luau`` (requires ``datamodel_type``: Edit, Client or
Server), ``search_game_tree``, ``inspect_instance``, ``screen_capture`` (optional custom camera position and
look-at target), ``start_stop_play``, ``get_console_output`` and ``list_roblox_studios``; every tool takes a
``studio_id``. This repository's own MCP server does NOT talk to Studio. It generates and validates code;
the agent forwards it to Studio's server. The exact argument name that carries the code is defined by
Studio's tool schema: read it from the tool listing instead of trusting this module.
"""

from __future__ import annotations

import json


def studio_handoff(luau: str, purpose: str, *, datamodel_type: str = "Edit") -> dict:
    """What to do with a generated script, expressed for an agent connected to Studio's MCP server."""
    return {
        "purpose": purpose,
        "forward_to": "Roblox Studio MCP server (name it was configured with, commonly 'Roblox_Studio')",
        "tool": "execute_luau",
        "datamodel_type": datamodel_type,
        "studio_id": "from list_roblox_studios",
        "code_argument": "see execute_luau's input schema in the tool listing",
        "then": "read the returned JSON (also printed); then get_console_output if anything looks wrong",
        "luau_chars": len(luau),
    }


def parse_report(text: str) -> dict:
    """Parse the JSON a generated script returns or prints. Tolerates surrounding log lines."""
    for candidate in [text.strip()] + [ln for ln in text.splitlines() if ln.strip().startswith("{")]:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON report found in the Studio output. Paste the execute_luau result (or the console output) as-is.")
