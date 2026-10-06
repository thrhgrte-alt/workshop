"""Wrappers around existing Luau tools: ``luau-analyze``, ``selene`` and ``stylua --check``.

Rules of this module:

* Availability is detected with ``shutil.which``. A missing tool is REPORTED as missing; it is never pretended to have run.
* Tools run as ``subprocess.run([...], shell=False)`` with a timeout, no stdin, and captured output.
* Output parsing is defensive: unknown lines are ignored, a malformed line never raises, and a run whose output could not be understood is
  reported as such (``status: "unparsed"``) instead of as "no findings".
* The output formats below are written from the author's knowledge of these tools. They were NOT verified against the real tools in this
  repository's test runs (the tests use stub executables); see README "What is verified".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

BACKENDS = ("luau-analyze", "selene", "stylua")
MAX_OUTPUT = 2_000_000


@dataclass
class BackendFinding:
    file: str
    line: int
    col: int
    rule_id: str
    severity: str
    problem: str
    fix: str = ""


@dataclass
class BackendResult:
    name: str
    status: str  # ran | missing | error | timeout | unparsed | not_requested
    findings: list[BackendFinding] = field(default_factory=list)
    path: str | None = None
    exit_code: int | None = None
    message: str = ""
    command: list[str] = field(default_factory=list)
    raw_lines_ignored: int = 0

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "ran": self.status in ("ran",), "path": self.path, "exit_code": self.exit_code, "message": self.message,
                "findings": len(self.findings), "command": self.command, "unrecognised_output_lines": self.raw_lines_ignored}


def available(name: str) -> str | None:
    return shutil.which(name)


# ---------------------------------------------------------------------------------------------------------------------------------
# parsers (pure functions: text in, findings out)
# ---------------------------------------------------------------------------------------------------------------------------------
ANALYZE_RE = re.compile(r"^(?P<file>.+?)\((?P<line>\d+)(?:,\s*(?P<col>\d+))?(?:\s*-\s*\d+(?:,\s*\d+)?)?\):\s*(?P<kind>[A-Za-z_][A-Za-z0-9_]*):\s*(?P<msg>.*)$")


def parse_luau_analyze(text: str) -> tuple[list[BackendFinding], int]:
    """Lines look like ``path(12,5): TypeError: message`` / ``path(3): SyntaxError: message`` / ``path(7,1): LocalUnused: message``.

    SyntaxError and TypeError are errors; any other word is a lint name and is a warning."""
    out, ignored = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = ANALYZE_RE.match(line)
        if not m:
            ignored += 1
            continue
        kind = m["kind"]
        sev = "error" if kind in ("SyntaxError", "TypeError") else "warning"
        out.append(BackendFinding(m["file"], int(m["line"]), int(m["col"] or 0), f"luau-analyze:{kind}", sev, m["msg"].strip()))
    return out, ignored


SELENE_QUIET_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*(?P<sev>error|warning|note|help)\[(?P<code>[^\]]+)\]:\s*(?P<msg>.*)$", re.I)
SEV_MAP = {"error": "error", "warning": "warning", "note": "info", "help": "info"}


def parse_selene(text: str) -> tuple[list[BackendFinding], int]:
    """Accepts ``--display-style=json2`` (one JSON object per line, ``type: Diagnostic``) and the one-line ``quiet`` style
    (``path:line:col: warning[code]: message``). Lines that are neither are ignored."""
    out, ignored = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                ignored += 1
                continue
            if not isinstance(obj, dict):
                ignored += 1
                continue
            if obj.get("type") not in (None, "Diagnostic"):
                continue  # e.g. the trailing Summary object
            label = obj.get("primary_label") or {}
            span = label.get("span") or {}
            fname = label.get("filename") or obj.get("filename")
            if not fname or "start_line" not in span:
                ignored += 1
                continue
            code = str(obj.get("code") or "unknown")
            msg = str(obj.get("message") or "").strip()
            notes = obj.get("notes") or []
            fix = "; ".join(str(n) for n in notes[:2]) if isinstance(notes, list) else ""
            sev = SEV_MAP.get(str(obj.get("severity", "warning")).lower(), "warning")
            out.append(BackendFinding(str(fname), int(span["start_line"]) + 1, int(span.get("start_column", 0)) + 1, f"selene:{code}", sev, msg, fix))
            continue
        m = SELENE_QUIET_RE.match(line)
        if m:
            out.append(BackendFinding(m["file"], int(m["line"]), int(m["col"]), f"selene:{m['code']}", SEV_MAP[m["sev"].lower()], m["msg"].strip()))
        else:
            ignored += 1
    return out, ignored


STYLUA_RE = re.compile(r"^Diff in (?P<file>.+?)(?: at line (?P<line>\d+))?:\s*$")


def parse_stylua_check(text: str, exit_code: int | None, files: list[str]) -> tuple[list[BackendFinding], int]:
    """``stylua --check`` prints ``Diff in <file> at line N:`` followed by a diff for each unformatted region and exits non-zero.

    If the exit code says files are unformatted but no ``Diff in`` line can be recognised, one finding per file is reported at line 1 so the
    problem is not silently dropped."""
    out, ignored = [], 0
    seen_files = set()
    for raw in text.splitlines():
        m = STYLUA_RE.match(raw.strip())
        if m:
            seen_files.add(m["file"])
            out.append(BackendFinding(m["file"], int(m["line"] or 1), 0, "stylua:format", "info", "code is not formatted the way StyLua would format it", "run `stylua <file>`"))
        elif raw.strip() and raw[:1] not in "+- @" and not raw.startswith("\\"):
            ignored += 1
    if exit_code == 1 and not out:
        for f in files:
            out.append(BackendFinding(f, 1, 0, "stylua:format", "info", "StyLua reports this file is not formatted (diff not recognised)", "run `stylua <file>`"))
    return out, ignored


# ---------------------------------------------------------------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------------------------------------------------------------
def build_command(name: str, exe: str, files: list[str]) -> list[str]:
    if name == "luau-analyze":
        return [exe, *files]
    if name == "selene":
        return [exe, "--display-style=json2", *files]
    if name == "stylua":
        return [exe, "--check", *files]
    raise ValueError(f"unknown backend '{name}'. Known: {list(BACKENDS)}")


def _map_file(name: str, files: list[str], cwd: str | None) -> Callable[[str], str | None]:
    by_abs = {os.path.abspath(f): f for f in files}
    by_base: dict[str, list[str]] = {}
    for f in files:
        by_base.setdefault(os.path.basename(f), []).append(f)

    def mapper(reported: str) -> str | None:
        cands = [reported, os.path.abspath(reported)]
        if cwd:
            cands.append(os.path.abspath(os.path.join(cwd, reported)))
        for c in cands:
            if c in by_abs:
                return by_abs[c]
            if c in files:
                return c
        base = by_base.get(os.path.basename(reported))
        return base[0] if base and len(base) == 1 else None

    return mapper


def run_backend(name: str, files: list[str], *, timeout: float = 20.0, cwd: str | None = None, batch: int = 40) -> tuple[BackendResult, dict[str, list[BackendFinding]]]:
    """Run one backend over ``files`` (absolute paths). Returns its status and findings grouped by the given file names."""
    exe = available(name)
    if exe is None:
        return BackendResult(name, "missing", message=f"{name} is not installed (not found on PATH): its checks were NOT run"), {}
    result = BackendResult(name, "ran", path=exe)
    grouped: dict[str, list[BackendFinding]] = {}
    mapper = _map_file(name, files, cwd)
    exit_codes = []
    for k in range(0, len(files), max(1, batch)):
        chunk = files[k:k + batch]
        cmd = build_command(name, exe, chunk)
        result.command = cmd[:3] + (["..."] if len(cmd) > 3 else [])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, shell=False, stdin=subprocess.DEVNULL, errors="replace")
        except subprocess.TimeoutExpired:
            result.status, result.message = "timeout", f"{name} did not finish within {timeout:g}s: its checks are incomplete"
            return result, grouped
        except (OSError, ValueError) as exc:
            result.status, result.message = "error", f"{name} could not be started: {exc}"
            return result, grouped
        exit_codes.append(proc.returncode)
        out = (proc.stdout or "")[:MAX_OUTPUT]
        err = (proc.stderr or "")[:MAX_OUTPUT]
        combined = out + ("\n" + err if err else "")
        if name == "luau-analyze":
            found, ignored = parse_luau_analyze(combined)
        elif name == "selene":
            found, ignored = parse_selene(out + "\n" + err)
        else:
            found, ignored = parse_stylua_check(combined, proc.returncode, chunk)
        result.raw_lines_ignored += ignored
        crashed = proc.returncode not in (0, 1) and not found
        if crashed:
            result.status = "error"
            result.message = f"{name} exited with code {proc.returncode} and produced no recognisable findings: {(err or out).strip()[:200]!r}"
            result.exit_code = proc.returncode
            return result, grouped
        if proc.returncode != 0 and not found and name != "stylua":
            # non-zero exit but nothing we could parse: do not claim the file is clean
            result.status = "unparsed"
            result.message = f"{name} exited with code {proc.returncode} but its output was not understood: {combined.strip()[:200]!r}"
        for f in found:
            target = mapper(f.file)
            if target is None:
                result.raw_lines_ignored += 1
                continue
            grouped.setdefault(target, []).append(BackendFinding(target, f.line, f.col, f.rule_id, f.severity, f.problem, f.fix))
            result.findings.append(f)
    result.exit_code = max(exit_codes) if exit_codes else None
    if result.status == "ran":
        result.message = f"{name} ran on {len(files)} file(s)"
    return result, grouped
