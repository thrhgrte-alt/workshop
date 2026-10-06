"""Result formats and hub-output parsing. EVERY parser here is ``schema_unverified``.

No real capture of ``execute_luau``, ``get_console_output``, ``screen_capture``, ``roblox_studio_start_stop_play`` or Studio-side pathfinding output exists in this environment.
What is documented below is the format this repository's OWN generated scripts emit (``playqa.result/1``, which it controls) and the SHAPES it is willing to accept from the
hub (tolerant, listed, and labelled schema_unverified in every tool answer). ``samples/`` holds SYNTHETIC examples written by hand; ``samples/README.md`` says which hub call
to run to replace each with a real capture.

``playqa.result/1`` (one JSON object returned by a generated script, also printed as ``PLAYQA_RESULT <json>``)::

    {"schema": "playqa.result/1", "check_id": "boot", "kind": "boot|spawn|reachability|remotes|economy|data|perf", "repeat_no": 1,
     "place": {"place_id": 0, "name": "Demo Mine Main (SYNTHETIC)"},
     "refused": null | "why the script declined to run",
     "assertions": [{"id": "services_exist", "subject": "optional", "ok": true, "measured": any, "expected": any, "detail": "text"}],
     "measures": { ...kind specific, see playqa/domain/evaluate.py... }}

An empty Luau table encodes as ``[]``, so every reader treats ``[]`` as ``{}`` where a mapping is expected.

Accepted wrappers around the raw hub answer of ``execute_luau`` (all unverified): a JSON string; a mapping that already is the result; a mapping whose
``result|output|returns|return|value|content|text|stdout|message`` holds either of those; a list of ``{"type": "text", "text": ...}`` blocks; or a console-like text containing a
line ``PLAYQA_RESULT <json>``. A text that contains an error and no result is a script error (the check could not run: inconclusive, not a failure of the game).
"""

from __future__ import annotations

import json
import re
from typing import Any

SCHEMA_STATUS = "schema_unverified"
RESULT_SCHEMA = "playqa.result/1"
BASELINE_SCHEMA = "playqa.baseline/1"
KINDS = ("boot", "spawn", "reachability", "remotes", "economy", "data", "perf")
WRAPPER_KEYS = ("result", "output", "returns", "return", "value", "content", "text", "stdout", "message")

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "check_id": {"type": "string", "description": "Check id from rules/*.yaml"},
        "verdict": {"enum": ["pass", "fail", "flaky", "inconclusive", "refused"], "description": "Verdict recorded when the entry was made"},
        "failed_assertions": {"type": "array", "items": {"type": "string"}, "description": "assertion ids that failed (failure_case entries)"},
        "result_file": {"type": "string", "description": "Path of the synthetic or real playqa.result/1 file the entry was made from"},
        "console_file": {"type": "string"},
        "measures": {"type": "object", "description": "Headline measured values, re-checked by a test for synthetic entries"},
        "baseline_name": {"type": "string"},
    },
}


class ParseNote(str):
    """A short remark about how a hub answer was read."""


def as_dict(x: Any) -> dict:
    """Luau encodes an empty table as []; treat that as an empty mapping."""
    if isinstance(x, dict):
        return x
    return {}


def as_list(x: Any) -> list:
    if isinstance(x, list):
        return x
    if isinstance(x, dict) and not x:
        return []
    return []


def _text_of(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list) and raw and all(isinstance(b, dict) for b in raw):
        parts = [b.get("text") for b in raw if isinstance(b.get("text"), str)]
        return "\n".join(parts) if parts else None
    return None


def _find_result_in_text(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        i = line.find("PLAYQA_RESULT ")
        if i >= 0:
            try:
                doc = json.loads(line[i + len("PLAYQA_RESULT "):])
            except json.JSONDecodeError:
                continue
            if isinstance(doc, dict):
                return doc
    s = text.strip()
    if s.startswith("{"):
        try:
            doc = json.loads(s)
            if isinstance(doc, dict):
                return doc
        except json.JSONDecodeError:
            return None
    return None


def extract_result(raw: Any, _depth: int = 0) -> tuple[dict | None, str | None, list[str]]:
    """``(result_doc, error_text, notes)`` from one raw hub answer. ``result_doc`` is None when no ``playqa.result/1`` object could be found;
    ``error_text`` is the text that explains why (script error or unreadable). Never raises."""
    notes = [SCHEMA_STATUS]
    if _depth > 4:
        return None, "answer nested too deeply to read", notes
    if isinstance(raw, dict) and raw.get("schema") == RESULT_SCHEMA:
        return raw, None, notes
    if isinstance(raw, dict):
        err = None
        for k in WRAPPER_KEYS:
            if k in raw:
                doc, e, n = extract_result(raw[k], _depth + 1)
                if doc is not None:
                    return doc, None, notes + n
                err = err or e
        if isinstance(raw.get("error"), str) and raw["error"]:
            err = raw["error"]
        if raw.get("isError") and err is None:
            err = "the hub reported an error and gave no text"
        return None, err or "no playqa.result/1 object found in the mapping", notes
    text = _text_of(raw)
    if text is None:
        return None, f"cannot read a {type(raw).__name__} as a hub answer", notes
    doc = _find_result_in_text(text)
    if doc is not None:
        if doc.get("schema") == RESULT_SCHEMA:
            return doc, None, notes
        for k in WRAPPER_KEYS:
            if k in doc:
                d2, _, n = extract_result(doc[k], _depth + 1)
                if d2 is not None:
                    return d2, None, notes + n
    return None, (text.strip()[:400] or "empty answer"), notes


def validate_result(doc: dict) -> list[str]:
    """Problems with a ``playqa.result/1`` object (empty list = readable)."""
    p: list[str] = []
    if doc.get("schema") != RESULT_SCHEMA:
        p.append(f"schema must be '{RESULT_SCHEMA}'")
    if not isinstance(doc.get("check_id"), str) or not doc.get("check_id"):
        p.append("check_id must be a non-empty string")
    if doc.get("kind") not in KINDS:
        p.append(f"kind must be one of {list(KINDS)}")
    ass = doc.get("assertions", [])
    if not isinstance(ass, (list, dict)):
        p.append("assertions must be a list")
    else:
        for i, a in enumerate(as_list(ass)):
            if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not isinstance(a.get("ok"), bool):
                p.append(f"assertions[{i}] needs a string id and a boolean ok")
    if not isinstance(doc.get("measures", {}), (dict, list)):
        p.append("measures must be a mapping")
    if doc.get("refused") is not None and not isinstance(doc["refused"], str):
        p.append("refused must be null or a string")
    return p


_WRONG_PLACE = re.compile(r"wrong place", re.I)


def is_wrong_place_error(text: str | None) -> bool:
    return bool(text and _WRONG_PLACE.search(text))


def studios_note() -> str:
    return ("the open-Studio list is read by guide_core.scope.match_open_studio: accepted keys name|placeName|title, place_id|placeId|PlaceId, studio_id|studioId|id "
            f"({SCHEMA_STATUS}: no real list_roblox_studios capture exists here)")
