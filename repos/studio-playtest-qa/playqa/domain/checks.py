"""The check library (``rules/*.yaml``) and helpers to read the thresholds a check uses.

A check definition names its script ``kind``, the context the script is assumed to run in, whether it can share a play session, whether it is flaky by default, the settings
and ranges it reads, the assertion ids it can emit and what it does not check. ``causes.yaml`` and ``log_patterns.yaml`` are not checks and are loaded elsewhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import SECTION_OF

NON_CHECK_FILES = {"causes.yaml", "log_patterns.yaml"}


def load_library(root: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted((Path(root) / "rules").glob("*.yaml")):
        if f.name in NON_CHECK_FILES:
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for c in data.get("checks", []):
            problems = validate_check(c)
            if problems:
                raise ValueError(f"{f.name}: check {c.get('id')!r}: " + "; ".join(problems))
            if c["id"] in out:
                raise ValueError(f"{f.name}: duplicate check id '{c['id']}'")
            out[c["id"]] = {**c, "_file": f.name}
    return out


def validate_check(c: dict) -> list[str]:
    p = []
    for k in ("id", "kind", "title", "context", "session", "flaky", "config_section", "assertions", "not_checked"):
        if k not in c:
            p.append(f"missing '{k}'")
    if p:
        return p
    if c["kind"] not in SECTION_OF:
        p.append(f"unknown kind '{c['kind']}'")
    elif SECTION_OF[c["kind"]] != c["config_section"]:
        p.append(f"config_section must be '{SECTION_OF[c['kind']]}' for kind {c['kind']}")
    if c["context"] not in ("server", "client"):
        p.append("context must be server or client")
    if c["session"] not in ("shared", "own"):
        p.append("session must be shared or own")
    if not c["assertions"] or any("id" not in a or "title" not in a or a.get("source") not in ("script", "tool") for a in c["assertions"]):
        p.append("assertions need id, title and source script|tool")
    if not c["not_checked"]:
        p.append("not_checked must say what the check does not cover")
    return p


def is_flaky(check: dict, cfg: dict) -> bool:
    if check["id"] in (cfg.get("not_flaky") or []):
        return False
    return bool(check["flaky"]) or check["id"] in (cfg.get("flaky") or [])


def check_flags(check: dict, cfg: dict) -> dict:
    """Why this check is (not) flaky for this place."""
    if check["id"] in (cfg.get("not_flaky") or []):
        return {"flaky": False, "why": "not_flaky in playtest.yaml"}
    if check["id"] in (cfg.get("flaky") or []):
        return {"flaky": True, "why": "flaky in playtest.yaml"}
    return {"flaky": bool(check["flaky"]), "why": "library default"}


def configured(check: dict, cfg: dict) -> bool:
    return check["config_section"] in cfg


def setting(style: dict, name: str) -> Any:
    return style["settings"][name]["value"]


def rng(style: dict, name: str) -> dict:
    return style["ranges"][name]


def repeats_for(check: dict, cfg: dict, style: dict, override: int | None = None) -> int:
    if override is not None:
        return int(override)
    return int(setting(style, "flaky_repeats")) if is_flaky(check, cfg) else 1
