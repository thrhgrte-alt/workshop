"""Telemetry: local output sizes and timings per tool (token discipline made measurable).

Each call of an instrumented tool appends ``{"tool", "at", "chars", "seconds", "ok"}`` to a local JSONL file. Nothing is sent
anywhere; no arguments or results are stored, only sizes and times. :func:`summary` reports per tool the call count, typical
(median) and worst-case output size in characters, an approximate token count (chars / 4, labelled approximate) and timings.
:func:`instrument` wraps :class:`~guide_core.mcpkit.ToolSpec` functions without changing their signature.
"""

from __future__ import annotations

import datetime as _dt
import functools
import json
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .mcpkit import ToolSpec

CHARS_PER_TOKEN = 4  # rough rule of thumb; the report says "approx"


def output_chars(result: Any) -> int:
    """Size of a tool result as the client would receive it (compact JSON, UTF-8 characters)."""
    if isinstance(result, str):
        return len(result)
    return len(json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")))


class Telemetry:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def record(self, tool: str, chars: int, seconds: float, ok: bool = True) -> dict:
        row = {"tool": tool, "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
               "chars": int(chars), "seconds": round(float(seconds), 6), "ok": bool(ok)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)  # keeps the signature and annotations: MCP builds its schema from them
        def timed(*a, **kw):
            t0 = time.perf_counter()
            try:
                res = fn(*a, **kw)
            except Exception:
                self.record(name, 0, time.perf_counter() - t0, ok=False)
                raise
            self.record(name, output_chars(res), time.perf_counter() - t0)
            return res

        return timed

    def instrument(self, specs: Iterable[ToolSpec]) -> list[ToolSpec]:
        return [ToolSpec(s.name, self.wrap(s.name, s.fn), s.description, s.read_only, s.destructive, s.idempotent, s.expensive, s.group) for s in specs]

    def summary(self) -> dict[str, dict]:
        by: dict[str, list[dict]] = {}
        for r in self.rows():
            by.setdefault(r["tool"], []).append(r)
        out = {}
        for tool, rs in sorted(by.items()):
            ok = [r for r in rs if r["ok"]]
            chars = [r["chars"] for r in ok] or [0]
            secs = [r["seconds"] for r in rs]
            out[tool] = {"calls": len(rs), "errors": len(rs) - len(ok), "chars_median": int(statistics.median(chars)), "chars_max": max(chars),
                         "approx_tokens_median": int(statistics.median(chars)) // CHARS_PER_TOKEN, "seconds_median": round(statistics.median(secs), 6),
                         "seconds_max": round(max(secs), 6)}
        return out


def check_budgets(summary: dict[str, dict], budgets: dict[str, int]) -> list[str]:
    """Tools whose median output exceeds a character budget (a regression guard for token discipline)."""
    return [f"{t}: median {summary[t]['chars_median']} chars > budget {b}" for t, b in budgets.items() if t in summary and summary[t]["chars_median"] > b]
