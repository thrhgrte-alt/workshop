"""MCP tools for the economy and progression balancer.

Rules every tool follows: it needs ``project_id`` and ``place_id`` (refuses without them, never guesses); the report says which place it is for;
the simulator does ALL arithmetic (copy numbers from results, never calculate); output is a one-line ``summary`` first, ranked findings, details only
with ``detail=true``; write tools default to ``dry_run=true``. The server never talks to Studio: it emits Luau (read-only dump, new config module) that the agent
forwards to Studio's own MCP server after the open place has been matched to the named one. Tools whose description starts with ``[rare]`` can be left
disabled (``ECONBAL_DISABLE_GROUPS=rare``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .domain import analysis as A
from .domain import importer, learning, luau, places, plot, rebalance as R
from .domain import spec as S
from .guide_adapter import ToolSpec, config, dryrun, feedback, manifest, scope

RARE = ("sensitivity", "compare_specs", "plot_curves", "suggest_band_adjustments", "emit_import_luau", "normalise_import", "export_values_luau", "save_spec_version", "promote_run")
SPEC_ARGS_DOC = "spec = inline what-if mapping; spec_name/spec_version = a version saved for this place; default = the place's economy.yaml"


def make_tools(project) -> list[ToolSpec]:
    def entry_of(project_id, place_id) -> dict:
        return places.resolve(project, project_id, place_id)

    def sproj_of(entry: dict):
        return scope.scoped_project(project, entry["project_id"], entry["place_id"])

    def global_proj():
        return scope.scoped_project(project, scope.GLOBAL, scope.GLOBAL)

    def resolve_raw(entry: dict, spec: dict | None, spec_name: str | None, spec_version: int | None) -> tuple[dict, dict]:
        if spec is not None and spec_name is not None:
            raise ValueError("pass at most one of spec (inline what-if) or spec_name (a version saved for this place)")
        if spec is not None:
            if not isinstance(spec, dict):
                raise ValueError("spec must be a mapping (the contents of an economy.yaml)")
            return spec, {"origin": "inline what-if"}
        if spec_name is not None:
            try:
                text = dryrun.Versioner(sproj_of(entry)).load(f"spec-{scope.safe_name(spec_name)}", spec_version)
            except FileNotFoundError as exc:
                raise ValueError(f"{exc} for place {entry['project_id']}/{entry['place_id']}. Save one with save_spec_version first (versions are per place).") from exc
            return yaml.safe_load(text), {"origin": "saved version", "name": spec_name, "version": spec_version or "latest"}
        p = Path(entry["spec_path"])
        if not p.exists():
            raise ValueError(f"place {entry['project_id']}/{entry['place_id']} has no spec at {p}. Create it (normalise_import can draft one) or fix projects.yaml.")
        return S.load_spec(p), {"origin": "place file", "path": str(p)}

    def load(project_id, place_id, spec=None, spec_name=None, spec_version=None):
        entry = entry_of(project_id, place_id)
        raw, src = resolve_raw(entry, spec, spec_name, spec_version)
        findings = S.validate(raw)
        errors = S.errors_of(findings)
        if errors:
            raise ValueError(f"the spec for {entry['project_id']}/{entry['place_id']} has errors (run validate_economy_spec): "
                             + "; ".join(f"[{e['code']}] {e['path']}: {e['message']}" for e in errors[:3]))
        style, layers = places.layered_style(project, entry)
        ctx = A.make_ctx(raw, style, A.load_rules(project.root))
        src = {**src, "hash": S.spec_hash(raw), "id": ctx.spec["id"], "bands": "place file" if layers["place_bands"] else "global style/style.yaml"}
        return ctx, raw, entry, src

    def head(summary: str, entry: dict, **rest: Any) -> dict[str, Any]:
        res = {"summary": summary, "project_id": entry["project_id"], "place_id": entry["place_id"], **rest}
        if entry.get("synthetic"):
            res["synthetic_place"] = True
        return A.clean(res)

    def brief(findings: list[dict], detail: bool, limit: int = 8) -> dict:
        """Ranked findings: errors and warnings first (info only with detail=true)."""
        if detail:
            return {"findings": findings}
        act = [f for f in findings if f["severity"] != "info"]
        out: dict[str, Any] = {"findings": [{"severity": f["severity"], "code": f["code"], "message": f["message"]} for f in act[:limit]]}
        if len(act) > limit:
            out["more_findings"] = len(act) - limit
        hidden = len(findings) - len(act) + max(0, len(act) - limit)
        if hidden:
            out["more"] = f"{hidden} more (info or beyond the top {limit}); pass detail=true"
        return out

    def archetype_list(ctx: A.Ctx, archetypes: list[str] | None) -> list[str] | None:
        if archetypes is None:
            return None
        unknown = [a for a in archetypes if a not in ctx.spec["archetypes"]]
        if unknown:
            raise ValueError(f"unknown archetype(s) {unknown}. Known: {sorted(ctx.spec['archetypes'])}")
        return archetypes

    def counts(findings: list[dict]) -> str:
        c = {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")}
        return f"{c['error']} error, {c['warning']} warning, {c['info']} info"

    # --- feedback (scoped) -----------------------------------------------------------------------------------------------------------
    def get_style_brief(project_id: str | None = None, place_id: str | None = None, focus: list[str] | None = None, max_chars: int = 1500) -> dict[str, Any]:
        """Pacing style brief for this place (global style plus the place's bands). Bands are PLACEHOLDERS until the user supplies theirs."""
        entry = entry_of(project_id, place_id)
        style, layers = places.layered_style(project, entry)
        bands = [{k: b[k] for k in ("id", "tiers", "min_minutes", "max_minutes", "archetypes")} for b in style.get("target_bands", [])]
        return head(f"style brief for {entry['project_id']}/{entry['place_id']}; bands from {'place file' if layers['place_bands'] else 'global placeholder style'}", entry,
                    brief=config.style_brief(style, focus or (), max_chars), bands=bands, bands_are_placeholders=bool(style.get("placeholder")),
                    ranges={k: {kk: vv for kk, vv in v.items() if kk in ("min", "max")} for k, v in style.get("ranges", {}).items()},
                    correction_dimensions=style.get("correction_dimensions", []))

    def find_past_corrections(request: str, project_id: str | None = None, place_id: str | None = None, k: int = 3, include_global: bool = True) -> dict[str, Any]:
        """Past corrections for THIS place (plus ones marked global) relevant to a request. Read before planning."""
        entry = entry_of(project_id, place_id)
        rows = [{**r, "scope": "place"} for r in feedback.corrections_for(sproj_of(entry), request, k)]
        if include_global:
            rows += [{**r, "scope": "global"} for r in feedback.corrections_for(global_proj(), request, k)]
        rows = sorted(rows, key=lambda r: -r["score"])[:k]
        return head(f"{len(rows)} correction(s) for {entry['project_id']}/{entry['place_id']}", entry, corrections=rows)

    def record_run(request: str, project_id: str | None = None, place_id: str | None = None, constraints: dict | None = None, retrieved: list[str] | None = None,
                   tools: list[str] | None = None, recipes: list[str] | None = None, outputs: list[str] | None = None, preview: str | None = None,
                   model: str | None = None, is_global: bool = False) -> dict[str, Any]:
        """Save a request (or playtest note) and its results for this place; returns run_id. is_global=true stores it for every place."""
        entry = entry_of(project_id, place_id)
        target = global_proj() if is_global else sproj_of(entry)
        cons = {**(constraints or {}), "project_id": entry["project_id"], "place_id": entry["place_id"], "global": bool(is_global)}
        rid = feedback.record_run(target, request=request, constraints=cons, retrieved=retrieved, tools=tools, recipes=recipes, outputs=outputs, preview=preview, model=model)
        return head(f"saved run {rid} for {entry['project_id']}/{entry['place_id']}{' (GLOBAL)' if is_global else ''}", entry, run_id=rid, global_=bool(is_global))

    def record_decision(run_id: str, decision: str, project_id: str | None = None, place_id: str | None = None, reason: str = "", corrections: list[dict] | None = None,
                        rating: int | None = None, promote_requested: bool = False, is_global: bool = False) -> dict[str, Any]:
        """Save the user's verdict (accept/reject/revise) on a run. Playtest note: corrections [{dimension: pacing, tier, felt: too_slow|too_fast, note}]. Use the user's words."""
        entry = entry_of(project_id, place_id)
        target = global_proj() if is_global else sproj_of(entry)
        dims = config.load_style(project).get("correction_dimensions")
        row = feedback.record_decision(target, run_id, decision, reason=reason, corrections=corrections, rating=rating, promote_requested=promote_requested, allowed_dimensions=dims)
        return head(f"saved {decision} for {run_id} ({entry['project_id']}/{entry['place_id']}{', GLOBAL' if is_global else ''})", entry, decision=row["decision"], global_=bool(is_global))

    def promote_run(run_id: str, kind: str, title: str, description: str, tags: list[str], path: str, project_id: str | None = None, place_id: str | None = None,
                    polarity: str = "positive", confirm: bool = False, is_global: bool = False) -> dict[str, Any]:
        """[rare] Add a reviewed run to this place's library (candidate unless confirm=true; ask the user first)."""
        entry = entry_of(project_id, place_id)
        target = global_proj() if is_global else sproj_of(entry)
        asset = feedback.promote_run(target, run_id, kind=kind, title=title, description=description, tags=tags, path=path, polarity=polarity, confirm=confirm)
        return head(f"{asset['status']} library entry {asset['id']}", entry, asset_id=asset["id"], status=asset["status"])

    # --- spec and simulation ------------------------------------------------------------------------------------------------------------
    def validate_economy_spec(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None,
                              spec_version: int | None = None) -> dict[str, Any]:
        """Validate this place's economy spec (ids, references, ranges, tiers, locked paths). Errors block every other tool."""
        entry = entry_of(project_id, place_id)
        raw, src = resolve_raw(entry, spec, spec_name, spec_version)
        findings = S.validate(raw)
        errors = S.errors_of(findings)
        summary: dict[str, Any] = {}
        if not errors:
            n = S.normalize(raw)
            summary = {"id": n["id"], "currencies": list(n["currencies"]), "sources": len(n["sources"]), "upgrades": len(n["upgrades"]), "tracks": sorted({u["track"] for u in n["upgrades"]}),
                       "boosts": len(n["boosts"]), "archetypes": list(n["archetypes"]), "rebirths": len(n["rebirths"]), "locked_values": len(S.locked_paths(n))}
        warns = [f for f in findings if f["severity"] == "warning"]
        return head(f"{'VALID' if not errors else 'INVALID'}: {len(errors)} error(s), {len(warns)} warning(s)", entry, ok=not errors, errors=errors[:8], warnings=warns[:8], spec_summary=summary, source=src)

    def simulate_progression(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                             archetypes: list[str] | None = None, days: int | None = None, seed: int | None = None, boost_mode: str = "archetype", detail: bool = False) -> dict[str, Any]:
        """Run the seeded simulator per archetype (boost_mode archetype|none|all). Lists each purchase minute and gap (active minutes). detail=true adds daily flows."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        if boost_mode not in ("archetype", "none", "all"):
            raise ValueError("boost_mode must be 'archetype', 'none' or 'all'")
        sims = ctx.sims(archetype_list(ctx, archetypes), boost_mode=boost_mode, seed=seed, days=days)
        res: dict[str, Any] = {}
        for n, sm in sims.items():
            row: dict[str, Any] = {"minutes_per_day": sm["minutes_per_day"], "purchases": [f"{p['id']} @{p['minute']} (+{p['gap_minutes']})" + (f" lap{p['lap']}" if p["lap"] else "") for p in sm["purchases"]],
                                   "rebirths": sm["rebirths"], "exhausted_after_days": None if sm["exhausted_at_minute"] is None else sm["exhausted_at_minute"] / sm["minutes_per_day"],
                                   "end_income_rate": sm["end"]["rates"]}
            if sm["pending"]:
                row["waiting_for"] = {k: sm["pending"].get(k) for k in ("id", "waited_minutes", "projected_gap_minutes")}
            if detail:
                row["daily"] = sm["daily"]
            res[n] = row
        first = next(iter(sims.values()))
        total = sum(len(s["purchases"]) for s in sims.values())
        return head(f"simulated {len(sims)} archetype(s) for {first['days']} days, seed {first['seed']}: {total} purchase(s); times are active minutes", entry, source=src, archetypes=res)

    def time_to_upgrade_table(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                              archetypes: list[str] | None = None, days: int | None = None, seed: int | None = None, boost_mode: str = "archetype", laps: str = "first",
                              detail: bool = False) -> dict[str, Any]:
        """Active minutes from the previous purchase to each tier, per archetype, against this place's target bands. Flags over/under band, wall, cliffs. detail=true adds every row."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        tt = A.time_to_upgrade(ctx, archetypes=archetype_list(ctx, archetypes), boost_mode=boost_mode, seed=seed, days=days, laps=laps)
        fl = A.sort_findings(tt["findings"])
        actionable = [f for f in fl if f["severity"] != "info"]
        extra: dict[str, Any] = {"rows": tt["rows"]} if detail else {}
        ph = " Bands are PLACEHOLDERS." if ctx.style.get("placeholder") else ""
        return head(f"{len(actionable)} pacing finding(s) ({counts(fl)}).{ph}", entry, source=src, bands_are_placeholders=bool(ctx.style.get("placeholder")),
                    per_tier=[{k: t[k] for k in ("archetype", "tier", "gap_minutes_min", "gap_minutes_max", "band")} for t in tt["per_tier"] if detail or t["archetype"] in ctx.reference],
                    **brief(fl, detail), **extra)

    def flow_report(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                    archetypes: list[str] | None = None, days: int | None = None, seed: int | None = None, boost_mode: str = "archetype", detail: bool = False) -> dict[str, Any]:
        """Per currency: total income, total sinks, net inflation, tail net share and the day content runs out. Flags runaway_inflation, content_exhausted, stalled. detail=true adds per-day rows."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.flow(ctx, archetypes=archetype_list(ctx, archetypes), boost_mode=boost_mode, seed=seed, days=days)
        flows = {}
        for n, f in res["flows"].items():
            flows[n] = {"exhausted_after_days": f["exhausted_after_days"], "rebirths": f["rebirths"], "currencies": {
                c: {**{k: v[k] for k in ("total_sources", "total_sinks", "net_inflation", "sink_ratio", "tail_net_share", "end_balance")}, **({"days": v["days"]} if detail else {})}
                for c, v in f["currencies"].items()}}
        fl = A.sort_findings(res["findings"])
        return head(f"{len(fl)} flow finding(s) ({counts(fl)})", entry, source=src, flows=flows, **brief(fl, detail))

    def find_dominant_strategies(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                                 days: int | None = None, seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """Choices (upgrades sharing a track and tier) where one option beats the others for every archetype, plus strategy labels that win every choice. detail=true adds per-archetype paybacks."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.dominant(ctx, seed=seed, days=days, forced_check=True)
        ref = ctx.reference[0]
        groups = []
        for g in res["groups"]:
            row = {"track": g["track"], "tier": g["tier"], "options": g["options"], "dominant": g["dominant"], "dominated": g["dominated"],
                   "payback_minutes": {o: r["payback_minutes"] for o, r in g["per_archetype"].get(ref, {}).items()}}
            if detail:
                row["per_archetype"] = g["per_archetype"]
                row["forced_outcomes"] = g["forced_outcomes"]
            groups.append(row)
        fl = A.sort_findings(res["findings"])
        return head(f"{res['contested_groups']} contested choice(s); {sum(1 for f in fl if f['severity'] != 'info')} dominance finding(s)", entry, source=src, groups=groups,
                    strategies=res["strategies"], **brief(fl, detail))

    def find_dead_options(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                          days: int | None = None, seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """Upgrades nobody should buy: payback above max_payback_minutes for every archetype, or no income effect. Lists only the bad ones unless detail=true."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.dead_options(ctx, seed=seed, days=days)
        bad = [r for r in res["rows"] if r["status"] in ("dead", "no_effect")]
        rows = res["rows"] if detail else [{"upgrade": r["upgrade"], "tier": r["tier"], "cost": r["cost"], "best_payback_minutes": r["best_payback_minutes"], "status": r["status"]} for r in bad]
        fl = A.sort_findings(res["findings"])
        return head(f"{len(bad)} dead or no-effect option(s) of {len(res['rows'])}; limit {res['max_payback_minutes']:g} active minutes", entry, source=src, options=rows, **brief(fl, detail))

    def monetisation_check(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                           days: int | None = None, seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """Boost effect on pace (free vs all boosts vs each boost) and whether free players hit a cliff. Relative pacing only; says nothing about revenue."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.monetisation(ctx, seed=seed, days=days)
        fl = A.sort_findings(res["findings"])
        sp = [r["speedup"] for r in res["rows"] if r["speedup"]]
        extra = {"rows": res["rows"]} if detail else {}
        return head(f"{len(res['boosts'])} boost(s); max speedup {max(sp):.2f}x (limit {res['max_boost_speedup']:g}x); {counts(fl)}" if sp else res.get("note", "no boosts"), entry, source=src,
                    per_boost=res["per_boost"], **brief(fl, detail), **extra)

    def check_economy(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                      days: int | None = None, seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """Run every analysis: one verdict (pass = no error/warning), ranked findings, headline metrics. Start here."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.check(ctx, seed=seed, days=days)
        ph = "; bands are PLACEHOLDERS" if ctx.style.get("placeholder") else ""
        return head(f"{res['verdict'].upper()} for {entry['project_id']}/{entry['place_id']}: {counts(res['findings'])}{ph}", entry, source=src, verdict=res["verdict"],
                    **brief(res["findings"], detail), metrics={k: round(v, 3) for k, v in res["metrics"].items()}, metrics_outside_ranges=sorted(f["metric"] for f in res["range_findings"]),
                    limits="relative pacing under assumed archetypes; not retention or revenue")

    def sensitivity(path: str, project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                    values: list[float] | None = None, pct: list[float] | None = None, archetypes: list[str] | None = None, days: int | None = None,
                    seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """[rare] Vary ONE value (path like upgrades.drill_2.cost) by absolute values or percents and show how each tier's purchase minute moves. Analysis only."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        res = A.sensitivity(ctx, path, values=values, pct=pct, archetypes=archetype_list(ctx, archetypes or ctx.reference), seed=seed, days=days)
        tables = {}
        for n, rows in res["tables"].items():
            tables[n] = [r for r in rows if detail or any(d not in (0, None) for d in r["minute_delta_vs_base"].values())]
        return head(f"varied {path} (base {res['base_value']}) over {len(res['variants']) - 1} variant(s); moved cells {res['moved_cells']}", entry, source=src, variants=res["variants"],
                    tables=tables, locked=res["locked"], note=res["note"])

    def compare_specs(a_project_id: str | None = None, a_place_id: str | None = None, b_project_id: str | None = None, b_place_id: str | None = None,
                      a_version: int | None = None, b_version: int | None = None, a_name: str | None = None, b_name: str | None = None, days: int | None = None,
                      seed: int | None = None, detail: bool = False) -> dict[str, Any]:
        """[rare] Compare the specs of two places (or two saved versions): changed values, purchase-minute differences, findings only in one. Comparing is allowed across places; changes are not."""
        ca, _, ea, sa = load(a_project_id, a_place_id, None, a_name, a_version)
        cb, _, eb, sb = load(b_project_id, b_place_id, None, b_name, b_version)
        res = A.compare(ca, cb, seed=seed, days=days)
        ref = ca.reference[0]
        timing = {n: [r for r in rows if detail or r["minute_delta"] not in (0, None)][: (60 if detail else 8)] for n, rows in res["timing"].items() if detail or n == ref}
        cross = (ea["project_id"], ea["place_id"]) != (eb["project_id"], eb["place_id"])
        vc = res["value_changes"]
        lim = 80 if detail else 10
        return A.clean({"summary": f"{len(vc)} value(s) differ between {ea['project_id']}/{ea['place_id']} and {eb['project_id']}/{eb['place_id']}"
                                   + ("; comparison only, no change can be applied across places" if cross else ""),
                        "a": {"project_id": ea["project_id"], "place_id": ea["place_id"], "hash": sa["hash"]}, "b": {"project_id": eb["project_id"], "place_id": eb["place_id"], "hash": sb["hash"]},
                        "value_changes": vc[:lim], **({"more_value_changes": len(vc) - lim} if len(vc) > lim else {}), "timing_differences": timing,
                        "findings_only_in_a": res["findings_only_in_a"][: lim // 2], "findings_only_in_b": res["findings_only_in_b"][: lim // 2]})

    def plot_curves(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                    archetypes: list[str] | None = None, days: int | None = None, seed: int | None = None, boost_mode: str = "archetype", currency: str | None = None,
                    log_y: bool = True, backend: str = "pillow") -> dict[str, Any]:
        """[rare] Render income and purchase curves per archetype to a PNG (Pillow; matplotlib only if requested). Writes a derived picture to this place's git-ignored output folder; never touches the spec."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        sims = ctx.sims(archetype_list(ctx, archetypes), boost_mode=boost_mode, seed=seed, days=days)
        if currency is not None and currency not in ctx.spec["currencies"]:
            raise ValueError(f"unknown currency '{currency}'. Known: {list(ctx.spec['currencies'])}")
        digest = S.spec_hash({"spec": ctx.spec, "a": list(sims), "d": days, "s": seed, "b": boost_mode, "c": currency, "l": log_y, "be": backend})
        folder = scope.resolve_inside(sproj_of(entry).output_dir / "plots", sproj_of(entry).allowed_roots)
        info = plot.render(sims, folder / f"{scope.safe_name(ctx.spec['id'])}-{digest}.png", title=f"{entry['project_id']}/{entry['place_id']} ({boost_mode} boosts)", currency=currency,
                           log_y=log_y, backend=backend)
        ser = plot.series(sims, currency)
        return head(f"wrote {info['width']}x{info['height']} PNG for {entry['project_id']}/{entry['place_id']}", entry, source=src, path=info["path"], backend=info["backend"],
                    series={n: {"final_rate": s["rate"][-1][1], "purchases": len(s["purchases"]) - 1} for n, s in ser.items()})

    def suggest_band_adjustments(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None,
                                 spec_version: int | None = None) -> dict[str, Any]:
        """[rare] From THIS place's saved playtest notes, archetype observations and accepted/rejected rebalances (plus ones marked global) suggest band, archetype and lock changes. Edits nothing."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        runs, decisions = [], []
        for p in (sproj_of(entry), global_proj()):
            runs += feedback.list_runs(p, 100000)
            decisions += manifest.read_jsonl(feedback._decisions_file(p))
        res = learning.suggest(ctx, runs, decisions)
        return head(f"{len([s for s in res['suggestions'] if s['type'] != 'need_more_reports'])} suggestion(s) from {res['evidence']['pacing_reports']} pacing report(s); "
                    f"only this place's notes and global ones were read", entry, source=src, **{k: v for k, v in res.items()})

    # --- importer ---------------------------------------------------------------------------------------------------------------------
    def emit_import_luau(project_id: str | None = None, place_id: str | None = None, studios: list[dict] | None = None, vendor_path: str | None = None,
                         tools_path: str | None = None, ores_path: str | None = None, max_items: int = 500) -> dict[str, Any]:
        """[rare] READ-ONLY Luau that dumps vendor items, tool stats and ore values as JSON. Needs 'studios' (list_roblox_studios output) and refuses unless the open place IS this place."""
        entry = entry_of(project_id, place_id)
        hit = places.match_studio(entry, studios)
        code = luau.build_import_luau(vendor_path=vendor_path, tools_path=tools_path, ores_path=ores_path, max_items=max_items,
                                      place=(*places.embeddable_identity(entry), f"{entry['project_id']}/{entry['place_id']}"))
        return head(f"import dump for {entry['project_id']}/{entry['place_id']}; open Studio matched by {hit['matched_by']}; forward to execute_luau (Edit) on studio_id {hit['studio_id']}", entry,
                    luau=code, lint=luau.lint(code), studio=hit, forward_to="Roblox Studio's built-in MCP server: execute_luau",
                    next="pass the printed JSON to normalise_import; the script also refuses to run in any other place. Not run in real Studio by this package.")

    def normalise_import(project_id: str | None = None, place_id: str | None = None, import_json: dict | str | None = None, field_map: dict | None = None, days: int = 14,
                         default_currency: str | None = None, detail: bool = False) -> dict[str, Any]:
        """[rare] Turn the dumped JSON into a DRAFT spec for this place, listing every assumption and unresolved field. Saves nothing."""
        entry = entry_of(project_id, place_id)
        if import_json is None:
            raise ValueError("import_json is required (the JSON printed by the dump script)")
        if isinstance(import_json, str):
            try:
                import_json = json.loads(import_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"import_json is not valid JSON: {exc}") from exc
        res = importer.normalise(import_json, field_map=field_map, game=entry["place_alias"], days=days, default_currency=default_currency)
        errs = S.errors_of(res["findings"])
        return head(f"draft spec: {len(res['spec']['upgrades'])} upgrade(s), {len(res['spec']['sources'])} source(s); {len(res['assumptions'])} assumption(s), {len(res['unresolved'])} unresolved; "
                    f"{'valid' if not errs else 'INVALID'}", entry, ok=res["ok"], spec=res["spec"], assumptions=res["assumptions"], unresolved=res["unresolved"],
                    mapping=res["mapping"] if detail else len(res["mapping"]), validation_errors=errs[:5])

    # --- write tools (dry-run by default) ---------------------------------------------------------------------------------------------
    def propose_rebalance(project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None, spec_version: int | None = None,
                          finding_code: str | None = None, upgrade: str | None = None, archetype: str | None = None, currency: str | None = None, track: str | None = None,
                          max_changes: int = 2, days: int | None = None, seed: int | None = None, dry_run: bool = True, detail: bool = False) -> dict[str, Any]:
        """Smallest set of value changes (1-2) that clears ONE finding without new warnings; locked values never change. Applies to this one place. dry_run=false writes a NEW spec file."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        if not isinstance(max_changes, int) or not 1 <= max_changes <= 2:
            raise ValueError("max_changes must be 1 or 2")
        base = A.check(ctx, seed=seed, days=days)
        target = R.select_finding(base["findings"], finding_code, upgrade=upgrade, archetype=archetype, currency=currency, track=track)
        res = R.propose(ctx, base, target, max_changes=max_changes, seed=seed, days=days)
        if not res["found"]:
            return head(f"no safe fix found for {target['code']}: {res['reason']}", entry, dry_run=dry_run, found=False, target={"code": target["code"], "message": target["message"]},
                        candidates=res["candidates_considered"], skipped=[f"{x['path']} ({x['reason']})" for x in res["skipped"]],
                        locked_skipped=[x["path"] for x in res["skipped"] if x["reason"] == "locked"], hint=res["hint"])
        proposed = R.apply_to_raw(raw, res["changes"])
        changed = {c["path"] for c in S.diff_values(raw, proposed)}
        if changed != {c["path"] for c in res["changes"]}:
            raise ValueError("internal error: the proposed spec differs from the input in more values than reported")
        locked = set(S.locked_paths(ctx.spec))
        sp = sproj_of(entry)
        digest = S.spec_hash(proposed)
        dest = scope.resolve_inside(sp.output_dir / "rebalance", sp.allowed_roots) / f"{scope.safe_name(ctx.spec['id'])}-{digest}.yaml"
        plan = dryrun.Plan(f"Rebalance '{target['code']}' for {entry['project_id']}/{entry['place_id']}: {len(res['changes'])} value(s)")
        for c in res["changes"]:
            plan.add("set", c["path"], f"{c['before']} -> {c['after']}")
        plan.add("file", str(dest), "new file; the place's economy.yaml is not modified")
        rows = res["before_after"] if detail else [r for r in res["before_after"] if r["gap_before"] != r["gap_after"]][:14]
        one = ", ".join(f"{c['path']} {c['before']} -> {c['after']}" for c in res["changes"])
        out = head(f"{'DRY RUN: ' if dry_run else ''}{target['code']} fixed by {one}; verdict {res['verdict_before']} -> {res['verdict_after']}", entry, dry_run=dry_run, found=True,
                   target={"code": target["code"], "message": target["message"]}, changes=A.clean(res["changes"], 6), locked_values_untouched=not (changed & locked),
                   locked_skipped=[s["path"] for s in res["skipped"] if s["reason"] == "locked"], related_also_cleared=res["related_findings_also_cleared"],
                   new_findings_after=[{"code": f["code"], "message": f["message"]} for f in res["findings_after"]], timing_changes=rows, trials=res["trials"], plan=plan.to_dict(dry_run))
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = S.dump_yaml(proposed)
            if dest.exists() and dest.read_text(encoding="utf-8") != text:
                raise ValueError(f"refusing to overwrite {dest}")
            dest.write_text(text, encoding="utf-8")
            out["written"] = str(dest)
            out["next"] = "review the file, save_spec_version it, then export_values_luau (spec_name = that version)"
        return out

    def export_values_luau(project_id: str | None = None, place_id: str | None = None, studios: list[dict] | None = None, spec: dict | None = None, spec_name: str | None = None,
                           spec_version: int | None = None, all_values: bool = False, config_name: str = "EconomyValues_Proposed", parent_path: str = "ReplicatedStorage.Config",
                           verify_on_mock: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """[rare] Luau that creates ONE NEW config ModuleScript with this place's proposed values (changed ones vs the place's economy.yaml unless all_values). Embeds and checks the place; never overwrites."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        hit = places.match_studio(entry, studios)
        luau.validate_name(config_name)
        luau.validate_path(parent_path, "parent_path")
        identity = places.embeddable_identity(entry)
        label = f"{entry['project_id']}/{entry['place_id']}"
        baseline = None
        if not all_values and (spec is not None or spec_name is not None):
            baseline = S.normalize(S.load_spec(Path(entry["spec_path"])))
        values, previous = luau.values_for_export(ctx.spec, baseline)
        if not values:
            raise ValueError("nothing to export: the spec has no differences from this place's economy.yaml (pass all_values=true to export everything)")
        module = luau.values_module_source(values, spec_id=ctx.spec["id"], spec_hash=src["hash"], previous=previous, label=label)
        code = luau.build_export_luau(module, parent_path=parent_path, config_name=config_name, n_values=len(values), spec_hash=src["hash"], place=(*identity, label))
        sp = sproj_of(entry)
        dest = scope.resolve_inside(sp.output_dir / "export", sp.allowed_roots) / f"{scope.safe_name(config_name)}-{S.spec_hash({'c': code})}.luau"
        plan = dryrun.Plan(f"Emit Luau creating new ModuleScript '{config_name}' under {parent_path} for {label}: {len(values)} value(s)")
        plan.add("file", str(dest), "reviewable Luau")
        plan.add("studio", f"{parent_path}.{config_name}", f"execute_luau on studio_id {hit['studio_id']}; refuses in any other place, fails if the name exists")
        res = {"dry_run": dry_run, "mode": "changed values only" if baseline else "all values", "values": len(values), "studio": hit, "lint": luau.lint(code), "plan": plan.to_dict(dry_run)}
        if baseline:
            res["changed"] = [{"path": p, "before": (previous or {}).get(p), "after": v} for p, v in sorted(values.items())][:40]
        if verify_on_mock:
            res["mock_run"] = luau.verify_export_on_mock(code, module, values, parent_path, config_name, open_place=(entry["studio_name"], identity[0]))
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.read_text(encoding="utf-8") != code:
                raise ValueError(f"refusing to overwrite {dest}")
            dest.write_text(code, encoding="utf-8")
            res.update({"luau": code, "module_source": module, "luau_path": str(dest),
                        "note": "Studio may refuse to write Script.Source from execute_luau; then paste module_source into a new ModuleScript by hand. Not verified in real Studio."})
        return head(f"{'DRY RUN: ' if dry_run else ''}{len(values)} value(s) for {label} as new module {config_name}", entry, source=src, **res)

    def save_spec_version(name: str, project_id: str | None = None, place_id: str | None = None, spec: dict | None = None, spec_name: str | None = None,
                          spec_version: int | None = None, label: str = "", dry_run: bool = True) -> dict[str, Any]:
        """[rare] Save a spec as a numbered version for this place (copy-on-write). dry_run=true shows the next version number."""
        ctx, raw, entry, src = load(project_id, place_id, spec, spec_name, spec_version)
        key = f"spec-{scope.safe_name(name)}"
        versions = dryrun.Versioner(sproj_of(entry))
        history = versions.history(key)
        plan = dryrun.Plan(f"Save '{ctx.spec['id']}' as version {len(history) + 1} of '{scope.safe_name(name)}' for {entry['project_id']}/{entry['place_id']}")
        plan.add("version", key, label or "no label")
        res: dict[str, Any] = {"dry_run": dry_run, "name": scope.safe_name(name), "existing_versions": [h["version"] for h in history], "plan": plan.to_dict(dry_run)}
        if not dry_run:
            saved = versions.save(key, S.dump_yaml(raw), suffix=".yaml", label=label)
            res["saved"] = {"version": saved["version"], "sha": saved["sha"], "unchanged": saved.get("unchanged", False)}
        return head(f"{'DRY RUN: would save' if dry_run else 'saved'} version of {scope.safe_name(name)} for {entry['project_id']}/{entry['place_id']}", entry, source=src, **res)

    fns = [get_style_brief, find_past_corrections, validate_economy_spec, check_economy, simulate_progression, time_to_upgrade_table, flow_report, find_dominant_strategies,
           find_dead_options, monetisation_check, sensitivity, compare_specs, plot_curves, suggest_band_adjustments, emit_import_luau, normalise_import]
    writers = [record_run, record_decision, promote_run, propose_rebalance, export_values_luau, save_spec_version]
    specs = [ToolSpec(f.__name__, f, f.__doc__, read_only=True) for f in fns]
    specs += [ToolSpec(f.__name__, f, f.__doc__, read_only=False, idempotent=f.__name__ not in ("record_run", "record_decision")) for f in writers]
    return specs
