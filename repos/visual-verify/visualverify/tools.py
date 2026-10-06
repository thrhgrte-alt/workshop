"""MCP tools for visual measurement and verification.

Rules every tool follows: it needs ``project_id`` (and takes ``place_id``) and REFUSES without a registered one (it never guesses); it states which project/place the
answer is for; it measures with Pillow and NumPy only (no model, no network, no randomness) so the same input gives the same output; it reports every number
WITH the limit it was compared with and whether it passed, and says what was not measured; images are read only from folders the project may read and are never
uploaded; output is a one-line ``summary`` first, failed checks ranked and capped (``limits.findings_cap``), details only with ``detail=true``; write tools default to
``dry_run=true`` and write only under the private workspace. The server never talks to Studio or Blender: screenshots come from the hub's ``screen_capture``, saved to disk.
Tools whose description starts with ``[rare]`` can be left out (``VISUALVERIFY_DISABLE_GROUPS=rare``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image

from . import learning_params as LP
from .domain import diff as D
from .domain import engine as E
from .domain import hubshot
from .domain import imageio as IO
from .domain import learning as LN
from .domain import pixels as P
from .domain import profiles as PF
from .domain import projects as PJ
from .guide_adapter import ToolSpec, config, dryrun, feedback, observe, scope as S, telemetry

RARE = ("list_thresholds", "promote_run", "get_target_profile")
MEASURE_FOR_RUN = tuple(E.RUNNERS)
ACTIONS = {"accept": "accept", "reject": "reject", "revise": "edit"}


def make_tools(project) -> list[ToolSpec]:
    def ctx_for(project_id: str | None, place_id: str | None) -> E.Ctx:
        reg = PJ.load_registry(project)
        sc = S.require_scope(project_id, place_id or None, registry=reg)
        return E.Ctx(project, sc, LP.Thresholds(project, sc), PJ.read_roots(project, reg, sc), profile_dir=PJ.scope_dir(project, sc),
                     project_dir=PJ.scope_dir(project, S.Scope(sc.project_id)))

    def state(ctx: E.Ctx) -> dict:
        reg_entry = PJ.load_registry(project).project(ctx.scope.project_id)
        out = {"project_id": ctx.scope.project_id, "place_id": ctx.scope.place_id}
        if reg_entry.synthetic:
            out["synthetic_project"] = True
        return out

    def answer(ctx: E.Ctx, rep: dict, detail: bool) -> dict[str, Any]:
        return E.render(ctx, rep, detail, state(ctx))

    # --- read-only measurements --------------------------------------------------------------------------------------------------------
    def measure_image(path: str, project_id: str | None = None, place_id: str | None = None, profile: str | None = None, target_palette: list[str] | None = None,
                      dimensions: list[str] | None = None, levels: int = 3, detail: bool = False) -> dict[str, Any]:
        """Measure one image: dominant colours, saturation/value, luminance contrast and 3/5-level value map, edge density and distribution (too flat/noisy), silhouette descriptors. Checks against thresholds, a target_palette or a saved profile. Numbers with limits and pass/fail; never a judgement of taste."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.measure_image(ctx, path, profile, target_palette, dimensions, levels), detail)

    def compare_to_reference(candidate: str, project_id: str | None = None, place_id: str | None = None, reference: str | None = None, profile: str | None = None,
                             method: str = "auto", reference_method: str = "auto", dimensions: list[str] | None = None, detail: bool = False) -> dict[str, Any]:
        """Compare an image with a reference image (or a saved target profile): silhouette IoU and offsets, palette distance, luminance-histogram distance, edge-density ratio. Each number is reported with its limit and pass/fail."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.compare_to_reference(ctx, candidate, reference, profile, method, reference_method, dimensions), detail)

    def silhouette_iou(candidate: str, reference: str, project_id: str | None = None, place_id: str | None = None, method: str = "auto", reference_method: str = "auto",
                       detail: bool = False) -> dict[str, Any]:
        """Silhouette masks (alpha, plain background, luminance, or an explicit mask image) and their IoU, centroid offset and bounding-box offset. Assumes a plain background or alpha."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.silhouette_iou(ctx, candidate, reference, method, reference_method), detail)

    def palette_distance(path: str, project_id: str | None = None, place_id: str | None = None, target_palette: list[str] | None = None, profile: str | None = None,
                         weights: list[float] | None = None, detail: bool = False) -> dict[str, Any]:
        """Distance (CIE76 delta-E, both directions) between an image's dominant colours and a target palette (hex list) or a saved profile's palette."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.palette_distance(ctx, path, target_palette, profile, weights), detail)

    def check_tiling(path: str, project_id: str | None = None, place_id: str | None = None, detail: bool = False) -> dict[str, Any]:
        """Seam score of a tile (wrap-around step vs the steps beside it, worst axis) and repetition inside the tile (autocorrelation peak outside the main lobe). Full resolution only."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.check_tiling(ctx, path), detail)

    def check_pbr_ranges(project_id: str | None = None, place_id: str | None = None, albedo: str | None = None, roughness: str | None = None, metalness: str | None = None,
                         normal: str | None = None, detail: bool = False) -> dict[str, Any]:
        """Channel ranges of PBR maps: albedo luminance bounds, roughness/metalness grayscale and spread, normal-map unit length and +Z, equal map sizes. Limits are placeholders (verify_against_current_docs)."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.check_pbr_ranges(ctx, albedo, roughness, metalness, normal), detail)

    def diff_images(before: str, after: str, project_id: str | None = None, place_id: str | None = None, resize: bool = False, detail: bool = False) -> dict[str, Any]:
        """Numeric before/after difference: MAE, RMSE, PSNR, changed-pixel fraction and the changed bounding box. Same-size images (resize=true resamples 'after'). Use render_diff_image for a picture."""
        ctx = ctx_for(project_id, place_id)
        return answer(ctx, E.diff_images(ctx, before, after, resize), detail)

    def get_style_brief(project_id: str | None = None, place_id: str | None = None, focus: list[str] | None = None, max_chars: int = 1500, detail: bool = False) -> dict[str, Any]:
        """The style brief and the pass/fail thresholds in force for this project/place (placeholders until you replace them), and the correction dimensions you can use."""
        ctx = ctx_for(project_id, place_id)
        style = S.load_layered_style(project, ctx.scope)
        meta = ctx.th.meta
        checks = [f"{n} {m['op']} {ctx.th(n)}" + ("" if ctx.th.source(n) == "default" else f" ({ctx.th.source(n)})") for n, m in meta.items() if m.get("kind") == "check"]
        out: dict[str, Any] = {"summary": f"style brief and {len(checks)} thresholds for {ctx.scope.label}; every default is a PLACEHOLDER until you or your decisions replace it", **state(ctx),
                               "brief": config.style_brief(style, focus or (), max_chars), "thresholds": checks, "correction_dimensions": style.get("correction_dimensions", []),
                               "style_layers": {k: v for k, v in (style.get("_layers") or {}).items() if v != "global"}}
        if detail:
            out["settings"] = {n: ctx.th(n) for n, m in meta.items() if m.get("kind") == "setting"}
        return out

    def find_past_corrections(request: str, project_id: str | None = None, place_id: str | None = None, k: int = 3, include_global: bool = True) -> dict[str, Any]:
        """Past accept/reject decisions with corrections for THIS project/place (and ones marked global) relevant to a request. Read before judging a result."""
        ctx = ctx_for(project_id, place_id)
        rows = feedback.find_past_corrections(project, request, max(1, min(int(k), 10)), scope=ctx.scope, include_global=include_global, strict_scope=True)
        return {"summary": f"{len(rows)} correction(s) for {ctx.scope.label}", **state(ctx), "corrections": rows}

    def get_target_profile(project_id: str | None = None, place_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        """[rare] List the saved target profiles for this project/place, or show one profile's numbers (palette, ranges, silhouette descriptors)."""
        ctx = ctx_for(project_id, place_id)
        if name is None:
            names = ctx.profile_names()
            return {"summary": f"{len(names)} target profile(s) for {ctx.scope.label}", **state(ctx), "profiles": names}
        return {"summary": f"target profile '{name}' for {ctx.scope.label}", **state(ctx), "profile": ctx.profile(name)}

    def list_thresholds(project_id: str | None = None, place_id: str | None = None, detail: bool = False) -> dict[str, Any]:
        """[rare] Every named threshold: current value, where it came from (default or learned), range and largest step. detail=true adds descriptions."""
        ctx = ctx_for(project_id, place_id)
        ps = LP.store(project)
        snap = ps.snapshot(ctx.scope)
        rows = []
        for n, m in ctx.th.meta.items():
            s = snap[n]
            r = {"name": n, "value": s["value"], "source": s["source"], "default": s["default"], "range": [m["min"], m["max"]], "step": m.get("step"), "kind": m.get("kind"), "locked": bool(m.get("locked"))}
            if detail:
                r["what"] = m.get("what")
            rows.append(r)
        learned = [r["name"] for r in rows if r["source"] != "default"]
        return {"summary": f"{len(rows)} thresholds for {ctx.scope.label}; {len(learned)} differ from the shipped placeholder default", **state(ctx), "learned": learned,
                "thresholds": rows if detail else [{k: r[k] for k in ("name", "value", "source")} for r in rows]}

    # --- writes (dry run by default) ---------------------------------------------------------------------------------------------------
    def save_target_profile(name: str, project_id: str | None = None, place_id: str | None = None, reference: str | None = None, palette: list[str] | None = None,
                            saturation_range: list[float] | None = None, value_range: list[float] | None = None, luminance_range: list[float] | None = None,
                            edge_density_range: list[float] | None = None, contrast_min: float | None = None, description: str = "", margin: float | None = None,
                            include_silhouette: bool = False, dry_run: bool = True, expect: str | None = None) -> dict[str, Any]:
        """Save a target profile (numbers only, never the image) from a reference image and/or explicit values, under this project/place. Dry run by default: shows the profile; dry_run=false writes it (previous versions are kept)."""
        ctx = ctx_for(project_id, place_id)
        PF.check_name(name)
        measured, pal, src, sil = None, [], {}, None
        if reference is None and not (palette or saturation_range or value_range or luminance_range or edge_density_range or contrast_min is not None):
            raise ValueError("give a reference image and/or explicit values (palette, saturation_range, value_range, luminance_range, edge_density_range, contrast_min)")
        if reference:
            rep = E.measure_image(ctx, reference)
            d, m = rep["detail"], rep["measured"]
            measured = {"saturation": d["saturation_value"]["saturation"], "value": d["saturation_value"]["value"], "luminance": m["luminance"], "edges": m["edges"]}
            pal = [{"hex": c["hex"], "share": c["share"]} for c in d["dominant"]]
            src = {"image": ctx.images[0]["name"], "sha256": ctx.images[0]["sha256"], "size": ctx.images[0]["size"]}
            if include_silhouette:
                sil = {k: m["silhouette"][k] for k in ("fill", "bbox_norm", "centroid_norm") if m["silhouette"].get(k) is not None}
        if palette:
            pal = [{"hex": P.to_hex(P.hex_to_rgb(h)), "share": round(1.0 / len(palette), 4)} for h in palette]
        overrides = {"saturation_mean": saturation_range, "value_mean": value_range, "luminance_mean": luminance_range, "edge_density": edge_density_range, "contrast_min": contrast_min}
        prof = PF.build(name, description=description, source=src, palette=pal, measured=measured, margin=ctx.th("palette.profile_margin") if margin is None else float(margin),
                        overrides=overrides, silhouette=sil)
        base = PJ.scope_dir(project, ctx.scope)
        target = PF.path_for(base, name)
        text = PF.dumps(prof)
        plan = dryrun.Plan(f"save target profile '{name}' for {ctx.scope.label}")
        existing = target.exists()
        plan.add("write", str(target.relative_to(project.workspace)) if project.workspace in target.parents else target.name,
                 f"{len(pal)} palette colour(s), ranges {sorted(prof['ranges'])}" + (", silhouette descriptors" if sil else "") + ("; replaces the current file (the old version is kept)" if existing else ""))
        if reference:
            plan.warnings.append("the reference image itself is not copied or uploaded: only the numbers above are stored, plus its file name and SHA-256")

        def apply(_plan) -> list[str]:
            dryrun.Versioner(S.project_for_scope(project, ctx.scope)).save(f"profile-{name}", text, label="save_target_profile")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            return [str(target)]

        res = dryrun.run_plan(plan, apply, apply=not dry_run, journal=base / "journal.jsonl", expect=expect)
        return {**res, "summary": f"{'DRY RUN: would save' if dry_run else 'saved'} target profile '{name}' for {ctx.scope.label}", **state(ctx), "profile": prof}

    def render_diff_image(before: str, after: str, project_id: str | None = None, place_id: str | None = None, mode: str = "side_by_side", gain: float = 4.0,
                          name: str | None = None, resize: bool = False, dry_run: bool = True, overwrite: bool = False) -> dict[str, Any]:
        """Render a before/after picture: side_by_side (before | after | amplified difference) or difference only, as a PNG in the private workspace. Dry run by default; dry_run=false writes. Returns the numeric summary too."""
        ctx = ctx_for(project_id, place_id)
        rep = E.diff_images(ctx, before, after, resize)
        a, b = E._load_exact_or_reduced(ctx, before), E._load_exact_or_reduced(ctx, after)
        if a.rgb.shape != b.rgb.shape:
            b = IO.resized_to(b, a.rgb.shape[1], a.rgb.shape[0])
        fname = PF.check_name(name) if name else f"diff-{a.sha256[:8]}-{b.sha256[:8]}-{mode}"
        base = PJ.scope_dir(project, ctx.scope)
        target = base / "output" / "diffs" / f"{fname}.png"
        img = D.render(a, b, mode, gain)
        plan = dryrun.Plan(f"render {mode} diff image for {ctx.scope.label}")
        plan.add("write", f"projects/{S.safe_name(ctx.scope.project_id)}/.../output/diffs/{target.name}", f"{img.width}x{img.height} PNG, gain {gain}")
        data = D.png_bytes(img) if not dry_run else b""

        def apply(_plan) -> list[str]:
            if target.exists() and not overwrite and target.read_bytes() != data:
                raise ValueError(f"{target.name} already exists with different content: pass overwrite=true or another name")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return [str(target)]

        res = dryrun.run_plan(plan, apply, apply=not dry_run, journal=base / "journal.jsonl")
        out = {**res, **answer(ctx, rep, False)}
        out["summary"] = f"{'DRY RUN: would write' if dry_run else 'wrote'} {mode} diff image {img.width}x{img.height}; " + rep_line(ctx, rep)
        if not dry_run:
            out["path"], out["bytes"] = str(target), len(data)
        return out

    def rep_line(ctx: E.Ctx, rep: dict) -> str:
        return E.render(ctx, rep, False, {})["summary"]

    def ingest_capture(path: str, name: str, project_id: str | None = None, place_id: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Turn a saved hub screen_capture result (raw image or JSON with base64) into a local PNG in the private workspace. Parser is schema_unverified (no real capture existed). Dry run by default."""
        ctx = ctx_for(project_id, place_id)
        PF.check_name(name)
        src = S.resolve_inside(path, ctx.roots) if path else None
        if src is None or not src.is_file():
            raise ValueError("path must be a saved capture file inside the allowed folders")
        raw = src.read_bytes()
        img, info = hubshot.decode(raw, int(ctx.th("limits.max_file_bytes")), int(ctx.th("limits.max_pixels")))
        rgba = img.convert("RGBA") if "A" in img.getbands() or img.mode == "P" else img.convert("RGB")
        buf = io.BytesIO()
        rgba.save(buf, format="PNG", compress_level=6, optimize=False)
        data = buf.getvalue()
        base = PJ.scope_dir(project, ctx.scope)
        target = base / "captures" / f"{name}.png"
        plan = dryrun.Plan(f"ingest capture as {name}.png for {ctx.scope.label}")
        plan.add("write", f"captures/{name}.png", f"{rgba.width}x{rgba.height} {rgba.mode} PNG re-encoded from {info.get('form')}")
        plan.warnings.append("input_schema is schema_unverified: no real screen_capture result was available when the parser was written")

        def apply(_plan) -> list[str]:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return [str(target)]

        res = dryrun.run_plan(plan, apply, apply=not dry_run, journal=base / "journal.jsonl")
        out = {**res, "summary": f"{'DRY RUN: would write' if dry_run else 'wrote'} capture {name}.png ({rgba.width}x{rgba.height}); parser is schema_unverified", **state(ctx),
               "input_schema": hubshot.SCHEMA_STATUS, "form": info.get("form"), "size": [rgba.width, rgba.height]}
        if not dry_run:
            out["image_path"] = str(target)
        return out

    # --- feedback (scoped) -------------------------------------------------------------------------------------------------------------
    def record_run(request: str, project_id: str | None = None, place_id: str | None = None, measure: dict | None = None, constraints: dict | None = None,
                   model: str | None = None) -> dict[str, Any]:
        """Save a request and, optionally, the measurement it was about: measure={"tool": "measure_image", "args": {...}} is RE-RUN here (deterministic) so the numbers are stored exactly, not retyped. Returns run_id for record_decision."""
        ctx = ctx_for(project_id, place_id)
        extra: dict[str, Any] = {}
        tools_used: list[str] = []
        if measure is not None:
            tool = measure.get("tool") if isinstance(measure, dict) else None
            args = dict(measure.get("args") or {}) if isinstance(measure, dict) else {}
            if tool not in MEASURE_FOR_RUN:
                raise ValueError(f"measure.tool must be one of {list(MEASURE_FOR_RUN)}")
            for banned in ("project_id", "place_id", "detail"):
                args.pop(banned, None)
            rep = E.RUNNERS[tool](ctx, **args)
            summ = E.render(ctx, rep, False, {})["summary"]
            extra["measurement"] = {"tool": tool, "summary": summ, "checks": E.compact_rows(rep["rows"]), "images": rep["images"]}
            tools_used = [tool]
        rid = feedback.record_run(project, request=request, constraints={**(constraints or {}), "project_id": ctx.scope.project_id, "place_id": ctx.scope.place_id},
                                  tools=tools_used, extra=extra, model=model, scope=ctx.scope, strict_scope=True)
        n = len(extra.get("measurement", {}).get("checks", []))
        return {"summary": f"saved run {rid} for {ctx.scope.label}" + (f" with {n} measured check(s)" if n else " (no measurement attached)"), **state(ctx), "run_id": rid}

    def record_decision(run_id: str, decision: str, project_id: str | None = None, place_id: str | None = None, reason: str = "", corrections: list[dict] | None = None,
                        rating: int | None = None, promote_requested: bool = False, is_global: bool = False) -> dict[str, Any]:
        """Save the user's verdict (accept/reject/revise) on a run with its measurements, so thresholds can later be proposed toward what they accept. corrections=[{dimension, note}]; use the user's words. Never changes a threshold itself."""
        ctx = ctx_for(project_id, place_id)
        run = feedback.get_run(project, run_id)
        if run is None:
            raise ValueError(f"unknown run_id '{run_id}'")
        rs = S.scope_from_dict(run.get("scope"))
        if rs is None or rs.project_id != ctx.scope.project_id or (rs.place_id and ctx.scope.place_id and rs.place_id != ctx.scope.place_id):
            raise S.ScopeError(f"run {run_id} belongs to {rs.label if rs else 'no scope'}, not {ctx.scope.label}: a decision cannot be recorded across projects or places")
        dims = S.load_layered_style(project, ctx.scope).get("correction_dimensions", [])
        meas = (run.get("extra") or {}).get("measurement") or {}
        rows = meas.get("checks", [])
        case = LN.regression_case(decision, rows, corrections or [])
        row = feedback.record_decision(project, run_id, decision, reason=reason, corrections=corrections, rating=rating, promote_requested=promote_requested, allowed_dimensions=dims,
                                       scope=ctx.scope, mark_global=bool(is_global), regression_case=case, strict_scope=True)
        signals, considered = LN.signals_for(decision, rows, corrections or [], ctx.th.meta)
        log = observe.RunLog(project.workspace / "learning" / "observations.jsonl")
        oid = log.log_run(request=run["request"], scope=ctx.scope, tools=run.get("tools") or [], signals=signals, considered=considered,
                          output_chars=len(meas.get("summary", "")), extra={"feedback_run_id": run_id, "decision": decision})
        log.log_action(oid, ACTIONS[decision], note=reason, targets=[s["target"] for s in signals])
        return {"summary": f"saved {decision} for {run_id} ({ctx.scope.label}{', GLOBAL' if is_global else ''}); {len(signals)} threshold signal(s) logged. Thresholds change only through the learn commands (propose, gate, approve)",
                **state(ctx), "decision": row["decision"], "signals": signals[: int(ctx.th("limits.findings_cap"))], "regression_case_kept": case is not None, "global": bool(is_global)}

    def promote_run(run_id: str, kind: str, title: str, description: str, tags: list[str], path: str, project_id: str | None = None, place_id: str | None = None,
                    polarity: str = "positive", confirm: bool = False) -> dict[str, Any]:
        """[rare] Add a reviewed run's image to this project's example library (a candidate unless confirm=true; ask the user first). The image stays where it is."""
        ctx = ctx_for(project_id, place_id)
        IO.check_path(path, ctx.roots)
        asset = feedback.promote_run(project, run_id, kind=kind, title=title, description=description, tags=tags, path=path, polarity=polarity, confirm=confirm, scope=ctx.scope)
        return {"summary": f"{asset['status']} library entry {asset['id']} for {ctx.scope.label}", **state(ctx), "asset_id": asset["id"], "status": asset["status"]}

    fns = [measure_image, compare_to_reference, silhouette_iou, palette_distance, check_tiling, check_pbr_ranges, diff_images, get_style_brief, find_past_corrections, get_target_profile,
           list_thresholds]
    writers = [save_target_profile, render_diff_image, ingest_capture, record_run, record_decision, promote_run]
    specs = [ToolSpec(f.__name__, f, f.__doc__, group="rare" if f.__name__ in RARE else None) for f in fns]
    specs += [ToolSpec(f.__name__, f, f.__doc__, read_only=False, idempotent=f.__name__ not in ("record_run", "record_decision"), group="rare" if f.__name__ in RARE else None) for f in writers]
    if project.env("TELEMETRY") in ("1", "true", "yes"):  # opt in: a local usage log (sizes and timings) is written to the workspace
        specs = telemetry.Telemetry(project.workspace / "telemetry.jsonl").instrument(specs)
    return specs
