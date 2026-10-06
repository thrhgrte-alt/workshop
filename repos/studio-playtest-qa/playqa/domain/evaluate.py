"""Evaluate the results of a check: per-run judgement, aggregation over repeats, and the failure report.

Input per run: a ``playqa.result/1`` document (what the generated script returned; format in ``schema.py``), optionally the console text of that run and screenshot paths.
Output per run: assertions (each ``{id, subject, ok, source: script|tool, measured, expected, detail}``), the exact statements of what was checked, and notes. Script assertions are
taken as reported; tool assertions are derived HERE from the measures and the thresholds in force (so a tuned threshold changes the verdict without regenerating the script).

Verdicts: ``pass`` | ``fail`` | ``flaky`` | ``inconclusive`` | ``refused``. A run that could not be read, or a script that raised, is ``inconclusive`` (it says nothing about the game).
A flaky check is judged over its repeats by ``aggregate``: a failure is called only when at least ``flaky_confirm_fail_fraction`` of at least N runs failed.
"""

from __future__ import annotations

import re
from typing import Any

from . import baseline as B
from . import console as CON
from . import reach as R
from .checks import is_flaky, rng, setting
from .schema import as_dict, as_list, extract_result, is_wrong_place_error, validate_result


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    return str(v)


def _a(id_: str, ok: bool | None, source: str, detail: str, measured: Any = None, expected: Any = None, subject: str | None = None, **extra: Any) -> dict:
    row = {"id": id_, "subject": subject, "ok": ok, "source": source, "measured": measured, "expected": expected, "detail": detail}
    row.update({k: v for k, v in extra.items() if v is not None})
    return row


class Ctx:
    """What the evaluators may look at besides the result document."""

    def __init__(self, check: dict, cfg: dict, style: dict, patterns: dict, console: Any = None, baseline: dict | None = None):
        self.check, self.cfg, self.style, self.patterns, self.console, self.baseline = check, cfg, style, patterns, console, baseline
        self.ignore = list(cfg.get("log_ignore") or [])

    def window(self) -> dict | None:
        if self.console is None:
            return None
        return CON.parse(self.console, self.patterns, window_seconds=setting(self.style, "boot_window_seconds"), ignore=self.ignore, max_findings=setting(self.style, "console_max_findings"))

    def marked(self, check_id: str, repeat_no: int) -> dict | None:
        if self.console is None:
            return None
        return CON.parse(self.console, self.patterns, marker=(check_id, repeat_no), ignore=self.ignore, max_findings=setting(self.style, "console_max_findings"))

    def probes(self, check_id: str, repeat_no: int) -> dict[str, list[dict]] | None:
        if self.console is None:
            return None
        return CON.parse_all_probes(self.console, self.patterns, self.ignore, marker=(check_id, repeat_no))


# --- per kind -----------------------------------------------------------------------------------------------------------------------------
def ev_boot(doc: dict, ctx: Ctx, out: dict) -> None:
    m, cfg = as_dict(doc.get("measures")), ctx.cfg.get("boot", {})
    s = ctx.style
    wnd = int(setting(s, "boot_window_seconds")) if float(setting(s, "boot_window_seconds")).is_integer() else setting(s, "boot_window_seconds")
    sv, ps = cfg.get("expected_services", []), cfg.get("expected_paths", [])
    out["checked"].append(f"{len(sv) - len(as_list(m.get('missing_services')))} of {len(sv)} expected services exist ({', '.join(sv) or 'none listed'})")
    out["checked"].append(f"{len(ps) - len(as_list(m.get('missing_paths')))} of {len(ps)} expected paths exist ({', '.join(ps) or 'none listed'})")
    for a in out["assertions"]:
        if a["id"] == "log_service_clean" and a["ok"] is False:
            a["log_lines"] = [str(x)[:200] for x in as_list(m.get("log_errors"))][:3]
    have_log = any(a.get("id") == "log_service_clean" for a in as_list(doc.get("assertions")))
    if have_log:
        out["checked"].append(f"LogService history: {len(as_list(m.get('log_errors')))} error line(s) in the first {_fmt(wnd)} s (secondary evidence)")
    elif m.get("log_service") == "unavailable":
        out["not_evaluated"].append("LogService history was not available to the script")
    win = ctx.window()
    out["_console_used"] = win is not None
    if win is None:
        out["not_evaluated"].append("console: no console_log was supplied (run get_console_output and pass its text); the boot window was NOT read from the console")
        return
    errs, warns = win["errors"], win["warnings"]
    emax, wmax = rng(s, "boot_error_lines")["max"], rng(s, "boot_warning_lines")["max"]
    lines = [f["text"] for f in win["findings"] if f["level"] == "error"][:3]
    wlines = [f["text"] for f in win["findings"] if f["level"] == "warning"][:3]
    scope = (f"first {_fmt(wnd)} s from the first time stamp" if win["window_applied"] else "whole log (no time stamps: the window could not be applied)")
    out["assertions"].append(_a("console_no_errors", errs <= emax, "tool", f"{errs} error line(s) in the {scope}" + (f"; first: {lines[0]}" if lines else ""), errs, f"at most {emax}", log_lines=lines))
    out["assertions"].append(_a("console_no_warnings", warns <= wmax, "tool", f"{warns} warning line(s) in the {scope}" + (f"; first: {wlines[0]}" if wlines else ""), warns, f"at most {wmax}", log_lines=wlines))
    out["checked"].append(f"console: {win['lines_read']} line(s) read, {errs} error(s) and {warns} warning(s) in the {scope}" + (f", {win['ignored']} ignored by log_ignore" if win["ignored"] else ""))
    out["notes"] += [n for n in win["notes"] if n != "schema_unverified"]


def ev_spawn(doc: dict, ctx: Ctx, out: dict) -> None:
    m = as_dict(doc.get("measures"))
    s = ctx.style
    by = {a["id"]: a for a in as_list(doc.get("assertions")) if isinstance(a, dict)}
    pos = m.get("spawn_position")
    if "character_spawned" in by and by["character_spawned"]["ok"] and pos:
        out["checked"].append(f"the character appeared after {_fmt(m.get('waited_for_character', 0))} s at {_fmt(pos)}")
    if "alive" in by and by["alive"]["ok"]:
        out["checked"].append(f"alive ({by['alive']['detail']}) and above the void line {_fmt(setting(s, 'spawn_void_y'))} ({by['above_void']['detail']})" if "above_void" in by else f"alive ({by['alive']['detail']})")
    fl = as_dict(m.get("floor"))
    if fl and "floor_valid" in by and by["floor_valid"]["ok"]:
        out["checked"].append(f"standing on {fl.get('path')} (not on the invalid list)")
    pr = as_dict(m.get("probe"))
    if pr:
        need = rng(s, "spawn_min_move_fraction")["min"] * pr["distance"]
        best = pr.get("best_moved", 0)
        ok = best + 1e-9 >= need
        out["assertions"].append(_a("not_stuck", ok, "tool", f"the best of {len(as_list(pr.get('tried')))} MoveTo probe(s) covered {_fmt(best)} of {_fmt(pr['distance'])} studs in {_fmt(pr['seconds'])} s (needs {_fmt(need)})",
                                    best, f"at least {_fmt(need)} studs"))
        if ok:
            out["checked"].append(f"not stuck: moved {_fmt(best)} of {_fmt(pr['distance'])} studs (needs {_fmt(need)})")
    else:
        out["not_evaluated"].append("not_stuck: no MoveTo probe ran (the character did not spawn)")


def ev_reach(doc: dict, ctx: Ctx, out: dict) -> None:
    m, s = as_dict(doc.get("measures")), ctx.style
    areas = [a["name"] for a in as_list(m.get("areas")) if isinstance(a, dict) and a.get("position") is not None]
    edges = [e for e in as_list(m.get("paths")) if isinstance(e, dict)]
    if not edges and not areas:
        out["not_evaluated"].append("no pathfinding results to flood-fill (the spawn or every area was not found)")
        return
    minwp, maxgap = int(rng(s, "reach_min_waypoints")["min"]), rng(s, "reach_end_gap_studs")["max"]
    fl = R.flood(areas, edges, min_waypoints=minwp)
    hop = "with hops through other areas" if m.get("hops") else "direct paths from the spawn only"
    for a in areas:
        if a in fl["reached"]:
            info = fl["reached"][a]
            chain = " -> ".join(R.route(fl["reached"], a))
            e = info["edge"]
            out["assertions"].append(_a("area_reachable", True, "tool", f"reached via {chain} ({e['waypoints']} waypoints, {_fmt(e['length'])} studs)", info["hops"], "reachable", subject=a))
            gap = e["end_gap"]
            out["assertions"].append(_a("path_ends_at_target", 0 <= gap <= maxgap, "tool", f"the last waypoint is {_fmt(gap)} studs from the target (allowed {_fmt(maxgap)})", gap, f"at most {_fmt(maxgap)} studs",
                                        subject=a, statuses=[e["status"]]))
        else:
            att = fl["attempts"].get(a, [])
            why = "; ".join(f"{e['from']}->{e['to']}: {R.usable(e, minwp)[1]}" for e in att[:4]) or "no pathfinding attempt reached it"
            out["assertions"].append(_a("area_reachable", False, "tool", f"not reached from the spawn ({why})", "unreachable", "reachable", subject=a, statuses=sorted({e["status"] for e in att})))
    ok_n = len(fl["reached"])
    out["checked"].append(f"flood fill from the spawn over {len(edges)} pathfinding result(s), {hop}: {ok_n} of {len(areas)} named area(s) reached ({', '.join(areas)}); usable = Success with at least {minwp} waypoints")
    reached_gap_ok = sum(1 for a in areas if a in fl["reached"] and 0 <= fl["reached"][a]["edge"]["end_gap"] <= maxgap)
    if ok_n:
        out["checked"].append(f"{reached_gap_ok} of {ok_n} reached area(s) end within {_fmt(maxgap)} studs of the target point")
    if m.get("unresolved"):
        out["notes"].append("areas not found in the place: " + ", ".join(as_list(m["unresolved"])))


def _probe_lines(probes: dict | None, key: str) -> list[str]:
    return [e["text"] for e in (probes or {}).get(key, [])][:3]


def ev_remotes(doc: dict, ctx: Ctx, out: dict) -> None:
    m = as_dict(doc.get("measures"))
    probes = ctx.probes(doc.get("check_id", "remotes"), int(doc.get("repeat_no") or 1))
    out["_console_used"] = probes is not None
    if probes is None:
        out["not_evaluated"].append("console: no console_log was supplied, so errors a handler logged on the server are NOT seen; only what the calls returned or raised was read")
    for r in as_list(m.get("remotes")):
        rid, cls = r.get("id"), r.get("class")
        if not r.get("class_ok"):
            continue
        valid, bad = [x for x in as_list(r.get("valid")) if isinstance(x, dict)], [x for x in as_list(r.get("bad")) if isinstance(x, dict)]
        if cls == "RemoteFunction":
            hung = [v["label"] for v in valid if v.get("timed_out")]
            out["assertions"].append(_a("valid_call_responds", not hung, "tool", f"{rid}: no answer within {_fmt(m.get('timeout'))} s for {', '.join(hung)}" if hung else f"{rid}: {len(valid)} valid call(s) answered or raised within {_fmt(m.get('timeout'))} s",
                                        hung or len(valid), "answers in time", subject=rid))
        verr = [v for v in valid if not v.get("ok") or _probe_lines(probes, f"{rid}/{v['label']}")]
        vlines = [l for v in valid for l in _probe_lines(probes, f"{rid}/{v['label']}")][:3]
        if verr:
            first = next((v.get("error") for v in verr if v.get("error")), None) or (vlines[0] if vlines else "error line in the console")
            out["assertions"].append(_a("valid_call_no_error", False, "tool", f"{rid}: a valid call raised or logged an error: {first}", [v["label"] for v in verr], "no error", subject=rid, log_lines=vlines))
        else:
            out["assertions"].append(_a("valid_call_no_error", True, "tool", f"{rid}: {len(valid)} valid call(s) raised no error", len(valid), "no error", subject=rid))
        if cls == "RemoteFunction":
            accepted = [b["label"] for b in bad if b.get("responded") and not b.get("rejected")]
            out["assertions"].append(_a("bad_input_rejected", not accepted, "tool", f"{rid}: {len(bad) - len(accepted)} of {len(bad)} bad input(s) rejected" + (f"; accepted: {', '.join(accepted)}" if accepted else ""),
                                        accepted or len(bad), "all rejected", subject=rid))
        berr = [b for b in bad if b.get("error") or _probe_lines(probes, f"{rid}/{b['label']}")]
        blines = [l for b in berr for l in _probe_lines(probes, f"{rid}/{b['label']}")][:3]
        if berr:
            first = next((b.get("error") for b in berr if b.get("error")), None) or (blines[0] if blines else "error line in the console")
            out["assertions"].append(_a("bad_input_no_error", False, "tool", f"{rid}: {len(berr)} of {len(bad)} bad input(s) raised or logged an error ({', '.join(b['label'] for b in berr[:4])}): {first}",
                                        [b["label"] for b in berr], "no error", subject=rid, log_lines=blines))
        else:
            out["assertions"].append(_a("bad_input_no_error", True, "tool", f"{rid}: {len(bad)} bad input(s) raised no error", len(bad), "no error", subject=rid))
    names = [f"{r.get('id')} ({r.get('class')}, {len(as_list(r.get('valid')))} valid + {len(as_list(r.get('bad')))} bad)" for r in as_list(m.get("remotes")) if r.get("class_ok")]
    if names:
        out["checked"].append("remotes probed: " + "; ".join(names))


def _expect_text(e: dict) -> str:
    if "delta" in e:
        return f"{e['delta']:+g}"
    if "sign" in e:
        return e["sign"]
    if "range" in e:
        return f"range {e['range']}"
    return f"{e.get('delta_min', '-inf')}..{e.get('delta_max', 'inf')}"


def ev_economy(doc: dict, ctx: Ctx, out: dict) -> None:
    m, s = as_dict(doc.get("measures")), ctx.style
    steps = {st["id"]: st for st in as_list(m.get("steps")) if isinstance(st, dict) and "id" in st}
    tol, cap, floor = setting(s, "economy_delta_tolerance"), rng(s, "economy_max_abs_delta")["max"], rng(s, "economy_min_balance")["min"]
    currency = {k for k, v in ctx.cfg.get("economy", {}).get("player_values", {}).items() if v.get("currency")}
    probes = ctx.probes(doc.get("check_id", "economy_smoke"), int(doc.get("repeat_no") or 1))
    out["_console_used"] = probes is not None
    parts, negative, big = [], [], []
    for st in ctx.cfg.get("economy", {}).get("steps", []):
        sid = st["id"]
        run = steps.get(sid)
        if run is None:
            out["not_evaluated"].append(f"step {sid}: did not run")
            continue
        before, after = as_dict(run.get("before")), as_dict(run.get("after"))
        lines = _probe_lines(probes, f"economy/{sid}")
        bits = []
        for vid, e in st["expect"].items():
            if vid not in before or vid not in after:
                out["assertions"].append(_a("delta_as_expected", False, "tool", f"{sid}: value '{vid}' was not read", None, _expect_text(e), subject=f"{sid}.{vid}"))
                continue
            d = after[vid] - before[vid]
            if "delta" in e:
                ok, ratio = abs(d - e["delta"]) <= tol + 1e-9, (None if e["delta"] == 0 else d / e["delta"])
            elif "sign" in e:
                ok, ratio = {"positive": d > 0, "negative": d < 0, "zero": d == 0}[e["sign"]], None
            elif "range" in e:
                rr = rng(s, f"economy_expect_{e['range']}")
                ok, ratio = rr["min"] - 1e-9 <= d <= rr["max"] + 1e-9, None
                e = {**e, "_text": f"range {e['range']} ({_fmt(rr['min'])}..{_fmt(rr['max'])}, PLACEHOLDER)"}
            else:
                ok, ratio = e.get("delta_min", float("-inf")) - 1e-9 <= d <= e.get("delta_max", float("inf")) + 1e-9, None
            bits.append(f"{vid} {d:+g}")
            out["assertions"].append(_a("delta_as_expected", ok, "tool", f"{sid}: {vid} went {_fmt(before[vid])} -> {_fmt(after[vid])} (change {d:+g}, expected {e.get('_text') or _expect_text(e)})", d, e.get("_text") or _expect_text(e), subject=f"{sid}.{vid}",
                                        ratio=None if ratio is None else round(ratio, 4), expected_zero=True if ("delta" in e and e["delta"] == 0 and not ok) else None, log_lines=lines if not ok else None))
        for vid in set(before) | set(after):
            if vid in after and abs(after[vid] - before.get(vid, after[vid])) > cap:
                big.append(f"{sid}.{vid}")
            if vid in currency and vid in after and after[vid] < floor:
                negative.append(f"{sid}.{vid}")
        parts.append(f"{sid}: " + ", ".join(bits))
    out["assertions"].append(_a("balance_not_negative", not negative, "tool", "no watched currency below " + _fmt(floor) + " after any step" if not negative else "below " + _fmt(floor) + " after: " + ", ".join(negative),
                                negative or 0, f"at least {_fmt(floor)}", subject=None))
    out["assertions"].append(_a("delta_within_cap", not big, "tool", f"no step moved a value by more than {_fmt(cap)}" if not big else "beyond the cap: " + ", ".join(big), big or 0, f"at most {_fmt(cap)}"))
    if parts:
        out["checked"].append("steps (change as measured): " + "; ".join(parts))
    out["checked"].append(f"exact deltas within +/-{_fmt(tol)}; no currency below {_fmt(floor)}; no step beyond {_fmt(cap)} (PLACEHOLDER limits)")


def ev_data(doc: dict, ctx: Ctx, out: dict) -> None:
    m = as_dict(doc.get("measures"))
    rows = [r for r in as_list(m.get("values")) if isinstance(r, dict)]
    if rows:
        out["checked"].append("TEST MODE round trip (save, reset, load): " + "; ".join(f"{r['id']} marker {_fmt(r.get('marker'))} -> after load {_fmt(r.get('after'))}" for r in rows))
    out["checked"].append("the switch is on and (when configured) the game acknowledged it before any hook was called; the script contains no DataStore call")


def ev_perf(docs: dict, ctx: Ctx, out: dict) -> None:
    """``docs`` = {"start": result doc, "end": result doc} (either may be missing)."""
    s = ctx.style
    st = as_dict(as_dict(docs.get("start")).get("measures"))
    en = as_dict(as_dict(docs.get("end")).get("measures"))
    if not st or not en:
        out["not_evaluated"].append("a performance run needs BOTH the start and the end snapshot (execute the 'start' script, wait N minutes, execute the 'end' script)")
        out["_incomplete"] = True
        return
    g = B.growth(st, en, s["ranges"])
    out["checked"].append("start snapshot: " + ", ".join(f"{k}={_fmt(v)}" for k, v in B.snapshot(st).items()) + "; end snapshot: " + ", ".join(f"{k}={_fmt(v)}" for k, v in B.snapshot(en).items()))
    if g["ok"] is not None:
        out["assertions"].append(_a("growth_over_run", g["ok"], "tool", f"memory {_fmt(g['start'])} -> {_fmt(g['end'])} MB between the snapshots ({_fmt(g['pct'])}%, allowed {_fmt(g['limit_pct'])}%)" if g["pct"] is not None else g["note"],
                                    g["pct"], f"at most {_fmt(g['limit_pct'])} %"))
        out["checked"].append(f"memory growth over the run {_fmt(g['pct'])}% (allowed {_fmt(g['limit_pct'])}%)")
    else:
        out["not_evaluated"].append("growth_over_run: " + g["note"])
    if ctx.baseline is None:
        out["not_evaluated"].append("within_baseline: no stored baseline: nothing was compared (save_baseline keeps this run as the baseline)")
        out["_no_baseline"] = True
        return
    rows = B.compare(ctx.baseline, st, en, s["ranges"])
    for r in rows:
        if r["ok"] is None:
            out["not_evaluated"].append(f"{r['metric']}@{r['phase']}: {r['note']}")
            continue
        out["assertions"].append(_a("within_baseline", r["ok"], "tool", f"{r['metric']}@{r['phase']}: {_fmt(r['baseline'])} -> {_fmt(r['current'])} ({'n/a' if r['pct'] is None else _fmt(r['pct']) + '%'}, allowed {_fmt(r['limit_pct'])}%)"
                                    + (f" {r['note']}" if r["note"] else ""), r["pct"], f"at most {_fmt(r['limit_pct'])} %", subject=f"{r['metric']}@{r['phase']}"))
    okc = sum(1 for r in rows if r["ok"])
    out["checked"].append(f"{okc} of {sum(1 for r in rows if r['ok'] is not None)} comparable measure(s) within their allowed increase over baseline '{ctx.baseline.get('name')}' (taken {ctx.baseline.get('taken_at')})")


EVALUATORS = {"boot": ev_boot, "spawn": ev_spawn, "reachability": ev_reach, "remotes": ev_remotes, "economy": ev_economy, "data": ev_data}


def _new_outcome(repeat: int) -> dict:
    return {"repeat": repeat, "verdict": "pass", "assertions": [], "checked": [], "not_evaluated": [], "notes": [], "refused": None, "script_error": None, "measures": {}}


def _screens(x: Any) -> list[str]:
    if x is None:
        return []
    items = [x] if isinstance(x, str) else list(x)
    out = []
    for p in items:
        if not isinstance(p, str) or not p.strip() or len(p) > 300 or any(ord(c) < 32 for c in p):
            raise ValueError("a screenshot path must be a printable string of at most 300 characters")
        out.append(p.strip())
    return out


def unwrap_run(item: Any) -> tuple[Any, Any, list[str]]:
    """One element of ``results``: the raw hub answer, or ``{"result": raw, "console_log": ..., "screenshots": [...]}``."""
    if isinstance(item, dict) and "result" in item and item.get("schema") is None and set(item) <= {"result", "console_log", "screenshots", "repeat"}:
        return item["result"], item.get("console_log"), _screens(item.get("screenshots"))
    return item, None, []


def evaluate_run(check: dict, docs: list[dict] | dict, ctx: Ctx, repeat: int) -> dict:
    """Judge one run. ``docs`` is the single result document (or ``{"start":..,"end":..}`` for a performance run)."""
    out = _new_outcome(repeat)
    kind = check["kind"]
    primary = docs if kind != "perf" else None
    if kind == "perf":
        ds = [d for d in docs.values() if d]
        out["measures"] = {ph: as_dict(as_dict(d).get("measures")) for ph, d in docs.items() if d}
    else:
        ds = [docs]
        out["measures"] = as_dict(docs.get("measures"))
    for d in ds:
        for a in as_list(d.get("assertions")):
            if isinstance(a, dict) and "id" in a and "ok" in a:
                row = {"id": a["id"], "subject": a.get("subject"), "ok": bool(a["ok"]), "source": "script", "measured": a.get("measured"), "expected": a.get("expected"), "detail": a.get("detail", "")}
                if kind == "perf":
                    row["subject"] = a.get("subject") or as_dict(d.get("measures")).get("phase")
                out["assertions"].append(row)
        if d.get("refused"):
            out["refused"] = str(d["refused"])
    if out["refused"]:
        out["verdict"] = "refused"
        out["checked"].append("nothing was checked: the script declined to run")
        return out
    if kind == "perf":
        ev_perf(docs, ctx, out)
    else:
        EVALUATORS[kind](primary, ctx, out)
    for a in out["assertions"]:
        if a["source"] == "script" and a["ok"]:
            out["checked"].append(f"{a['id']}{'[' + a['subject'] + ']' if a.get('subject') else ''}: {a['detail']}")
    failed = [a for a in out["assertions"] if a["ok"] is False]
    out["verdict"] = "fail" if failed else "pass"
    if kind == "boot" and not failed and not out.pop("_console_used", False) and not any(a["id"] == "log_service_clean" for a in out["assertions"]):
        out["verdict"] = "inconclusive"
        out["notes"].append("no console and no LogService evidence about errors: a boot check cannot pass on folder existence alone; pass the console text as console_log")
    if kind == "perf":
        if out.pop("_incomplete", False):
            out["verdict"] = "inconclusive"
        elif out.pop("_no_baseline", False) and out["verdict"] == "pass":
            out["verdict"] = "inconclusive"
            out["notes"].append("only the growth over the run was judged; there is no baseline to compare against")
    out.pop("_console_used", None)
    return out


def read_runs(check: dict, results: list, ctx_for: Any, shared_console: Any = None, shared_screens: list[str] | None = None) -> list[dict]:
    """Parse and judge every element of ``results``. ``ctx_for(console)`` builds a Ctx. Performance results are paired by ``repeat_no`` (start and end)."""
    kind = check["kind"]
    parsed: list[dict] = []
    for i, item in enumerate(results, start=1):
        raw, console, screens = unwrap_run(item)
        doc, err, notes = extract_result(raw)
        parsed.append({"i": i, "doc": doc, "err": err, "console": console if console is not None else shared_console, "screens": screens or list(shared_screens or []), "notes": notes})
    runs: list[dict] = []
    if kind == "perf":
        groups: dict[int, dict] = {}
        for p in parsed:
            if p["doc"] is None:
                runs.append({**_new_outcome(p["i"]), "verdict": "inconclusive", "script_error": p["err"], "screenshots": p["screens"]})
                continue
            phase = as_dict(p["doc"].get("measures")).get("phase") or "start"
            g = groups.setdefault(int(p["doc"].get("repeat_no") or p["i"]), {"docs": {}, "console": p["console"], "screens": []})
            g["docs"][phase] = p["doc"]
            g["screens"] += p["screens"]
        for rep, g in sorted(groups.items()):
            runs.append({**evaluate_run(check, g["docs"], ctx_for(g["console"]), rep), "screenshots": g["screens"]})
        return runs
    for p in parsed:
        doc = p["doc"]
        if doc is None:
            o = _new_outcome(p["i"])
            if is_wrong_place_error(p["err"]):
                o.update(verdict="refused", refused=f"the script's place guard stopped it: {p['err']}")
                o["checked"].append("nothing was checked: the script declined to run")
            else:
                o.update(verdict="inconclusive", script_error=p["err"])
                o["notes"].append("the script raised or its answer could not be read: this says nothing about the game")
            o["screenshots"] = p["screens"]
            runs.append(o)
            continue
        problems = validate_result(doc)
        if problems or doc.get("check_id") != check["id"] or doc.get("kind") != check["kind"]:
            o = _new_outcome(p["i"])
            o.update(verdict="inconclusive", script_error="unreadable or mismatched result: " + "; ".join(problems or [f"result is for check {doc.get('check_id')!r}/{doc.get('kind')!r}, not {check['id']!r}/{check['kind']!r}"]))
            o["screenshots"] = p["screens"]
            runs.append(o)
            continue
        o = evaluate_run(check, doc, ctx_for(p["console"]), int(doc.get("repeat_no") or p["i"]))
        o["screenshots"] = p["screens"]
        runs.append(o)
    return runs


# --- aggregation over repeats -------------------------------------------------------------------------------------------------------------
def aggregate(check: dict, cfg: dict, style: dict, runs: list[dict], planned: int | None = None) -> dict:
    """The check's verdict over its repeats, with the repeat statistics.

    non-flaky: any conclusive failing run fails the check. flaky (N = ``planned``, default flaky_repeats): all conclusive runs pass -> pass; failures with fewer than N conclusive
    runs -> inconclusive (re-run first); with N or more, ``fail`` when the failing share is at least flaky_confirm_fail_fraction, otherwise ``flaky``.
    """
    flaky = is_flaky(check, cfg)
    need = int(planned if planned is not None else (setting(style, "flaky_repeats") if flaky else 1))
    confirm = setting(style, "flaky_confirm_fail_fraction")
    refused = [r for r in runs if r["verdict"] == "refused"]
    conclusive = [r for r in runs if r["verdict"] in ("pass", "fail")]
    failed = [r for r in conclusive if r["verdict"] == "fail"]
    stats = {"planned": need, "ran": len(runs), "conclusive": len(conclusive), "failed": len(failed), "passed": len(conclusive) - len(failed), "flaky_check": flaky}
    notes: list[str] = []
    if not runs:
        return {"verdict": "inconclusive", "repeats": stats, "notes": ["no results were supplied"], "runs": runs}
    if refused:
        verdict = "refused"
    elif not conclusive:
        verdict = "inconclusive"
        why = next((r["notes"][0] for r in runs if r["notes"]), None) or next((str(r["script_error"]) for r in runs if r.get("script_error")), None) or next((r["not_evaluated"][0] for r in runs if r["not_evaluated"]), None)
        notes.append(why or "no run produced a readable result")
    elif not failed:
        verdict = "pass"
        if len(conclusive) < need:
            notes.append(f"ran {len(conclusive)} of {need} planned repeat(s); all passed")
    elif not flaky:
        verdict = "fail"
    elif len(conclusive) < need:
        verdict = "inconclusive"
        notes.append(f"{len(failed)} of {len(conclusive)} run(s) failed, but this check is flaky: re-run it until {need} run(s) exist before calling a failure")
    elif len(failed) / len(conclusive) + 1e-9 >= confirm:
        verdict = "fail"
        notes.append(f"failed in {len(failed)} of {len(conclusive)} runs (confirmed at {confirm:g} of runs)")
    else:
        verdict = "flaky"
        notes.append(f"failed in {len(failed)} of {len(conclusive)} runs: intermittent, not confirmed as a failure")
    inconclusive_runs = [r for r in runs if r["verdict"] == "inconclusive"]
    if inconclusive_runs and conclusive:
        notes.append(f"{len(inconclusive_runs)} run(s) were unreadable or inconclusive and are not counted")
    return {"verdict": verdict, "repeats": stats, "notes": notes, "runs": runs}
