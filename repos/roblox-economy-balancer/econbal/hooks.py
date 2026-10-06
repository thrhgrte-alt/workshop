"""Wires the economy-balancer domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import tools as tools_mod
from .guide_adapter import DomainHooks, all_tools, config, emit, evals as evals_mod, feedback as fb, mcpkit, scope
from .domain import analysis as A
from .domain import importer, learning, luau, places, plot, rebalance as R
from .domain import sim as M
from .domain import spec as S

INSTRUCTIONS = """\
Roblox economy and progression balancer. Workflow: get_style_brief -> find_past_corrections -> validate_economy_spec -> check_economy (one verdict, all findings)
-> drill into time_to_upgrade_table, flow_report, find_dominant_strategies, find_dead_options, monetisation_check -> sensitivity to learn which number matters ->
propose_rebalance (dry_run=true: the SMALLEST set of values that fixes ONE finding, before/after timing, locked values never touched) -> compare_specs ->
export_values_luau (dry run, then a reviewable Luau that creates a NEW config module) -> save_spec_version -> record_run / record_decision with the user's words.
ARITHMETIC RULE: the simulator does all the arithmetic. Every number you state (minutes, costs, ratios, days) must be copied from a tool result; never calculate in your head.
Target pacing in style/style.yaml is a PLACEHOLDER until the user supplies theirs: say so. Archetype assumptions (session length, sessions per day, efficiency) are assumptions,
not player data. The simulator predicts relative pacing only, not retention or revenue. To read a real game: emit_import_luau -> Studio's execute_luau -> normalise_import (a draft:
review every assumption it lists). This server never talks to Studio, never publishes and never overwrites a config. Save playtest notes ('tier 4 felt too slow') with
record_decision (dimension pacing, tier, felt) and use suggest_band_adjustments before changing the bands."""

dig = evals_mod.dig

EXTENSIONS = {"economy_spec": [".yaml", ".yml", ".json"], "playtest_note": [".json", ".md"], "rebalance": [".yaml", ".yml"]}


def correction_dimensions(project: scope.Project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project: scope.Project) -> dict:
    try:
        reg = places.load_registry(project)
        report = []
        for (pid, plid), e in sorted(reg.items()):
            try:
                findings = S.validate(S.load_spec(e["spec_path"]))
                report.append({"place": f"{pid}/{plid}", "synthetic": e["synthetic"], "spec_ok": not S.errors_of(findings), "bands_file": Path(e["bands_path"]).exists()})
            except Exception as exc:
                report.append({"place": f"{pid}/{plid}", "spec_ok": False, "error": f"{type(exc).__name__}: {exc}"})
        spec = {"registry": str(places.registry_path(project)), "places": report}
    except Exception as exc:
        spec = {"registry": str(places.registry_path(project)), "error": f"{type(exc).__name__}: {exc}"}
    style = config.load_style(project)
    return {
        "projects": spec,
        "target_bands": {"placeholder": bool(style.get("placeholder")), "bands": [b["id"] for b in style.get("target_bands", [])]},
        "luau_mock": "available" if importlib.util.find_spec("lupa") else "unavailable (pip install '.[simulate]'): Luau scripts are linted but not run",
        "plots": "pillow" if importlib.util.find_spec("PIL") else "unavailable (pip install '.[imaging]')",
        "matplotlib": "installed (optional backend)" if importlib.util.find_spec("matplotlib") else "not installed (optional; the Pillow backend is the default)",
        "roblox_studio": "this server never connects to Studio; use emit_import_luau / export_values_luau and forward the Luau to Studio's own MCP server (execute_luau)",
        "works_without_studio": ["spec validation", "seeded simulation", "time-to-upgrade vs bands", "flow and inflation", "dominant/dead options", "monetisation check", "sensitivity",
                                 "rebalance proposals", "Luau generation + lint + mock run", "import normalisation from JSON", "plots", "feedback and band suggestions", "evals", "MCP server"],
        "needs_a_real_game": ["real values (run the import dump in Studio)", "real archetype data (session lengths, efficiency)", "your real target pacing", "any claim about retention or revenue"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("ECONBAL_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["ECONBAL_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("ECONBAL_WORKSPACE", None)
            else:
                os.environ["ECONBAL_WORKSPACE"] = old


def mutate(raw: dict, mutations: list[dict] | None) -> dict:
    """Apply small, explicit edits to a spec for eval tasks: set a value, lock a pattern, or remove an entity."""
    s = copy.deepcopy(raw)
    for m in mutations or []:
        op = m["op"]
        if op == "set":
            s = S.set_value(s, m["path"], m["value"])
        elif op == "lock":
            s.setdefault("locked", []).append(m["pattern"])
        elif op == "remove":
            s[m["section"]] = [x for x in s[m["section"]] if x["id"] != m["id"]] if isinstance(s[m["section"]], list) else {k: v for k, v in s[m["section"]].items() if k != m["id"]}
        elif op == "add":
            s.setdefault(m["section"], []).append(m["value"])
        elif op == "top":
            s[m["field"]] = m["value"]
        else:
            raise ValueError(f"unknown spec mutation '{op}'")
    return s


EVAL_PLACE = (0, "Eval Place", "eval/place")
EVAL_LABEL = "eval/place"


DEMO_STUDIO = {"name": "Demo Mine Main (SYNTHETIC)", "place_id": 0, "studio_id": "studio-main"}


def dummy_args(tool, scope: tuple[str, str] | None = None) -> dict:
    """Minimal valid arguments for any tool (used by the scope evals), optionally scoped to one place."""
    import inspect

    given = {"request": "x", "run_id": "run-x", "decision": "accept", "path": "upgrades.drill_2.cost", "name": "x", "kind": "economy_spec", "title": "t", "description": "d", "tags": [],
             "pct": [10], "studios": [DEMO_STUDIO], "vendor_path": "ReplicatedStorage.Shop", "all_values": True,
             "import_json": {"format": "econbal-import/1", "containers": {"ores": {"items": [{"name": "C", "fields": {"Value": 1}}]}}}}
    args: dict = {}
    for n, prm in inspect.signature(tool.fn).parameters.items():
        if n in given and (prm.default is inspect.Parameter.empty or n in ("pct", "studios", "vendor_path", "all_values", "import_json")):
            args[n] = given[n]
    if scope:
        for n in ("project_id", "a_project_id", "b_project_id"):
            if n in inspect.signature(tool.fn).parameters:
                args[n] = scope[0]
        for n in ("place_id", "a_place_id", "b_place_id"):
            if n in inspect.signature(tool.fn).parameters:
                args[n] = scope[1]
    return args


def eval_solver(project: scope.Project):
    style = config.load_style(project)
    rules = A.load_rules(project.root)

    def raw_of(inp: dict) -> dict:
        if inp.get("spec") is not None:
            raw = copy.deepcopy(inp["spec"])
        else:
            raw = S.load_spec(project.root / "examples" / "economies" / f"{inp.get('econ', 'balanced')}.yaml")
        return mutate(raw, inp.get("mutations"))

    def ctx_of(inp: dict) -> A.Ctx:
        raw = raw_of(inp)
        errs = S.errors_of(S.validate(raw))
        if errs:
            raise ValueError("spec invalid: " + errs[0]["message"])
        return A.make_ctx(raw, style, rules)

    def codes(findings, sev=("error", "warning")):
        return sorted({f["code"] for f in findings if f["severity"] in sev})

    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "validate":
            raw = raw_of(inp)
            f = S.validate(raw)
            return {"ok": not S.errors_of(f), "error_codes": sorted({x["code"] for x in S.errors_of(f)}), "warning_codes": sorted({x["code"] for x in f if x["severity"] == "warning"}),
                    "text": " | ".join(x["message"] for x in f)}
        if op == "simulate":
            ctx = ctx_of(inp)
            sm = M.simulate(ctx.spec, inp.get("archetype", "regular"), seed=inp.get("seed"), days=inp.get("days"), boost_mode=inp.get("boost_mode"), policy=inp.get("policy"))
            pur = [p for p in sm["purchases"] if p["lap"] == inp.get("lap", 0)]
            return {"order": [p["id"] for p in sm["purchases"]], "minutes": {p["id"]: p["minute"] for p in pur}, "gaps": {p["id"]: p["gap_minutes"] for p in pur},
                    "paybacks": {p["id"]: p["payback_minutes"] for p in pur}, "rebirths": sm["rebirths"], "count": len(sm["purchases"]), "exhausted_at_minute": sm["exhausted_at_minute"],
                    "stalled_at_minute": sm["stalled_at_minute"], "end_balance": sm["end"]["balance"], "end_rates": sm["end"]["rates"], "pending": sm["pending"],
                    "minutes_per_day": sm["minutes_per_day"], "days": sm["days"]}
        if op == "check":
            ctx = ctx_of(inp)
            res = A.check(ctx)
            return {"verdict": res["verdict"], "codes": codes(res["findings"]), "errors": codes(res["findings"], ("error",)), "all_codes": codes(res["findings"], ("error", "warning", "info")),
                    "metrics": res["metrics"], "findings": res["findings"], "range_flagged": sorted(f["metric"] for f in res["range_findings"])}
        if op == "timing":
            ctx = ctx_of(inp)
            tt = A.time_to_upgrade(ctx, archetypes=inp.get("archetypes"), boost_mode=inp.get("boost_mode"), days=inp.get("days"), laps=inp.get("laps", "first"))
            arch = inp.get("archetype", "regular")
            rows = [r for r in tt["rows"] if r["archetype"] == arch]
            return {"gaps": {r["upgrade"]: r["gap_minutes"] for r in rows if not r.get("projected")}, "status": {r["upgrade"]: r["status"] for r in rows},
                    "projected": {r["upgrade"]: r["gap_minutes"] for r in rows if r.get("projected")}, "codes": codes(tt["findings"], ("error", "warning", "info")),
                    "findings": tt["findings"], "unreached": [(u["archetype"], u["track"], u["first_missing_tier"]) for u in tt["unreached"]],
                    "per_tier": {f"{t['archetype']}:{t['tier']}": [t["gap_minutes_min"], t["gap_minutes_max"]] for t in tt["per_tier"]}}
        if op == "flow":
            ctx = ctx_of(inp)
            res = A.flow(ctx, archetypes=inp.get("archetypes"), days=inp.get("days"))
            arch = inp.get("archetype", "regular")
            f = res["flows"][arch]
            cur = inp.get("currency", next(iter(f["currencies"])))
            c = f["currencies"][cur]
            err = max(abs(c["end_balance"] - (c["start_balance"] + c["total_sources"] - c["total_sinks"])), 0.0)
            return {"identity_error": err, "total_sources": c["total_sources"], "total_sinks": c["total_sinks"], "net_inflation": c["net_inflation"], "tail_net_share": c["tail_net_share"],
                    "exhausted_after_days": f["exhausted_after_days"], "day1_sources": c["days"][0]["sources"], "day1_sinks": c["days"][0]["sinks"], "day1_sinks_by": c["days"][0]["sinks_by"],
                    "end_balance": c["end_balance"], "codes": codes(res["findings"], ("error", "warning", "info")), "days": [d["net"] for d in c["days"]]}
        if op == "dominant":
            ctx = ctx_of(inp)
            res = A.dominant(ctx, forced_check=inp.get("forced_check", True))
            return {"dominant": [g["dominant"] for g in res["groups"] if g["dominant"]], "dominated": sorted({d for g in res["groups"] for d in g["dominated"]}),
                    "groups": [f"{g['track']}:{g['tier']}" for g in res["groups"]], "strategies": {s["strategy"]: [s["groups_won"], s["contested_groups"]] for s in res["strategies"]},
                    "codes": codes(res["findings"], ("error", "warning", "info")),
                    "paybacks": {f"{g['track']}:{g['tier']}": {o: r["payback_minutes"] for o, r in g["per_archetype"].get("regular", {}).items()} for g in res["groups"]}}
        if op == "dead":
            ctx = ctx_of(inp)
            res = A.dead_options(ctx)
            return {"status": {r["upgrade"]: r["status"] for r in res["rows"]}, "best_payback": {r["upgrade"]: r["best_payback_minutes"] for r in res["rows"]},
                    "codes": codes(res["findings"], ("error", "warning", "info"))}
        if op == "monetisation":
            ctx = ctx_of(inp)
            res = A.monetisation(ctx)
            return {"codes": codes(res["findings"], ("error", "warning", "info")), "speedups": {r["upgrade"]: r["speedup"] for r in res["rows"]}, "boosts": res["boosts"],
                    "per_boost": {r["boost"]: r["speedup"] for r in res["per_boost"]}, "gap_free": {r["upgrade"]: r["gap_free"] for r in res["rows"]},
                    "gap_boosted": {r["upgrade"]: r["gap_boosted"] for r in res["rows"]}}
        if op == "sensitivity":
            ctx = ctx_of(inp)
            try:
                res = A.sensitivity(ctx, inp["path"], values=inp.get("values"), pct=inp.get("pct"), archetypes=inp.get("archetypes", ["regular"]))
            except ValueError as exc:
                return {"error": str(exc)}
            tab = {r["upgrade"]: r for r in res["tables"][inp.get("archetype", "regular")]}
            label = res["variants"][1]["label"]
            return {"variants": [v["label"] for v in res["variants"]], "minute": {u: r["minute"] for u, r in tab.items()}, "delta": {u: r["minute_delta_vs_base"][label] for u, r in tab.items()},
                    "moved": res["moved_cells"].get(inp.get("archetype", "regular"), 0), "locked": res["locked"]}
        if op == "compare":
            a = A.make_ctx(raw_of({"econ": inp["a"], "mutations": inp.get("a_mutations")}), style, rules)
            b = A.make_ctx(raw_of({"econ": inp["b"], "mutations": inp.get("b_mutations")}), style, rules)
            res = A.compare(a, b)
            rows = {r["upgrade"]: r for r in res["timing"]["regular"]}
            return {"changed": [c["path"] for c in res["value_changes"]], "delta": {u: r["minute_delta"] for u, r in rows.items()}, "only_a": res["findings_only_in_a"], "only_b": res["findings_only_in_b"],
                    "minute_a": {u: r["minute_a"] for u, r in rows.items()}, "minute_b": {u: r["minute_b"] for u, r in rows.items()}}
        if op == "rebalance":
            ctx = ctx_of(inp)
            base = A.check(ctx)
            try:
                target = R.select_finding(base["findings"], inp.get("code"), upgrade=inp.get("upgrade"), archetype=inp.get("archetype"), currency=inp.get("currency"), track=inp.get("track"))
            except ValueError as exc:
                return {"error": str(exc), "found": False}
            try:
                res = R.propose(ctx, base, target, max_changes=inp.get("max_changes", 2))
            except ValueError as exc:
                return {"error": str(exc), "found": False}
            if not res["found"]:
                return {"found": False, "reason": res["reason"], "skipped": [s["path"] for s in res["skipped"]], "locked_skipped": [s["path"] for s in res["skipped"] if s["reason"] == "locked"],
                        "considered": res["candidates_considered"]}
            changed = {c["path"]: c["after"] for c in res["changes"]}
            locked = set(S.locked_paths(ctx.spec))
            after = A.check(A.make_ctx(res["proposed_spec"], style, rules))
            return {"found": True, "paths": sorted(changed), "changes": [{"path": c["path"], "before": c["before"], "after": c["after"]} for c in res["changes"]], "n_changes": res["n_changes"], "locked_touched": sorted(set(changed) & locked), "verdict_after": after["verdict"],
                    "target_gone": not any(R.same_target(f, target) for f in after["findings"]), "new_codes": sorted({f["code"] for f in res["findings_after"] if f["severity"] in ("error", "warning")}),
                    "locked_skipped": [s["path"] for s in res["skipped"] if s["reason"] == "locked"], "trials": res["trials"],
                    "original_untouched": S.value_paths(ctx.spec) == S.value_paths(S.normalize(raw_of(inp)))}
        if op == "export":
            ctx = ctx_of(inp)
            parent, cname = inp.get("parent_path", "ReplicatedStorage.Config"), inp.get("config_name", "EconomyValues_Proposed")
            try:
                values, prev = luau.values_for_export(ctx.spec, A.make_ctx(raw_of({"econ": inp["baseline"]}), style, rules).spec if inp.get("baseline") else None)
                module = luau.values_module_source(values, spec_id=ctx.spec["id"], spec_hash=S.spec_hash(ctx.spec), previous=prev, label=EVAL_LABEL)
                build = lambda: luau.build_export_luau(module, parent_path=parent, config_name=cname, n_values=len(values), spec_hash="x", place=EVAL_PLACE)
                code = build()
            except ValueError as exc:
                return {"error": str(exc)}
            mock = luau.verify_export_on_mock(code, module, values, parent, cname, pre_existing=bool(inp.get("pre_existing")), open_place=(inp.get("open_name", EVAL_PLACE[1]), 0))
            return {"error": None, "lint": luau.lint(code), "values": len(values), "mock": mock.get("status"), "mock_error": mock.get("error"), "has_previous": prev is not None,
                    "creates_module": "Instance.new(\"ModuleScript\")" in code, "refuses_overwrite": "refusing to overwrite" in code, "has_place_guard": "wrong place" in code,
                    "deterministic": code == build()}
        if op == "luau_lint":
            return {"lint": luau.lint(inp["code"])}
        if op == "import_roundtrip":
            from .domain.mock_luau import MockRoblox

            if importlib.util.find_spec("lupa") is None:
                return {"mock": "skipped"}
            code = luau.build_import_luau(vendor_path=inp.get("vendor_path"), tools_path=inp.get("tools_path"), ores_path=inp.get("ores_path"), place=EVAL_PLACE)
            mock = MockRoblox()
            mock.set_place(inp.get("open_name", EVAL_PLACE[1]), 0)
            for node in inp["tree"]:
                mock.ensure_path(node["parent"])
                mock.add(node["parent"], node["class"], node["name"], attributes=node.get("attributes"), value=node.get("value"))
            try:
                dump = json.loads(mock.run(code))
            except Exception as exc:
                return {"mock": "ran", "error": str(exc).splitlines()[0]}
            res = importer.normalise(dump, game="Mock")
            return {"mock": "ok", "lint": luau.lint(code), "ok": res["ok"], "upgrades": [u["id"] for u in res["spec"]["upgrades"]], "sources": [s["id"] for s in res["spec"]["sources"]],
                    "costs": {u["id"]: u["cost"] for u in res["spec"]["upgrades"]}, "containers": sorted(dump["containers"]), "unresolved": len(res["unresolved"])}
        if op == "import_text":
            try:
                code = luau.build_import_luau(vendor_path=inp.get("vendor_path"), tools_path=inp.get("tools_path"), ores_path=inp.get("ores_path"), max_items=inp.get("max_items", 500), place=EVAL_PLACE)
            except ValueError as exc:
                return {"error": str(exc)}
            return {"error": None, "lint": luau.lint(code), "read_only": all(t not in code for t in (".Parent =", "Instance.new", "SetAttribute(", ".Source =")),
                    "uses_json_encode": "JSONEncode" in code}
        if op == "normalise":
            try:
                res = importer.normalise(inp["dump"], field_map=inp.get("field_map"), game=inp.get("game"))
            except ValueError as exc:
                return {"error": str(exc)}
            sp = res["spec"]
            out = {"error": None, "ok": res["ok"], "upgrades": {u["id"]: {"cost": u["cost"], "tier": u["tier"], "track": u["track"], "effects": u["effects"]} for u in sp["upgrades"]},
                   "sources": {s["id"]: s["per_minute"] for s in sp["sources"]}, "currencies": sorted(sp["currencies"]), "assumptions": res["assumptions"], "unresolved": res["unresolved"],
                   "mapping": {m["original"]: m["id"] for m in res["mapping"]}, "assumptions_text": " | ".join(res["assumptions"]), "unresolved_text": " | ".join(res["unresolved"]),
                   "n_assumptions": len(res["assumptions"]), "n_unresolved": len(res["unresolved"]), "finding_codes": sorted({f["code"] for f in res["findings"]}), "archetypes_assumed": all(
                       a.get("assumed") for a in sp["archetypes"].values())}
            if res["ok"]:
                out["check_runs"] = A.check(A.make_ctx(sp, style, rules))["verdict"] in ("pass", "fail")
            return out
        if op == "determinism":
            ctx = ctx_of(inp)
            kw = {"archetype": inp.get("archetype", "regular")}
            a = M.simulate(ctx.spec, kw["archetype"], seed=inp.get("seed", 7))
            b = M.simulate(ctx.spec, kw["archetype"], seed=inp.get("seed", 7))
            c = M.simulate(ctx.spec, kw["archetype"], seed=inp.get("seed", 7) + 1)
            key = lambda s: [(p["id"], p["minute"]) for p in s["purchases"]]
            return {"same_seed_identical": A.clean(a) == A.clean(b), "other_seed_differs": key(a) != key(c), "n": len(a["purchases"]), "first_minutes": key(a)[:3]}
        if op == "plot":
            ctx = ctx_of(inp)
            with tempfile.TemporaryDirectory() as d:
                info = plot.render(ctx.sims(), Path(d) / "p.png", title="t")
                from PIL import Image

                w, h = Image.open(info["path"]).size
                nonblank = len(set(Image.open(info["path"]).convert("RGB").getdata())) > 3
            return {"width": w, "height": h, "nonblank": nonblank}
        if op == "style":
            return {"placeholder": bool(style.get("placeholder")), "bands": [b["id"] for b in style["target_bands"]], "early_min": style["target_bands"][0]["min_minutes"],
                    "early_max": style["target_bands"][0]["max_minutes"], "marked_placeholder": all(b.get("placeholder") for b in style["target_bands"]),
                    "rules": sorted(rules), "severity": {k: v["severity"] for k, v in rules.items()}}
        if op == "tools":
            specs = all_tools(project, HOOKS)
            return {"names": sorted(t.name for t in specs), "read_only": sorted(t.name for t in specs if t.read_only), "writers": sorted(t.name for t in specs if not t.read_only)}
        if op == "tool_sizes":
            allt = all_tools(project, HOOKS)
            old = os.environ.get("ECONBAL_DISABLE_GROUPS")
            os.environ["ECONBAL_DISABLE_GROUPS"] = "rare"
            try:
                core = all_tools(project, HOOKS)
            finally:
                if old is None:
                    os.environ.pop("ECONBAL_DISABLE_GROUPS", None)
                else:
                    os.environ["ECONBAL_DISABLE_GROUPS"] = old
            rare = sorted({t.name for t in allt} - {t.name for t in core})
            return {"description_chars_total": sum(len(t.description) for t in allt), "max_description_chars": max(len(t.description) for t in allt),
                    "longest": max(allt, key=lambda t: len(t.description)).name, "core_description_chars": sum(len(t.description) for t in core),
                    "rare": rare, "core": sorted(t.name for t in core), "n_all": len(allt), "n_core": len(core)}
        if op in ("tools_seq", "refuse_all", "scope_stated"):
            specs = all_tools(project, HOOKS)
            if op == "refuse_all":
                bad = []
                with _temp_workspace():
                    for t in specs:
                        args = dummy_args(t)
                        for k in ("project_id", "place_id", "a_project_id", "a_place_id", "b_project_id", "b_place_id"):
                            args.pop(k, None)
                        try:
                            mcpkit.call_local(specs, t.name, args)
                            bad.append(t.name)
                        except ValueError as exc:
                            if "project_id" not in str(exc):
                                bad.append(f"{t.name}: {str(exc)[:80]}")
                        except Exception as exc:  # any other failure also means it did not refuse for the right reason
                            bad.append(f"{t.name}: {type(exc).__name__}")
                return {"not_refused": bad, "checked": len(specs)}
            if op == "scope_stated":
                missing = []
                with _temp_workspace():
                    for t in specs:
                        if not t.read_only or t.name in ("search_library",):
                            continue
                        args = dummy_args(t, scope=("demo_mine", "main"))
                        res = mcpkit.call_local(specs, t.name, args)
                        ok = (res.get("project_id"), res.get("place_id")) == ("demo_mine", "main") or (res.get("a", {}).get("project_id"), res.get("b", {}).get("place_id")) == ("demo_mine", "main")
                        if not ok or not res.get("summary"):
                            missing.append(t.name)
                return {"missing": missing}
            results: list[dict] = []

            def subst(v):
                if isinstance(v, dict) and "$econ" in v:
                    return raw_of({"econ": v["$econ"], "mutations": v.get("mutations")})
                if isinstance(v, dict):
                    return {k: subst(x) for k, x in v.items()}
                if isinstance(v, list):
                    return [subst(x) for x in v]
                if isinstance(v, str) and v.startswith("$ref:"):
                    return dig({"steps": results}, v[5:])
                return v

            with _temp_workspace() as ws:
                for st in inp["steps"]:
                    try:
                        r = mcpkit.call_local(specs, st["tool"], subst(st.get("args", {})))
                        results.append({"ok": True, "result": r, "chars": len(json.dumps(r, default=str))})
                    except (ValueError, PermissionError, FileNotFoundError) as exc:
                        results.append({"ok": False, "error": str(exc), "result": {}})
                wrote = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
            return {"steps": results, "wrote": wrote, "n_errors": sum(1 for r in results if not r["ok"])}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project: scope.Project) -> dict:
    dig = evals_mod.dig

    def approx(result, task, path, value, tol=1e-6):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def list_empty(result, task, path):
        got = dig(result, path)
        return got == [] or got is None, f"{path}={got!r} should be empty"

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:160]!r})"

    def greater(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got > than, f"{path}={got!r} should be > {than}"

    def less(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got < than, f"{path}={got!r} should be < {than}"

    def text_contains(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() in got.lower(), f"{path} should mention '{text}' (got {got[:160]!r})"

    def mock_ok_or_skipped(result, task):
        return result.get("mock") in ("ok", "skipped"), f"mock status {result.get('mock')!r} (error: {result.get('mock_error')})"

    def step_error_contains(result, task, step, text):
        err = dig(result, f"steps.{step}.error") or ""
        return text.lower() in err.lower(), f"step {step} should be refused mentioning '{text}' (got {err[:200]!r})"

    def step_ok(result, task, step):
        ok = dig(result, f"steps.{step}.ok")
        return ok is True, f"step {step} should succeed (got {dig(result, f'steps.{step}.error')!r})"

    return {"step_error_contains": step_error_contains, "step_ok": step_ok, "approx": approx, "list_contains": list_contains, "list_excludes": list_excludes, "list_empty": list_empty, "error_contains": error_contains, "greater": greater,
            "less": less, "text_contains": text_contains, "mock_ok_or_skipped": mock_ok_or_skipped}


def register_cli(sub, project: scope.Project) -> None:
    for name, helptext in (("check", "run every analysis on a place's spec; exit 1 unless it passes"), ("table", "time-to-upgrade table for a place"), ("plot", "write the progression curves PNG for a place")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--project-id", required=True)
        p.add_argument("--place-id", required=True)
        if name == "plot":
            p.add_argument("--out")
        p.set_defaults(handler={"check": _check, "table": _table, "plot": _plot}[name])
    p = sub.add_parser("places", help="list the projects and places in projects.yaml")
    p.set_defaults(handler=lambda a, pr: emit({"registry": str(places.registry_path(pr)), "places": places.known(places.load_registry(pr))}))


def _call(pr: scope.Project, tool: str, **kw) -> dict:
    return mcpkit.call_local(all_tools(pr, HOOKS), tool, {k: v for k, v in kw.items() if v is not None})


def _check(a, pr) -> int:
    res = _call(pr, "check_economy", project_id=a.project_id, place_id=a.place_id)
    emit(res)
    return 0 if res["verdict"] == "pass" else 1


def _table(a, pr) -> int:
    emit(_call(pr, "time_to_upgrade_table", project_id=a.project_id, place_id=a.place_id))
    return 0


def _plot(a, pr) -> int:
    res = _call(pr, "plot_curves", project_id=a.project_id, place_id=a.place_id)
    if a.out:
        import shutil

        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(res["path"], a.out)
        print(a.out)
    else:
        print(res["path"])
    return 0


HOOKS = DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver,
    eval_checks=eval_checks,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
