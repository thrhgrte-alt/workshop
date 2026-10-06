"""One call that turns raw hub answers for ONE check into its verdict and report (what ``explain_failure`` and ``record_result`` use)."""

from __future__ import annotations

from typing import Any

from . import evaluate as E
from . import report as RP


def judge(check: dict, cfg: dict, style: dict, results: list, *, console: Any = None, screenshots: list[str] | None = None, baseline: dict | None = None,
          patterns: dict, causes: list[dict], planned: int | None = None, detail: bool = False) -> tuple[dict, dict]:
    if not isinstance(results, list):
        raise ValueError("results must be a list with one raw hub answer per run (or {result, console_log, screenshots} wrappers)")
    if len(results) > 60:
        raise ValueError("results holds more than 60 runs: judge one check at a time, and at most its planned repeats")
    shots = E._screens(screenshots)

    def ctx_for(c):
        return E.Ctx(check, cfg, style, patterns, console=c, baseline=baseline)

    runs = E.read_runs(check, results, ctx_for, shared_console=console, shared_screens=shots)
    agg = E.aggregate(check, cfg, style, runs, planned)
    return agg, RP.build_report(check, agg, style, causes, detail=detail)
