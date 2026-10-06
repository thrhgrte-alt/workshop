"""Observations: an append-only, local JSONL log of every run (layer 1 of the learning layer).

A row records what was asked, for which project and place, which tools ran, a summary of the findings, output size, time and
(later, as a separate row) what the user did with the result: accept, reject or edit. Rows are only ever appended; nothing here
rewrites or deletes one. The log never leaves the machine: this module imports no network code (a test enforces it).

Redaction. Free text is passed through :func:`redact` BEFORE it is written: file system paths (POSIX, Windows, ``~``), anything that
looks like an API key, token, password or cookie, long random-looking strings, e-mail addresses and Roblox auth cookies become
``<path>`` / ``<secret>`` / ``<email>``. Redaction is pattern based and best effort: it reduces accidental leakage, it is not a
guarantee, so do not log content you could not show a colleague.

Row shapes (``schema`` = 1)::

    {"kind": "run",    "run_id", "at", "request", "project_id", "place_id", "tools": [...], "findings": {...}, "signals": [...],
                       "output_chars", "seconds", "extra"}
    {"kind": "action", "run_id", "at", "action": "accept|reject|edit", "note", "targets": [...]}

``signals`` is what the proposal step groups: ``{"kind": "override", "target": "param:rule.SEC003.confidence", "direction": "down"}``
(see :mod:`guide_core.propose`). ``considered`` lists the targets that were in play in the run, so "overridden in 6 of 8 runs" has a
true denominator.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .scope import Scope, ScopeError

ACTIONS = ("accept", "reject", "edit")

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:bearer|basic|token)?\s*[A-Za-z0-9._~+/=-]{8,}"), "<secret>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "<secret>"),
    (re.compile(r"(?i)\b(?:api[_-]?key|apikey|secret|token|passwd|password|pwd|cookie|roblosecurity|access[_-]?key|private[_-]?key)\b\s*[:=]\s*[\"']?[^\s\"',;]{3,}"), "<secret>"),
    (re.compile(r"_\|WARNING:-DO-NOT-SHARE-THIS[^\s\"']*"), "<secret>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "<secret>"),  # JWT
    (re.compile(r"\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{16,}\b"), "<secret>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat|glpat|xox[abprs]|AKIA|ASIA|AIza)[A-Za-z0-9_-]{12,}\b"), "<secret>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), "<secret>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    # paths: Windows drive/UNC, home-relative, absolute POSIX with at least two segments
    (re.compile(r"\b[A-Za-z]:[\\/](?:[^\s\\/\"'<>|]+[\\/])*[^\s\\/\"'<>|]*"), "<path>"),
    (re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"']+"), "<path>"),
    (re.compile(r"(?<![\w/.])~[\\/][^\s\"']*"), "<path>"),
    (re.compile(r"(?<![\w:/.-])/(?:[A-Za-z0-9._@+-]+/)+[A-Za-z0-9._@+-]*"), "<path>"),
    # long random-looking strings (hex or base64-ish, 32+ chars with at least one digit and one letter)
    (re.compile(r"\b(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}={0,2}"), "<secret>"),
]


def redact(value: Any) -> Any:
    """Redact strings anywhere inside ``value`` (str, list, dict, tuple); other types pass through."""
    if isinstance(value, str):
        out = value
        for pat, repl in _PATTERNS:
            out = pat.sub(repl, out)
        return out
    if isinstance(value, dict):
        return {redact(k) if isinstance(k, str) else k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class RunLog:
    """Append-only run log at ``path`` (a ``.jsonl`` file; its parent is created on first write)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")

    def log_run(self, *, request: str, scope: Scope | None, tools: Iterable[str] = (), findings: dict | None = None,
                signals: Iterable[dict] = (), considered: Iterable[str] = (), output_chars: int | None = None,
                seconds: float | None = None, extra: dict | None = None, strict_scope: bool = True) -> str:
        """Log one run; returns its ``run_id``. A missing scope is refused unless ``strict_scope=False``."""
        if not request or not request.strip():
            raise ValueError("request must not be empty")
        if scope is None and strict_scope:
            raise ScopeError("log_run needs a resolved scope (project_id, optional place_id)")
        run_id = f"obs-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        sigs = []
        for s in signals:
            if not s.get("kind") or not s.get("target"):
                raise ValueError("a signal needs 'kind' and 'target'")
            sigs.append(redact(dict(s)))
        self._append({
            "schema": SCHEMA_VERSION, "kind": "run", "run_id": run_id, "at": _now(), "request": redact(request),
            "project_id": scope.project_id if scope else None, "place_id": scope.place_id if scope else None,
            "global": bool(scope and scope.is_global), "tools": list(tools), "findings": redact(findings or {}),
            "signals": sigs, "considered": sorted(set(considered)), "output_chars": output_chars,
            "seconds": None if seconds is None else round(float(seconds), 4), "extra": redact(extra or {}),
        })
        return run_id

    def log_action(self, run_id: str, action: str, *, note: str = "", targets: Iterable[str] = ()) -> dict:
        """The user's verdict on a run, as a NEW row (the run row is never edited)."""
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        if not any(r["run_id"] == run_id for r in self.rows(kind="run")):
            raise ValueError(f"unknown run_id '{run_id}'")
        row = {"schema": SCHEMA_VERSION, "kind": "action", "run_id": run_id, "at": _now(), "action": action,
               "note": redact(note), "targets": sorted(set(targets))}
        self._append(row)
        return row

    # reading -------------------------------------------------------------------------------------------------
    def rows(self, kind: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn last line is skipped, never "repaired"
                if kind is None or row.get("kind") == kind:
                    out.append(row)
        return out

    def runs(self, scope: Scope | None = None, *, include_global: bool = True) -> list[dict]:
        """Run rows with their latest action merged in as ``user_action``. With a scope: that project (and place) only."""
        actions: dict[str, dict] = {}
        for a in self.rows(kind="action"):
            actions[a["run_id"]] = a
        out = []
        for r in self.rows(kind="run"):
            if scope is not None and not scope.is_global:
                rs = None if r.get("project_id") is None else (Scope.make_global() if r.get("global") else Scope(r["project_id"], r.get("place_id")))
                if rs is None or not ((rs.is_global and include_global) or (not rs.is_global and rs.covers(scope))):
                    continue
            r = dict(r)
            r["user_action"] = actions.get(r["run_id"], {}).get("action")
            r["user_targets"] = actions.get(r["run_id"], {}).get("targets", [])
            out.append(r)
        return out
