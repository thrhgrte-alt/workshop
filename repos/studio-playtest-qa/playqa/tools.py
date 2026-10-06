"""MCP tools for playtest QA.

Rules every tool follows: it needs ``project_id`` AND ``place_id`` (refuses without them or with an unregistered pair, never guesses); the answer starts with a one-line ``summary``
and states the project and place; findings are ranked and capped (default 10), details only with ``detail=true``; write tools default to ``dry_run=true``. A tool that generates a
script or plans a run also needs ``studios`` (the output of the hub's ``list_roblox_studios``) and refuses unless EXACTLY ONE open place is the named one. This server never
connects to Studio: it emits Luau and a run plan that the hub executes, and parses what comes back (every hub-output parser is ``schema_unverified``). Tools whose group is ``rare``
can be left disabled (``PLAYQA_DISABLE_GROUPS=rare``).
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

from . import learning_params as LP
from .domain import baseline as B
from .domain import checks as K
from .domain import console as CON
from .domain import gen, judge, places, plan as PL, report as RP, store
from .domain.schema import SCHEMA_STATUS, extract_result, validate_result, as_dict
from .guide_adapter import ToolSpec, config, dryrun, feedback, mock, observe, scope as S, telemetry

RARE = ("compare_baseline", "save_baseline", "promote_run")


def make_tools(project) -> list[ToolSpec]:
    def lib() -> dict:
        return K.load_library(project.root)

    def check_of(check_id: str) -> dict:
        L = lib()
        if check_id not in L:
            raise ValueError(f"unknown check '{check_id}'. Known: {sorted(L)}")
        return L[check_id]

    def bname(name: str | None, ctx: places.PlaceCtx) -> str:
        n = name or ctx.cfg.get("performance", {}).get("baseline", "default")
        if not isinstance(n, str) or not gen._ID.fullmatch(n):
            raise ValueError("baseline_name must match [A-Za-z][A-Za-z0-9_]{0,39}")
        return n

    def causes() -> list[dict]:
        return RP.load_causes(project.root)

    def patterns() -> dict:
        return CON.load_patterns(project.root)

    def need_configured(ctx: places.PlaceCtx, c: dict) -> None:
        if not K.configured(c, ctx.cfg):
            raise ValueError(f"check '{c['id']}' is not configured for {ctx.label}: add a '{c['config_section']}' section to {ctx.config_path.name}. Nothing was done.")

    def known_flags(ctx, c):
        return K.check_flags(c, ctx.cfg)

    # --- read-only -------------------------------------------------------------------------------------------------------------------
    def list_checks(project_id: str | None = None, place_id: str | None = None, detail: bool = False) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        rows, can, flaky = [], 0, 0
        for c in lib().values():
            fl = known_flags(ctx, c)
            ok, why = K.configured(c, ctx.cfg), ""
            if not ok:
                why = f"no '{c['config_section']}' section in playtest.yaml"
            elif c.get("requires_test_mode"):
                try:
                    gen.require_data_config(ctx.cfg)
                except ValueError as exc:
                    ok, why = False, str(exc).replace("refusing to generate a data check: ", "refused: ")[:160]
            row: dict[str, Any] = {"id": c["id"], "kind": c["kind"], "runnable": ok, "flaky": fl["flaky"], "session": c["session"], "context": c["context"]}
            if ok:
                can += 1
                flaky += 1 if fl["flaky"] else 0
                row["runs"] = K.repeats_for(c, ctx.cfg, ctx.style)
            else:
                row["why_not"] = why
            if detail:
                row.update(title=c["title"], flaky_why=fl["why"], assertions=[a["id"] for a in c["assertions"]], not_checked=c["not_checked"], verify_against_current_docs=bool(c.get("verify_against_current_docs")))
            rows.append(row)
        n = int(K.setting(ctx.style, "flaky_repeats"))
        return places.head(ctx, f"{len(rows)} checks in the library; {can} can run for {ctx.label} ({flaky} flaky: re-run {n}x before a failure is called). Every threshold is a PLACEHOLDER; hub-output parsers are {SCHEMA_STATUS}.",
                           checks=rows, thresholds_are_placeholders=bool(ctx.style.get("placeholder")), input_status=SCHEMA_STATUS)

    def plan_playtest(project_id: str | None = None, place_id: str | None = None, studios: list | None = None, check_ids: list[str] | None = None, repeats: int | None = None,
                      minutes: float | None = None, detail: bool = False) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id, studios=studios, need_studio=True)
        L = lib()
        selected, skipped = PL.select(L, ctx.cfg, check_ids)
        if not selected:
            raise ValueError(f"nothing to plan for {ctx.label}: skipped {skipped}")
        if repeats is not None and (not isinstance(repeats, int) or isinstance(repeats, bool) or not 1 <= repeats <= 10):
            raise ValueError("repeats must be a whole number from 1 to 10")
        if minutes is not None and (isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 120):
            raise ValueError("minutes must be a number from 1 to 120")
        probes = gen.load_probes(project.root)
        hashes = {}
        for key in PL.hash_keys(selected, ctx.cfg, ctx.style, repeats):
            cid, rep, phase = key
            hashes[key] = gen.generate(L[cid], ctx.cfg, ctx.style, ctx.entry, ctx.label, repeat_no=rep, phase=phase, probes=probes).sha256
        built = PL.build(selected, ctx.cfg, ctx.style, repeats=repeats, minutes=minutes, hashes=hashes)
        runs = sum(1 for s in built["sessions"] for st in s["steps"] if st.startswith("execute_luau"))
        flaky = {c["id"]: K.repeats_for(c, ctx.cfg, ctx.style, repeats) for c in selected if K.is_flaky(c, ctx.cfg)}
        mins = sum((minutes if minutes is not None else ctx.cfg.get("performance", {}).get("minutes", K.setting(ctx.style, "perf_default_minutes"))) * K.repeats_for(c, ctx.cfg, ctx.style, repeats)
                   for c in selected if c["kind"] == "perf")
        warn = ["run it ONLY in the open Studio session on this place, never against a live server",
                "the hub tools' argument names are schema_unverified: read their schemas; the plan gives intent and order"]
        if mins:
            warn.append(f"the performance check waits {mins:g} minutes of play in total")
        if any(c["kind"] == "data" for c in selected):
            warn.append("data_roundtrip: turn the test-mode switch ON in the Studio session first (never in the published place); the script declines to run without it")
        res = places.head(ctx, f"plan: {len(selected)} check(s), {runs} script run(s) in {len(built['sessions'])} play session(s) for {ctx.label}; open Studio matched by {ctx.studio['matched_by']}. "
                          f"Flaky checks ({', '.join(f'{k} x{v}' for k, v in flaky.items()) or 'none'}) get their runs before any failure is called.",
                          studio={"name": ctx.studio.get("name"), "studio_id": ctx.studio.get("studio_id"), "matched_by": ctx.studio["matched_by"], "input_schema": ctx.studio.get("input_schema")},
                          sessions=built["sessions"], skipped=skipped or None,
                          then="per check: explain_failure(check_id, results=[raw answers, one per run], console_log, screenshots); then record_result(dry_run=false)",
                          script="generate_check_script(check_id, repeat_no, phase, studios, dry_run=false) returns the luau for each execute_luau step (sha256 prefixes under detail)",
                          warnings=warn, input_status=SCHEMA_STATUS)
        if detail:
            res["sha256_prefix"] = {f"{k[0]}#{k[1]}" + (f"/{k[2]}" if k[2] else ""): v[:10] for k, v in hashes.items()}
            res["hub_calls"] = PL.HUB_CALLS
            res["rule"] = "a flaky check failing in fewer than N conclusive runs is inconclusive; at N or more it is FAIL when the failing share reaches flaky_confirm_fail_fraction, otherwise FLAKY"
        return res

    def parse_console_log(project_id: str | None = None, place_id: str | None = None, console_log: Any = None, window_seconds: float | None = None, check_id: str | None = None,
                          repeat_no: int = 1, detail: bool = False) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if console_log is None:
            raise ValueError("console_log is required: the text (or list of lines or entries) that the hub's get_console_output returned")
        if window_seconds is not None and (isinstance(window_seconds, bool) or not isinstance(window_seconds, (int, float)) or window_seconds <= 0):
            raise ValueError("window_seconds must be a positive number")
        marker = None
        if check_id is not None:
            check_of(check_id)
            marker = (check_id, int(repeat_no))
        if window_seconds is None and check_id == "boot":
            window_seconds = K.setting(ctx.style, "boot_window_seconds")
        cap = int(K.setting(ctx.style, "console_max_findings"))
        res = CON.parse(console_log, patterns(), window_seconds=window_seconds, marker=marker, ignore=list(ctx.cfg.get("log_ignore") or []), max_findings=cap, detail=detail)
        res.pop("_entries", None)
        scope_txt = (f"first {window_seconds:g} s" if res["window_applied"] else "whole log") if window_seconds else "whole log"
        line = (f"console: {res['lines_read']} line(s) read, {res['errors']} error(s) and {res['warnings']} warning(s) in the {scope_txt}"
                + (f", {res['ignored']} ignored by log_ignore" if res["ignored"] else "") + f"; format is {SCHEMA_STATUS}")
        return places.head(ctx, line, **{k: v for k, v in res.items() if k not in ("notes",)}, notes=res["notes"])

    def _perf_pair(results: list, check: dict) -> tuple[dict | None, dict | None, list[str]]:
        start = end = None
        problems: list[str] = []
        for item in results:
            raw = item["result"] if isinstance(item, dict) and "result" in item and item.get("schema") is None else item
            doc, err, _ = extract_result(raw)
            if doc is None:
                problems.append(f"unreadable answer: {str(err)[:120]}")
                continue
            if validate_result(doc) or doc.get("kind") != "perf":
                problems.append("a result is not a performance snapshot")
                continue
            ph = as_dict(doc.get("measures")).get("phase")
            if ph == "start":
                start = as_dict(doc["measures"])
            elif ph == "end":
                end = as_dict(doc["measures"])
        return start, end, problems

    def compare_baseline(project_id: str | None = None, place_id: str | None = None, results: list | None = None, baseline_name: str | None = None, detail: bool = False) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if not isinstance(results, list) or not results:
            raise ValueError("results is required: the raw hub answers of the performance script's 'start' and 'end' runs")
        name = bname(baseline_name, ctx)
        start, end, problems = _perf_pair(results, lib()["perf_snapshot"])
        if start is None or end is None:
            raise ValueError("need both a 'start' and an 'end' performance snapshot in results" + (f" ({'; '.join(problems)})" if problems else ""))
        base = store.load_baseline(project, ctx.scope, name)
        g = B.growth(start, end, ctx.style["ranges"])
        if base is None:
            return places.head(ctx, f"no stored baseline '{name}' for {ctx.label}: nothing was compared (the run's own memory growth is {g['pct']}%, allowed {g['limit_pct']}%). save_baseline keeps this run as the baseline.",
                               baseline=None, growth=g, snapshot={"start": B.snapshot(start), "end": B.snapshot(end)}, input_status=SCHEMA_STATUS)
        rows = B.compare(base, start, end, ctx.style["ranges"])
        bad = [r for r in rows if r["ok"] is False]
        shown = rows if detail else bad[: int(K.setting(ctx.style, "console_max_findings"))]
        line = (f"{len(bad)} of {sum(1 for r in rows if r['ok'] is not None)} comparable measure(s) above the allowed increase over baseline '{name}'" if bad
                else f"all {sum(1 for r in rows if r['ok'] is not None)} comparable measure(s) within the allowed increase over baseline '{name}'")
        return places.head(ctx, line + f"; memory growth over the run {g['pct']}% (allowed {g['limit_pct']}%). Limits are PLACEHOLDERS.", baseline={"name": name, "taken_at": base["taken_at"], "minutes": base["minutes"]},
                           rows=shown, growth=g, input_status=SCHEMA_STATUS)

    def explain_failure(project_id: str | None = None, place_id: str | None = None, check_id: str | None = None, results: list | None = None, console_log: Any = None,
                        screenshots: list[str] | None = None, baseline_name: str | None = None, repeats: int | None = None, detail: bool = False) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if not check_id:
            raise ValueError("check_id is required")
        c = check_of(check_id)
        need_configured(ctx, c)
        if results is None:
            raise ValueError("results is required: a list with the raw hub answer of each run of this check (one per repeat)")
        base = store.load_baseline(project, ctx.scope, bname(baseline_name, ctx)) if c["kind"] == "perf" else None
        agg, rep = judge.judge(c, ctx.cfg, ctx.style, results, console=console_log, screenshots=screenshots, baseline=base, patterns=patterns(), causes=causes(), planned=repeats, detail=detail)
        return places.head(ctx, rep.pop("summary"), **rep)

    def find_past_corrections(request: str, project_id: str | None = None, place_id: str | None = None, k: int = 3, include_global: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        rows = feedback.find_past_corrections(project, request, max(1, min(int(k), 10)), scope=ctx.scope, include_global=include_global, strict_scope=True)
        return places.head(ctx, f"{len(rows)} correction(s) for {ctx.label}" + (" (including global ones)" if include_global else ""), corrections=rows)

    # --- writes (dry run by default) --------------------------------------------------------------------------------------------------
    def _plan_result(ctx, plan: dryrun.Plan, **extra) -> dict[str, Any]:
        res = {k: v for k, v in dryrun.run_plan(plan).items() if k != "summary"}
        return places.head(ctx, plan.summary, **res, **extra)

    def generate_check_script(project_id: str | None = None, place_id: str | None = None, studios: list | None = None, check_id: str | None = None, repeat_no: int = 1,
                              phase: str | None = None, verify_on_mock: bool = False, dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id, studios=studios, need_studio=True)
        if not check_id:
            raise ValueError("check_id is required")
        c = check_of(check_id)
        if phase not in (None, "start", "end"):
            raise ValueError("phase must be 'start' or 'end' (only the performance check has phases)")
        s = gen.generate(c, ctx.cfg, ctx.style, ctx.entry, ctx.label, repeat_no=repeat_no, phase=phase, probes=gen.load_probes(project.root))
        fname = f"{c['id']}-{s.repeat_no}" + (f"-{phase}" if phase else "") + ".luau"
        target = store.place_project(project, ctx.scope).output_dir / "scripts" / fname
        extra: dict[str, Any] = {"check_id": c["id"], "repeat_no": s.repeat_no, "phase": phase, "context": s.context, "lines": s.lines, "sha256": s.sha256[:16], "lint": s.lint,
                                 "test_mode": "required and enforced in the script" if c["kind"] == "data" else None}
        if verify_on_mock:
            extra["mock_run"] = _mock_run(s.luau)
        plan = dryrun.Plan(f"script for {c['id']}#{s.repeat_no}{'/' + phase if phase else ''} ({s.lines} lines, lint clean, context {s.context}); open Studio matched by {ctx.studio['matched_by']}")
        plan.add("write", str(target), f"sha256 {s.sha256[:16]}")
        plan.warnings.append(f"run it with the hub's execute_luau in the open Studio session on {ctx.label} only; the script stops itself in any other place")
        if dry_run:
            return _plan_result(ctx, plan, **extra)
        target.parent.mkdir(parents=True, exist_ok=True)
        res = dryrun.run_plan(plan, lambda p: (target.write_text(s.luau, encoding="utf-8"), [str(target)])[1], apply=True)
        return places.head(ctx, f"wrote the script for {c['id']}#{s.repeat_no}: run its `luau` with execute_luau", **{k: v for k, v in res.items() if k != "summary"}, **extra, luau=s.luau, path=str(target))

    def _mock_run(code: str) -> dict:
        if not mock.available():
            return {"status": "skipped", "reason": "lupa is not installed (pip install '.[dev]')"}
        from .domain.mockworld import World

        try:
            out = World().run_json(code)
        except Exception as exc:  # the script ran or it did not: this is a syntax/runtime smoke test only
            return {"status": "error", "error": str(exc)[:200]}
        problems = validate_result(out)
        return {"status": "ok" if not problems else "unreadable", "note": "ran on the synthetic mock world: only 'it runs and answers playqa.result/1' is meant, its assertions are about the mock", "problems": problems}

    def save_baseline(project_id: str | None = None, place_id: str | None = None, results: list | None = None, name: str = "default", note: str = "", dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if not isinstance(results, list) or not results:
            raise ValueError("results is required: the raw hub answers of a performance run's 'start' and 'end' snapshots")
        K.configured(lib()["perf_snapshot"], ctx.cfg) or need_configured(ctx, lib()["perf_snapshot"])
        if not isinstance(name, str) or not gen._ID.fullmatch(name):
            raise ValueError("name must match [A-Za-z][A-Za-z0-9_]{0,39}")
        start, end, problems = _perf_pair(results, lib()["perf_snapshot"])
        if start is None or end is None:
            raise ValueError("need both a 'start' and an 'end' performance snapshot in results" + (f" ({'; '.join(problems)})" if problems else ""))
        if any(v is None for v in (B.snapshot(start)["part_count"], B.snapshot(end)["part_count"])):
            raise ValueError("a snapshot has no part_count: it is not a usable baseline")
        minutes = ctx.cfg.get("performance", {}).get("minutes", K.setting(ctx.style, "perf_default_minutes"))
        doc = B.make_baseline(name, start, end, minutes, store.now(), note)
        v = store.versioner(project, ctx.scope)
        prev = len(v.history(store.baseline_name(name)))
        plan = dryrun.Plan(f"save baseline '{name}' for {ctx.label} (version {prev + 1}; start {B.snapshot(start)}, end {B.snapshot(end)})")
        plan.add("write", f"baseline '{name}' v{prev + 1} under the {ctx.label} workspace", note[:80])
        if prev:
            plan.warnings.append(f"replaces the current baseline '{name}' as the one compared against; version {prev} is kept and can be restored")
        if dry_run:
            return _plan_result(ctx, plan, baseline_name=name)

        def apply(_p):
            e = v.save(store.baseline_name(name), json.dumps(doc, indent=2, sort_keys=True), label=note[:60])
            return [e["file"]]

        res = dryrun.run_plan(plan, apply, apply=True)
        return places.head(ctx, f"saved baseline '{name}' for {ctx.label}", **{k: v2 for k, v2 in res.items() if k != "summary"}, baseline_name=name)

    def record_result(project_id: str | None = None, place_id: str | None = None, check_id: str | None = None, results: list | None = None, console_log: Any = None,
                      screenshots: list[str] | None = None, baseline_name: str | None = None, repeats: int | None = None, dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if not check_id:
            raise ValueError("check_id is required")
        c = check_of(check_id)
        need_configured(ctx, c)
        if results is None:
            raise ValueError("results is required: the raw hub answers of every run of this check")
        base = store.load_baseline(project, ctx.scope, bname(baseline_name, ctx)) if c["kind"] == "perf" else None
        agg, rep = judge.judge(c, ctx.cfg, ctx.style, results, console=console_log, screenshots=screenshots, baseline=base, patterns=patterns(), causes=causes(), planned=repeats)
        rid = store.new_result_id()
        row = store.result_row(ctx.scope, rep, rid)
        hint = store.flaky_hint(store.read_results(project, ctx.scope, c["id"]), c["id"], rep["verdict"], K.is_flaky(c, ctx.cfg))
        plan = dryrun.Plan(f"record the {rep['verdict'].upper()} result of {c['id']} for {ctx.label}: {rep['summary'][:140]}")
        plan.add("append", str(store.results_file(project, ctx.scope)), f"{row['check_id']} {row['verdict']} {row['repeats']['failed']}/{row['repeats']['conclusive']} failed")
        extra = {"verdict": rep["verdict"], "check_id": c["id"], "flaky_hint": hint}
        if dry_run:
            return _plan_result(ctx, plan, **extra)
        res = dryrun.run_plan(plan, lambda p: [str(store.append_result(project, ctx.scope, row))], apply=True)
        return places.head(ctx, f"recorded {rep['verdict'].upper()} for {c['id']} as {rid}", **{k: v for k, v in res.items() if k != "summary"}, result_id=rid, **extra)

    def record_run(request: str, project_id: str | None = None, place_id: str | None = None, constraints: dict | None = None, tools: list[str] | None = None,
                   outputs: list[str] | None = None, preview: str | None = None, model: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if not request or not request.strip():
            raise ValueError("request must not be empty")
        plan = dryrun.Plan(f"save run for {ctx.label}: {request[:100]}")
        plan.add("append", str(project.feedback_dir / "runs.jsonl"), "one run row carrying the scope")
        if dry_run:
            return _plan_result(ctx, plan)

        def apply(_p):
            rid = feedback.record_run(project, request=request, constraints={**(constraints or {}), "project_id": ctx.scope.project_id, "place_id": ctx.scope.place_id}, tools=tools, outputs=outputs,
                                      preview=preview, model=model, scope=ctx.scope, strict_scope=True)
            observe.RunLog(project.workspace / "learning" / "observations.jsonl").log_run(request=request, scope=ctx.scope, tools=tools or [], findings={"outputs": outputs or []})
            apply.rid = rid
            return [rid]

        res = dryrun.run_plan(plan, apply, apply=True)
        return places.head(ctx, f"saved run {apply.rid} for {ctx.label}", run_id=apply.rid, dry_run=False, fingerprint=res["fingerprint"])

    def record_decision(run_id: str, decision: str, project_id: str | None = None, place_id: str | None = None, reason: str = "", corrections: list[dict] | None = None, rating: int | None = None,
                        promote_requested: bool = False, mark_global: bool = False, dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        dims = config.load_style(project).get("correction_dimensions")
        plan = dryrun.Plan(f"save {decision} for {run_id} ({ctx.label}{', GLOBAL' if mark_global else ''})")
        plan.add("append", str(project.feedback_dir / "decisions.jsonl"), "one decision row carrying the scope")
        if mark_global:
            plan.warnings.append("mark_global=true applies this correction to every project and place: only when the user said so")
        # validate now so a dry run refuses what the real call would refuse
        if decision not in feedback.DECISIONS:
            raise ValueError(f"decision must be one of {feedback.DECISIONS}")
        if feedback.get_run(project, run_id) is None:
            raise ValueError(f"unknown run_id '{run_id}': save the run first with record_run(dry_run=false)")
        if decision != "accept" and not (reason or corrections):
            raise ValueError("a reject/revise decision needs a reason or at least one correction")
        for cr in corrections or []:
            if not cr.get("dimension") or not cr.get("note"):
                raise ValueError("each correction needs 'dimension' and 'note'")
            if dims and cr["dimension"] not in dims:
                raise ValueError(f"unknown correction dimension '{cr['dimension']}'. Allowed: {dims}")
        if dry_run:
            return _plan_result(ctx, plan)

        def apply(_p):
            row = feedback.record_decision(project, run_id, decision, reason=reason, corrections=corrections, rating=rating, promote_requested=promote_requested, allowed_dimensions=dims,
                                           scope=ctx.scope, mark_global=mark_global, strict_scope=True)
            log = observe.RunLog(project.workspace / "learning" / "observations.jsonl")
            if any(r["run_id"] == run_id for r in log.rows(kind="run")):
                log.log_action(run_id, {"accept": "accept", "reject": "reject", "revise": "edit"}[decision], note=reason)
            return [row["run_id"]]

        res = dryrun.run_plan(plan, apply, apply=True)
        return places.head(ctx, f"saved {decision} for {run_id} ({ctx.label}{', GLOBAL' if mark_global else ''})", decision=decision, global_=bool(mark_global), dry_run=False, fingerprint=res["fingerprint"])

    def promote_run(run_id: str, kind: str, title: str, description: str, tags: list[str], path: str, project_id: str | None = None, place_id: str | None = None,
                    polarity: str = "positive", confirm: bool = False, dry_run: bool = True) -> dict[str, Any]:
        ctx = places.resolve(project, project_id, place_id)
        if kind not in project.asset_kinds:
            raise ValueError(f"kind must be one of {list(project.asset_kinds)}")
        if feedback.get_run(project, run_id) is None:
            raise ValueError(f"unknown run_id '{run_id}': save the run first with record_run(dry_run=false)")
        sp = store.place_project(project, ctx.scope)
        plan = dryrun.Plan(f"add run {run_id} to the {ctx.label} library as a {'curated' if confirm else 'candidate'} {kind}")
        plan.add("upsert", str(sp.library_file), title[:60])
        if dry_run:
            return _plan_result(ctx, plan)

        def apply(_p):
            asset = feedback.promote_run(project, run_id, kind=kind, title=title, description=description, tags=tags, path=path, polarity=polarity, confirm=confirm, scope=ctx.scope,
                                         store=config.LibraryStore(sp))
            apply.asset = asset
            return [asset["id"]]

        dryrun.run_plan(plan, apply, apply=True)
        return places.head(ctx, f"{apply.asset['status']} library entry {apply.asset['id']} for {ctx.label}", asset_id=apply.asset["id"], status=apply.asset["status"], dry_run=False)

    D = {
        "list_checks": "List the checks, which can run for this place, which are flaky (re-run N times) and why one cannot run. Needs project_id and place_id.",
        "plan_playtest": "Plan the playtest: play sessions, hub calls in order (start_stop_play, execute_luau, get_console_output, screen_capture), repeats for flaky checks. Needs studios (list_roblox_studios) matching the place.",
        "parse_console_log": "Parse get_console_output text into ranked error/warning findings, with an optional boot window or per-check cut. Format is schema_unverified.",
        "compare_baseline": "[rare] Compare a performance run (start and end snapshots) with the stored baseline: per-measure percent against placeholder limits.",
        "explain_failure": "Judge the raw hub answers of one check (all repeats), console text and screenshot paths: verdict first, then each failure with evidence and most likely cause, and what a pass checked.",
        "find_past_corrections": "Past corrections for THIS place (plus ones marked global) relevant to a request. Read before planning.",
        "generate_check_script": "Generate the assertion Luau for one check run (lint-checked, place-guarded). A data check is REFUSED without a test-mode switch. dry_run=true shows the plan; false writes it and returns the luau. Needs studios.",
        "save_baseline": "[rare] Save a performance run (start and end snapshots) as this place's baseline (versioned; dry_run=true by default).",
        "record_result": "Judge and store one check's result (verdict, repeats, failed assertions) in this place's workspace; flags a stable check that mixes pass and fail. dry_run=true by default.",
        "record_run": "Save a request and its outputs for this place; returns run_id. dry_run=true by default.",
        "record_decision": "Save the user's verdict (accept/reject/revise) on a run, with corrections [{dimension, note}] in their words. mark_global only when asked. dry_run=true by default.",
        "promote_run": "[rare] Add a reviewed run to this place's library (candidate unless confirm=true; ask first). dry_run=true by default.",
    }
    fns = {"list_checks": (list_checks, True), "plan_playtest": (plan_playtest, True), "parse_console_log": (parse_console_log, True), "compare_baseline": (compare_baseline, True),
           "explain_failure": (explain_failure, True), "find_past_corrections": (find_past_corrections, True), "generate_check_script": (generate_check_script, False),
           "save_baseline": (save_baseline, False), "record_result": (record_result, False), "record_run": (record_run, False), "record_decision": (record_decision, False),
           "promote_run": (promote_run, False)}
    specs = [ToolSpec(n, f, D[n], read_only=ro, idempotent=ro or n in ("generate_check_script", "save_baseline"), group="rare" if n in RARE else None) for n, (f, ro) in fns.items()]
    if (project.env("TELEMETRY") or "").lower() in ("1", "true", "yes"):
        specs = telemetry.Telemetry(project.workspace / "telemetry.jsonl").instrument(specs)
    return specs
