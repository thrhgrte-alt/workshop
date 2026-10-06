"""Run a check end to end on the mock world: generate the script, execute it, read the console, judge the result.

Used by the eval solver, the tests and the README demonstration. It is the proof that the generated Luau is coherent against the mock and that the evaluators catch the planted faults;
it says nothing about real Studio. ``faults_by_run`` lets a flaky check fail in some runs only.
"""

from __future__ import annotations

import copy
from typing import Any

from . import baseline as B
from . import checks as K
from . import console as CON
from . import gen, judge, places, report as RP
from .mockworld import World

CLIENT_COINS = 50


def patch_cfg(cfg: dict, patches: list[dict] | None) -> dict:
    """Small explicit edits for tests: ``{set: "boot.expected_paths", value: [...]}`` (dotted path; list indexes allowed) or ``{delete: "data.test_mode"}``."""
    out = copy.deepcopy(cfg)
    for p in patches or []:
        path = p.get("set") or p.get("delete")
        parts = path.split(".")
        cur: Any = out
        for part in parts[:-1]:
            cur = cur[int(part)] if isinstance(cur, list) else cur.setdefault(part, {})
        last = parts[-1]
        if "set" in p:
            if isinstance(cur, list):
                cur[int(last)] = p["value"]
            else:
                cur[last] = p["value"]
        else:
            cur.pop(last, None)
    return out


def _world(faults, check: dict, *, context: str | None, coins: int | None, test_mode: bool, setup: dict | None, name: str, place_id: int) -> World:
    ctx = context or check["context"]
    w = World(faults, context=ctx, start_coins=CLIENT_COINS if (coins is None and ctx == "client") else (coins or 0), test_mode=test_mode, name=name, place_id=place_id)
    s = setup or {}
    if s.get("add_parts"):
        w.add_parts(int(s["add_parts"]))
    if any(k in s for k in ("memory_mb", "frame_ms", "leak_mb_per_min")):
        w.set_perf(s.get("memory_mb"), s.get("frame_ms"), s.get("leak_mb_per_min"))
    return w


def clean_baseline(project, pctx: places.PlaceCtx, *, minutes: float | None = None, name: str = "default") -> dict:
    """A baseline taken from the CLEAN mock world (what a user would save after a good run)."""
    lib = K.load_library(project.root)
    c = lib["perf_snapshot"]
    probes = gen.load_probes(project.root)
    w = World((), name=pctx.entry.studio_name, place_id=pctx.entry.roblox_place_id or 0)
    mins = minutes if minutes is not None else pctx.cfg.get("performance", {}).get("minutes", K.setting(pctx.style, "perf_default_minutes"))
    s = w.run_json(gen.generate(c, pctx.cfg, pctx.style, pctx.entry, pctx.label, phase="start", probes=probes).luau)
    w.advance(mins * 60)
    e = w.run_json(gen.generate(c, pctx.cfg, pctx.style, pctx.entry, pctx.label, phase="end", probes=probes).luau)
    return B.make_baseline(name, s["measures"], e["measures"], mins, "2026-01-01T00:00:00+00:00", "clean mock world")


def run_world_check(project, check_id: str, *, faults: list[str] | None = None, faults_by_run: list[list[str]] | None = None, place_key: str = "demo_mine/main", context: str | None = None,
                    coins: int | None = None, test_mode: bool = True, repeats: int | None = None, console: str = "text", cfg_patch: list[dict] | None = None, setup: dict | None = None,
                    baseline: dict | str | None = None, minutes: float | None = None, planned: int | None = None, screenshots: list[str] | None = None, detail: bool = False,
                    raw_hook: Any = None, style_patch: dict | None = None) -> dict:
    pid, plid = place_key.split("/")
    pctx = places.resolve(project, pid, plid)
    cfg = patch_cfg(pctx.cfg, cfg_patch)
    style = copy.deepcopy(pctx.style)
    for k, v in ((style_patch or {}).get("settings") or {}).items():
        style["settings"][k]["value"] = v
    for k, v in ((style_patch or {}).get("ranges") or {}).items():
        style["ranges"][k].update(v)
    lib = K.load_library(project.root)
    c = lib[check_id]
    probes = gen.load_probes(project.root)
    n = len(faults_by_run) if faults_by_run else (repeats if repeats is not None else K.repeats_for(c, cfg, style))
    patterns, causes = CON.load_patterns(project.root), RP.load_causes(project.root)
    base = baseline
    if baseline == "clean":
        base = clean_baseline(project, pctx, minutes=minutes)
    results, last_console, scripts = [], None, []
    for i in range(1, n + 1):
        fl = faults_by_run[i - 1] if faults_by_run else (faults or [])
        w = _world(fl, c, context=context, coins=coins, test_mode=test_mode, setup=setup, name=pctx.entry.studio_name, place_id=pctx.entry.roblox_place_id or 0)
        phases = ["start", "end"] if c["kind"] == "perf" else [None]
        for ph in phases:
            s = gen.generate(c, cfg, style, pctx.entry, pctx.label, repeat_no=i, phase=ph, probes=probes)
            scripts.append({"lines": s.lines, "sha256": s.sha256[:12], "lint": s.lint})
            if ph == "end":
                w.advance((minutes if minutes is not None else cfg.get("performance", {}).get("minutes", K.setting(style, "perf_default_minutes"))) * 60)
            try:
                raw = w.run(s.luau)
            except RuntimeError as exc:
                raw = str(exc)
            if raw_hook:
                raw = raw_hook(raw)
            results.append({"result": raw, "console_log": None if console == "none" else (w.console_entries() if console == "entries" else w.console_text()), "screenshots": screenshots or []})
        last_console = results[-1]["console_log"]
    agg, rep = judge.judge(c, cfg, style, results, patterns=patterns, causes=causes, baseline=base if isinstance(base, dict) else None, planned=planned, detail=detail)
    failed = sorted({f"{f['assertion']}" + (f"[{f['subject']}]" if f.get("subject") else "") for f in rep.get("failures", [])})
    return {"verdict": rep["verdict"], "summary": rep["summary"], "report": rep, "failed": failed, "runs": [r["verdict"] for r in agg["runs"]], "scripts": scripts,
            "lint_clean": all(not s["lint"] for s in scripts), "console_last": last_console, "n_results": len(results),
            "raws": [r["result"] for r in results]}
