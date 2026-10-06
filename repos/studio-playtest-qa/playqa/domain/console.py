"""Console log parsing (``parse_console_log``). SCHEMA_UNVERIFIED: no real ``get_console_output`` capture exists here.

Accepted input (all assumptions, see ``schema.py``):

* a text, one console line per line, optionally starting with a time stamp (``HH:MM:SS``, ``HH:MM:SS.mmm``, optionally in brackets, or an ISO-8601 date-time) and
  optionally a level tag (``[Error]``, ``Warning:`` ...). Studio's own output has no tag: those lines are classified by the patterns in ``rules/log_patterns.yaml``
  (``level_source: pattern``), which is a heuristic and is reported as one;
* a list of mappings with a message under ``message|text|msg``, a level under ``type|messageType|level|severity`` (``MessageError``, ``Enum.MessageType.MessageWarning``,
  ``error`` ...) and a time under ``timestamp|time|t`` (seconds as a number, or one of the string forms above);
* a mapping with the list under ``entries|messages|lines|output|logs`` or the text under ``text|content``.

The boot window is anchored at the FIRST time-stamped line (the console is assumed to start with play) unless a ``PLAYQA_MARK <check> <repeat> start`` line is used. When
lines carry no time stamps the window cannot be applied: every line counts and the result says so (``window_applied: false``).
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any

import yaml

SCHEMA_STATUS = "schema_unverified"
TS_RE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*(.*)$", re.S)
ISO_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\s*(.*)$", re.S)
SRC_RE = re.compile(r"([A-Za-z_][\w.]*):(\d+)")
MARK_RE = re.compile(r"PLAYQA_MARK\s+(\w+)\s+(\d+)\s+(start|end)")
OWN_RE = re.compile(r"PLAYQA_RESULT ")
PROBE_RE = re.compile(r"PLAYQA_PROBE\s+(\w+)\s+(\w+)")
MAX_CHARS = 5_000_000
LIST_KEYS = ("entries", "messages", "lines", "output", "logs")
TEXT_KEYS = ("text", "content")
LEVEL_KEYS = ("type", "messageType", "level", "severity")
MSG_KEYS = ("message", "text", "msg")
TIME_KEYS = ("timestamp", "time", "t")


def load_patterns(root: Path) -> dict:
    data = yaml.safe_load((Path(root) / "rules" / "log_patterns.yaml").read_text(encoding="utf-8")) or {}
    for k in ("tags", "error_patterns", "warning_patterns", "continuation_patterns", "ignore_patterns"):
        data.setdefault(k, [] if k != "tags" else {})
    return data


def _ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = re.fullmatch(r"\[?(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?\]?", s)
    if m:
        h, mi, se, frac = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(se) + (float("0." + frac) if frac else 0.0)
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return None


def _level_from_tag(text: str, tags: dict) -> tuple[str | None, str]:
    low = text.lstrip().lower()
    for level in ("error", "warning", "info"):
        for t in tags.get(level, []):
            if low.startswith(t):
                return level, text.lstrip()[len(t):].lstrip(" :-")
    return None, text


def _level_from_field(value: Any, tags: dict) -> str | None:
    if value is None:
        return None
    low = str(value).strip().lower()
    for level in ("error", "warning", "info"):
        if any(low == t.strip("[]<>:- ") or low.endswith(t.strip("[]<>:- ")) for t in tags.get(level, [])):
            return level
    if "error" in low:
        return "error"
    if "warn" in low:
        return "warning"
    if low in ("info", "output", "print", "log", "message", "messageoutput", "messageinfo"):
        return "info"
    return None


def _classify(text: str, patterns: dict) -> str:
    if OWN_RE.search(text):
        return "info"  # this tool's own result line carries other lines' words inside its JSON
    low = text.lower()
    if any(re.search(p, low) for p in patterns["error_patterns"]):
        return "error"
    if any(re.search(p, low) for p in patterns["warning_patterns"]):
        return "warning"
    return "info"


def _entries_from_input(source: Any) -> tuple[list[dict], list[str]]:
    """Raw entries ``{n, t, level_field, text}`` plus notes about how the input was read."""
    notes: list[str] = [SCHEMA_STATUS]
    if isinstance(source, dict):
        for k in LIST_KEYS:
            if isinstance(source.get(k), list):
                source = source[k]
                break
        else:
            for k in TEXT_KEYS:
                if isinstance(source.get(k), str):
                    source = source[k]
                    break
            else:
                raise ValueError("console_log mapping needs a list under one of " + "|".join(LIST_KEYS) + " or a text under " + "|".join(TEXT_KEYS))
    out: list[dict] = []
    if isinstance(source, str):
        if len(source) > MAX_CHARS:
            raise ValueError(f"console_log is {len(source):,} characters, above the {MAX_CHARS:,} limit: pass the part around the boot window or one check")
        for i, line in enumerate(source.splitlines()):
            if not line.strip():
                continue
            t = None
            text = line
            m = TS_RE.match(line) or ISO_RE.match(line)
            if m:
                t, text = _ts(m.group(1)), m.group(2)
            out.append({"n": i + 1, "t": t, "level_field": None, "text": text.rstrip()})
        notes.append("read as text lines")
        return out, notes
    if isinstance(source, list):
        if len(source) > 200_000:
            raise ValueError("console_log has more than 200,000 entries: pass the part around the boot window or one check")
        for i, e in enumerate(source):
            if isinstance(e, str):
                m = TS_RE.match(e) or ISO_RE.match(e)
                out.append({"n": i + 1, "t": _ts(m.group(1)) if m else None, "level_field": None, "text": (m.group(2) if m else e).rstrip()})
                continue
            if not isinstance(e, dict):
                raise ValueError(f"console_log entry {i} must be a string or a mapping")
            msg = next((e[k] for k in MSG_KEYS if isinstance(e.get(k), str)), None)
            if msg is None:
                raise ValueError(f"console_log entry {i} has no message text under {'|'.join(MSG_KEYS)}")
            lvl = next((e[k] for k in LEVEL_KEYS if k in e), None)
            ts = next((_ts(e[k]) for k in TIME_KEYS if k in e), None)
            out.append({"n": i + 1, "t": ts, "level_field": lvl, "text": msg.rstrip()})
        notes.append("read as a list of entries")
        return out, notes
    raise ValueError("console_log must be text, a list of lines or entries, or a mapping holding one")


def parse(source: Any, patterns: dict, *, window_seconds: float | None = None, anchor: str = "first_line", marker: tuple[str, int] | None = None,
          ignore: list[str] | None = None, max_findings: int = 10, line_chars: int = 200, detail: bool = False) -> dict:
    """Parse a console log into entries and a ranked summary. Pure function."""
    raw, notes = _entries_from_input(source)
    entries: list[dict] = []
    cont = [re.compile(p, re.I) for p in patterns["continuation_patterns"]]
    for r in raw:
        text = r["text"]
        if entries and any(c.search(text.strip()) for c in cont):
            entries[-1].setdefault("trace", []).append(text.strip()[:line_chars])
            continue
        level, rest = (None, text)
        if r["level_field"] is not None:
            level = _level_from_field(r["level_field"], patterns["tags"])
            level_source = "type_field" if level else None
        else:
            level, rest = _level_from_tag(text, patterns["tags"])
            level_source = "tag" if level else None
        if level is None:
            level, level_source = _classify(rest, patterns), "pattern"
        msrc = SRC_RE.search(rest)
        entries.append({"n": r["n"], "t": r["t"], "level": level, "level_source": level_source, "text": rest.strip()[:line_chars],
                        "script": msrc.group(1) if msrc else None, "line": int(msrc.group(2)) if msrc else None})
    ign = [re.compile(p, re.I) for p in (list(patterns.get("ignore_patterns") or []) + list(ignore or []))]
    marks = [(i, MARK_RE.search(e["text"])) for i, e in enumerate(entries)]
    marks = [(i, m) for i, m in marks if m]
    marker_found = None
    window_note = None
    selected = entries
    if marker is not None:
        cid, rep = marker
        s = next((i for i, m in marks if m.group(1) == cid and int(m.group(2)) == rep and m.group(3) == "start"), None)
        e = next((i for i, m in marks if m.group(1) == cid and int(m.group(2)) == rep and m.group(3) == "end" and (s is None or i > s)), None)
        marker_found = s is not None
        if s is not None:
            selected = entries[s + 1: e if e is not None else len(entries)]
        else:
            window_note = f"marker 'PLAYQA_MARK {cid} {rep} start' not found: the whole log was read"
    window_applied = False
    if window_seconds is not None and anchor == "first_line":
        timed = [e for e in selected if e["t"] is not None]
        if timed:
            t0 = timed[0]["t"]
            keep, inside = [], True
            for e in selected:
                if e["t"] is not None:
                    inside = e["t"] - t0 <= window_seconds
                if inside:
                    keep.append(e)
            selected = keep
            window_applied = True
        else:
            window_note = "no time stamps in the log: the window cannot be applied, every line counts"
    ignored = [e for e in selected if e["level"] != "info" and any(p.search(e["text"]) for p in ign)]
    ignored_ids = {id(e) for e in ignored}
    counted = [e for e in selected if id(e) not in ignored_ids and not MARK_RE.search(e["text"]) and not PROBE_RE.search(e["text"])]
    groups: dict[tuple, dict] = {}
    for e in counted:
        if e["level"] == "info":
            continue
        key = (e["level"], e["script"], e["line"], re.sub(r"\d+", "#", e["text"])[:120])
        g = groups.setdefault(key, {"level": e["level"], "level_source": e["level_source"], "text": e["text"], "script": e["script"], "line": e["line"], "count": 0, "first_line_no": e["n"]})
        g["count"] += 1
    ranked = sorted(groups.values(), key=lambda g: (0 if g["level"] == "error" else 1, -g["count"], g["first_line_no"]))
    n_err = sum(1 for e in counted if e["level"] == "error")
    n_warn = sum(1 for e in counted if e["level"] == "warning")
    cap = None if detail else max_findings
    shown = ranked if cap is None else ranked[:cap]
    flags = []
    if any(e["level_source"] == "pattern" for e in counted if e["level"] != "info"):
        flags.append("some levels were inferred from message patterns (heuristic)")
    res = {
        "lines_read": len(raw), "lines_in_window": len(selected), "errors": n_err, "warnings": n_warn, "ignored": len(ignored),
        "distinct": len(ranked), "window_seconds": window_seconds, "window_applied": window_applied, "marker_found": marker_found,
        "findings": shown, "more_findings": max(0, len(ranked) - len(shown)), "notes": notes + flags + ([window_note] if window_note else []),
        "_entries": counted,
    }
    return res


def attribute_to_probes(entries: list[dict]) -> dict[str, list[dict]]:
    """Error and warning entries that follow a ``PLAYQA_PROBE <remote> <label>`` line, up to the next probe or mark line; key ``<remote>/<label>``.
    Entries before the first probe go under ``""``."""
    out: dict[str, list[dict]] = {}
    key = ""
    for e in entries:
        m = PROBE_RE.search(e["text"])
        if m:
            key = f"{m.group(1)}/{m.group(2)}"
            out.setdefault(key, [])
            continue
        if MARK_RE.search(e["text"]):
            key = ""
            continue
        if e["level"] in ("error", "warning"):
            out.setdefault(key, []).append(e)
    return out


def parse_all_probes(source: Any, patterns: dict, ignore: list[str] | None = None, marker: tuple[str, int] | None = None) -> dict[str, list[dict]]:
    """Convenience: parse ``source`` WITHOUT dropping probe lines and attribute errors to probes."""
    raw, _ = _entries_from_input(source)
    cont = [re.compile(p, re.I) for p in patterns["continuation_patterns"]]
    ign = [re.compile(p, re.I) for p in (list(patterns.get("ignore_patterns") or []) + list(ignore or []))]
    entries: list[dict] = []
    for r in raw:
        text = r["text"]
        if entries and any(c.search(text.strip()) for c in cont):
            continue
        if r["level_field"] is not None:
            level = _level_from_field(r["level_field"], patterns["tags"]) or _classify(text, patterns)
            rest = text
        else:
            level, rest = _level_from_tag(text, patterns["tags"])
            level = level or _classify(rest, patterns)
        entries.append({"n": r["n"], "t": r["t"], "level": level, "text": rest.strip()[:200]})
    if marker is not None:
        cid, rep = marker
        idx = [(i, MARK_RE.search(e["text"])) for i, e in enumerate(entries)]
        s = next((i for i, m in idx if m and m.group(1) == cid and int(m.group(2)) == rep and m.group(3) == "start"), None)
        if s is not None:
            e2 = next((i for i, m in idx if m and m.group(1) == cid and int(m.group(2)) == rep and m.group(3) == "end" and i > s), None)
            entries = entries[s + 1: e2 if e2 is not None else len(entries)]
    entries = [e for e in entries if not (e["level"] != "info" and any(p.search(e["text"]) for p in ign))]
    return attribute_to_probes(entries)
