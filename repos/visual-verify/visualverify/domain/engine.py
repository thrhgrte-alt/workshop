"""The measurements, assembled into reports. Pure functions of (images, thresholds): the same input gives the same output.

Each function takes a :class:`Ctx` (scope, thresholds in force, folders images may be read from) and returns a *report*:
``{"subject", "rows" (every check), "measured" (headline numbers), "detail" (everything else), "warnings", "images", "not_measured"}``.
:func:`render` turns a report into the compact tool answer (one-line summary first, failed checks ranked and capped, passed checks with their limits).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import checks as C
from . import diff as D
from . import imageio as IO
from . import pbr as PB
from . import pixels as P
from . import profiles as PF
from . import silhouette as SL
from . import tiling as TL

DIMENSIONS = ("palette", "value", "edges", "silhouette")


@dataclass
class Ctx:
    project: Any
    scope: Any
    th: Any  # learning_params.Thresholds
    roots: list[Path]
    profile_dir: Path | None = None  # scope folder: profiles live in <profile_dir>/profiles and (fallback) in the project-level folder
    project_dir: Path | None = None
    images: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.roots = [Path(r) for r in self.roots]

    @property
    def label(self) -> str:
        return self.scope.label if self.scope is not None else "eval"

    def load(self, path: str | Path, exact: bool = False) -> IO.Loaded:
        """Read an image inside the allowed folders. ``exact`` (tiling, PBR, diff) keeps full resolution and refuses sides above limits.max_side_exact."""
        th = self.th
        im = IO.load(path, self.roots, max_bytes=int(th("limits.max_file_bytes")), max_pixels=int(th("limits.max_pixels")),
                     max_side=None if exact else int(th("limits.analysis_max_side")))
        if exact and max(im.size) > int(th("limits.max_side_exact")):
            raise IO.ImageRefused(f"'{im.name}' is {im.size[0]}x{im.size[1]}: this check works at full resolution and accepts at most {int(th('limits.max_side_exact'))} px per side "
                                  f"(limits.max_side_exact). Crop or resize it yourself first; it is not downscaled silently.")
        self.images.append({"name": im.name, "sha256": im.sha256[:12], "size": list(im.size)})
        return im

    def profile(self, name: str) -> dict:
        PF.check_name(name)
        for base in (self.profile_dir, self.project_dir):
            if base is not None:
                f = PF.path_for(base, name)
                if f.is_file():
                    import json

                    return PF.validate(json.loads(f.read_text(encoding="utf-8")))
        raise ValueError(f"no target profile '{name}' for {self.label} (or its project). Save one with save_target_profile; saved: {self.profile_names()}")

    def profile_names(self) -> list[str]:
        out: set[str] = set()
        for base in (self.profile_dir, self.project_dir):
            if base is not None and (Path(base) / "profiles").is_dir():
                out |= {f.stem for f in (Path(base) / "profiles").glob("*.json")}
        return sorted(out)


def _report(subject: str, rows: list[dict], measured: dict, detail: dict | None = None, *, warnings: list[str] | None = None, not_measured: list[str] | None = None,
            ctx: Ctx | None = None, extra: dict | None = None, verdict_note: str = "") -> dict:
    return {"subject": subject, "rows": rows, "measured": measured, "detail": detail or {}, "warnings": warnings or [], "not_measured": [*C.BASE_NOT_MEASURED, *(not_measured or [])],
            "images": list(ctx.images) if ctx else [], "extra": extra or {}, "note": verdict_note}


def _round(o: Any, nd: int = 4) -> Any:
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_round(v, nd) for v in o]
    if isinstance(o, (float, np.floating)):
        return round(float(o), nd)
    if isinstance(o, (int, np.integer)) and not isinstance(o, bool):
        return int(o)
    return o


def _tcheck(ctx: Ctx, cid: str, value, param: str, *, note: str = "", dimension: str = "", op: str | None = None) -> dict:
    """A check against a registered threshold: the operator, limit and 'where the limit came from' come from the thresholds file and the scope's learned values."""
    meta = ctx.th.meta[param]
    src = ctx.th.source(param)
    return C.check(cid, value, op or meta["op"], ctx.th(param), param=param, dimension=dimension or meta.get("dimension", ""), note=note, source="" if src == "default" else src)


def _desc_norm(d: dict) -> dict:
    w, h = d["size"]
    if d["bbox"] is None:
        return {"fill": d["fill"], "bbox_norm": None, "centroid_norm": None, "components": 0}
    b, c = d["bbox"], d["centroid"]
    return {"fill": d["fill"], "bbox_norm": [b[0] / w, b[1] / h, b[2] / w, b[3] / h], "centroid_norm": [c[0] / w, c[1] / h], "components": d["components"], "largest_share": d["largest_share"]}


def _composite_lum(im: IO.Loaded) -> np.ndarray:
    """Luminance for edge measurement: images with real transparency are composited over mid gray, so the subject's outline counts as an edge."""
    if im.has_alpha:
        a = im.alpha.astype(np.float64)[..., None]
        return P.luminance(im.rgb.astype(np.float64) * a + 0.5 * (1.0 - a))
    return P.luminance(im.rgb)


def _valid_or_refuse(im: IO.Loaded) -> np.ndarray:
    v = im.valid
    if not v.any():
        raise IO.ImageRefused(f"'{im.name}' has no opaque pixels (alpha < 0.5 everywhere): nothing to measure")
    return v


# --- measure_image -------------------------------------------------------------------------------------------------------------------------
def measure_image(ctx: Ctx, path: str, profile: str | None = None, target_palette: list[str] | None = None, dimensions: list[str] | None = None, levels: int = 3) -> dict:
    dims = _dims(dimensions)
    if levels not in (3, 5):
        raise ValueError("levels must be 3 or 5")
    prof = ctx.profile(profile) if profile else None
    th = ctx.th
    im = ctx.load(path)
    valid = _valid_or_refuse(im)
    rgb64 = im.rgb.astype(np.float64)
    rows: list[dict] = []
    measured: dict = {"size": [im.rgb.shape[1], im.rgb.shape[0]], "file_size": list(im.size)}
    detail: dict = {"mode": im.mode, "bit_depth": im.bit_depth, "sha256": im.sha256}
    warnings: list[str] = []
    if im.has_alpha:
        measured["transparent_fraction"] = float(1.0 - valid.mean())
        warnings.append(f"{measured['transparent_fraction']:.1%} of pixels are transparent: colour and value statistics use only the opaque pixels; edges are measured over mid gray")
    lum_valid = P.luminance(rgb64)[valid]
    ranges = (prof or {}).get("ranges", {})
    src = f"profile:{prof['name']}" if prof else ""

    if "palette" in dims:
        dom = P.dominant_colors(rgb64[valid], int(th("palette.k")))
        sv = P.sat_value_stats(rgb64, valid)
        measured["dominant"] = [f"{d['hex']} {d['share']:.3f}" for d in dom]
        measured["saturation"] = {"mean": sv["saturation"]["mean"], "p5": sv["saturation"]["p5"], "p95": sv["saturation"]["p95"]}
        measured["value"] = {"mean": sv["value"]["mean"], "p5": sv["value"]["p5"], "p95": sv["value"]["p95"]}
        detail["dominant"] = dom
        detail["saturation_value"] = sv
        tgt = [{"hex": h, "share": 1.0 / len(target_palette)} for h in target_palette] if target_palette else (prof or {}).get("palette")
        if tgt:
            pd = P.palette_distance(dom, [t["hex"] for t in tgt], [t["share"] for t in tgt])
            measured["palette_distance"] = {k: pd[k] for k in ("distance", "image_to_target", "target_to_image")}
            detail["palette_pairs"] = pd["pairs"]
            detail["palette_targets"] = pd["targets"]
            rows.append(_tcheck(ctx, "palette.distance", pd["distance"], "palette.distance_max", note="dominant colours are far from the target palette (CIE76 delta-E)"))
        else:
            rows.append(C.check("palette.distance", None, "<=", ctx.th("palette.distance_max"), param="palette.distance_max", dimension="palette", skipped="no target_palette or profile was given"))
        for key, rid in (("saturation_mean", "palette.saturation_mean"), ("value_mean", "palette.value_mean")):
            if key in ranges:
                v = sv["saturation" if key == "saturation_mean" else "value"]["mean"]
                rows.append(C.check(rid, v, "in", ranges[key], dimension="palette", source=src, note=f"outside the profile range {ranges[key]}"))

    if "value" in dims:
        vs = P.value_structure(lum_valid, th("value.dark_cut"), th("value.light_cut"))
        measured["luminance"] = {k: vs[k] for k in ("mean", "p5", "p50", "p95", "contrast")}
        measured["levels3" if levels == 3 else "levels5"] = vs["levels3"] if levels == 3 else vs["levels5"]
        detail["histogram8"] = vs["histogram"]
        detail["levels3"], detail["levels5"] = vs["levels3"], vs["levels5"]
        if "contrast_min" in ranges:
            rows.append(C.check("value.contrast", vs["contrast"], ">=", ranges["contrast_min"], dimension="value", source=src, param=None, note="luminance contrast below the profile minimum"))
        else:
            rows.append(_tcheck(ctx, "value.contrast", vs["contrast"], "value.contrast_min", note="low value range: 95th minus 5th percentile of luminance"))
        lowest = min(vs["levels3"], key=lambda k: vs["levels3"][k])
        rows.append(_tcheck(ctx, "value.level_min_share", vs["levels3"][lowest], "value.level_min_share", note=f"the '{lowest}' level holds {vs['levels3'][lowest]:.1%} of pixels"))
        if "luminance_mean" in ranges:
            rows.append(C.check("value.luminance_mean", vs["mean"], "in", ranges["luminance_mean"], dimension="value", source=src, note=f"outside the profile range {ranges['luminance_mean']}"))

    if "edges" in dims:
        es = P.edge_stats(_composite_lum(im), th("edges.gradient_threshold"))
        measured["edges"] = {"density": es["density"], "distribution": es["distribution"]}
        detail["edge_grid_density"] = es["grid_density"]
        detail["edge_pixels"] = es["edge_pixels"]
        if "edge_density" in ranges:
            rows.append(C.check("edges.density", es["density"], "in", ranges["edge_density"], dimension="edges", source=src,
                                note="too flat" if es["density"] < ranges["edge_density"][0] else "too noisy"))
        else:
            rows.append(_tcheck(ctx, "edges.density_min", es["density"], "edges.density_min", note="too flat: few edge pixels"))
            rows.append(_tcheck(ctx, "edges.density_max", es["density"], "edges.density_max", note="too noisy: many edge pixels"))
        if es["distribution"] is None:
            rows.append(C.check("edges.distribution", None, ">=", th("edges.distribution_min"), param="edges.distribution_min", dimension="edges", skipped="no edge pixels to distribute"))
        else:
            rows.append(_tcheck(ctx, "edges.distribution", es["distribution"], "edges.distribution_min", note="edge detail is concentrated in a few cells of a 4x4 grid"))

    if "silhouette" in dims:
        mask, info = SL.extract_mask(im, "auto", th("silhouette.bg_tolerance"))
        d = SL.describe(mask, th("silhouette.min_component_share"))
        nd = _desc_norm(d)
        measured["silhouette"] = {"method": info["method"], **{k: nd[k] for k in ("fill", "bbox_norm", "centroid_norm", "components")}}
        warnings += SL.mask_warnings(info, "silhouette")
        ps = (prof or {}).get("silhouette")
        if ps and nd["centroid_norm"] and nd["bbox_norm"]:
            cd = math.hypot(nd["centroid_norm"][0] - ps["centroid_norm"][0], nd["centroid_norm"][1] - ps["centroid_norm"][1])
            bd = max(abs(a - b) for a, b in zip(nd["bbox_norm"], ps["bbox_norm"]))
            rows.append(_tcheck(ctx, "silhouette.profile_centroid_offset", cd, "silhouette.centroid_offset_max", note="subject centroid is away from the profile's"))
            rows.append(_tcheck(ctx, "silhouette.profile_bbox_offset", bd, "silhouette.bbox_offset_max", note="subject bounding box is away from the profile's"))
    return _report("measure_image", rows, _round(measured), _round(detail), warnings=warnings, ctx=ctx,
                   not_measured=["whether the subject is what was asked for", "mask quality on a busy background"], extra={"profile": profile} if profile else {})


def _dims(dimensions: list[str] | None) -> set[str]:
    if dimensions is None:
        return set(DIMENSIONS)
    bad = [d for d in dimensions if d not in DIMENSIONS]
    if bad or not dimensions:
        raise ValueError(f"dimensions must be a non-empty list of {list(DIMENSIONS)} (got {dimensions})")
    return set(dimensions)


# --- silhouette / compare ------------------------------------------------------------------------------------------------------------------
def _silhouette_part(ctx: Ctx, cand: IO.Loaded, ref: IO.Loaded, method: str, ref_method: str) -> tuple[list[dict], dict, dict, list[str], IO.Loaded]:
    th = ctx.th
    h, w = cand.rgb.shape[:2]
    warnings: list[str] = []
    ar_c, ar_r = cand.size[0] / cand.size[1], ref.size[0] / ref.size[1]
    if abs(ar_c - ar_r) / ar_c > 0.01:
        warnings.append(f"aspect ratios differ ({cand.size[0]}x{cand.size[1]} vs {ref.size[0]}x{ref.size[1]}): the reference was stretched onto the candidate's grid, so overlap numbers mix framing with shape")
    ref_r = IO.resized_to(ref, w, h) if ref.rgb.shape[:2] != (h, w) else ref
    mc, ic = SL.extract_mask(cand, method, th("silhouette.bg_tolerance"))
    mr, ir = SL.extract_mask(ref_r, ref_method, th("silhouette.bg_tolerance"))
    warnings += SL.mask_warnings(ic, "candidate") + SL.mask_warnings(ir, "reference")
    cmp = SL.compare_masks(mc, mr)
    dc, dr = SL.describe(mc, th("silhouette.min_component_share")), SL.describe(mr, th("silhouette.min_component_share"))
    rows = [_tcheck(ctx, "silhouette.iou", cmp["iou"], "silhouette.iou_min", note="candidate and reference silhouettes overlap too little") if cmp["iou"] is not None
            else C.check("silhouette.iou", None, ">=", th("silhouette.iou_min"), param="silhouette.iou_min", dimension="silhouette", skipped="both masks are empty")]
    for rid, key, param, note in (("silhouette.centroid_offset", "centroid_offset", "silhouette.centroid_offset_max", "centroids are far apart"),
                                  ("silhouette.bbox_offset", "bbox_offset", "silhouette.bbox_offset_max", "bounding boxes are shifted")):
        rows.append(_tcheck(ctx, rid, cmp.get(key), param, note=note) if cmp.get(key) is not None else
                    C.check(rid, None, "<=", th(param), param=param, dimension="silhouette", skipped="a mask is empty"))
    measured = {"iou": cmp["iou"], "dice": cmp["dice"], "centroid_offset_px": cmp.get("centroid_offset_px"), "bbox_offset_px": cmp.get("bbox_offset_px"),
                "candidate": {"fill": dc["fill"], "bbox": dc["bbox"], "components": dc["components"], "method": ic["method"]},
                "reference": {"fill": dr["fill"], "bbox": dr["bbox"], "components": dr["components"], "method": ir["method"]}, "grid": [w, h]}
    detail = {"intersection_px": cmp["intersection_px"], "union_px": cmp["union_px"], "candidate_only_share": cmp.get("candidate_only_share"),
              "reference_only_share": cmp.get("reference_only_share"), "candidate_centroid": dc["centroid"], "reference_centroid": dr["centroid"],
              "candidate_mask": ic, "reference_mask": ir, "resized_reference": ref.rgb.shape[:2] != (h, w)}
    return rows, measured, detail, warnings, ref_r


def silhouette_iou(ctx: Ctx, candidate: str, reference: str, method: str = "auto", reference_method: str = "auto") -> dict:
    cand, ref = ctx.load(candidate), ctx.load(reference)
    rows, measured, detail, warnings, _ = _silhouette_part(ctx, cand, ref, method, reference_method)
    return _report("silhouette_iou", rows, _round(measured), _round(detail), warnings=warnings, ctx=ctx,
                   not_measured=["whether the shapes are the same OBJECT", "mask quality on a busy background (masks assume a plain background or alpha)"])


def compare_to_reference(ctx: Ctx, candidate: str, reference: str | None = None, profile: str | None = None, method: str = "auto", reference_method: str = "auto",
                         dimensions: list[str] | None = None) -> dict:
    if (reference is None) == (profile is None):
        raise ValueError("give exactly one of reference (an image) or profile (a saved target profile name)")
    if profile is not None:
        rep = measure_image(ctx, candidate, profile=profile, dimensions=dimensions)
        rep["subject"] = "compare_to_reference (profile)"
        return rep
    dims = _dims(dimensions)
    th = ctx.th
    cand, ref = ctx.load(candidate), ctx.load(reference)
    rows: list[dict] = []
    measured: dict = {}
    detail: dict = {}
    warnings: list[str] = []
    ref_r = None
    if "silhouette" in dims or True:
        s_rows, s_meas, s_det, warnings, ref_r = _silhouette_part(ctx, cand, ref, method, reference_method)
        if "silhouette" in dims:
            rows += s_rows
            measured["silhouette"] = {k: s_meas[k] for k in ("iou", "centroid_offset_px", "bbox_offset_px")}
            detail["silhouette"] = {**s_det, "dice": s_meas["dice"], "candidate": s_meas["candidate"], "reference": s_meas["reference"]}
        else:
            warnings = []
    vc, vr = _valid_or_refuse(cand), _valid_or_refuse(ref_r)
    if "palette" in dims:
        dc = P.dominant_colors(cand.rgb.astype(np.float64)[vc], int(th("palette.k")))
        dr = P.dominant_colors(ref_r.rgb.astype(np.float64)[vr], int(th("palette.k")))
        pd = P.palette_distance(dc, [d["hex"] for d in dr], [d["share"] for d in dr])
        measured["palette_distance"] = {k: pd[k] for k in ("distance", "image_to_target", "target_to_image")}
        measured["dominant"] = {"candidate": [f"{d['hex']} {d['share']:.3f}" for d in dc], "reference": [f"{d['hex']} {d['share']:.3f}" for d in dr]}
        rows.append(_tcheck(ctx, "palette.distance", pd["distance"], "palette.distance_max", note="the candidate's dominant colours are far from the reference's (CIE76 delta-E)"))
    if "value" in dims:
        lc, lr = P.luminance(cand.rgb)[vc], P.luminance(ref_r.rgb)[vr]
        emd = P.histogram_emd(lc, lr)
        measured["value"] = {"hist_distance": emd, "contrast_candidate": float(np.percentile(lc, 95) - np.percentile(lc, 5)), "contrast_reference": float(np.percentile(lr, 95) - np.percentile(lr, 5)),
                             "mean_candidate": float(lc.mean()), "mean_reference": float(lr.mean())}
        rows.append(_tcheck(ctx, "value.hist_distance", emd, "value.hist_distance_max", note="the luminance distributions differ (earth-mover distance)"))
    if "edges" in dims:
        ec = P.edge_stats(_composite_lum(cand), th("edges.gradient_threshold"))["density"]
        er = P.edge_stats(_composite_lum(ref_r), th("edges.gradient_threshold"))["density"]
        measured["edges"] = {"density_candidate": ec, "density_reference": er, "ratio": None if er <= 0 else ec / er}
        if er <= 0:
            for rid, p in (("edges.density_ratio_min", "edges.density_ratio_min"), ("edges.density_ratio_max", "edges.density_ratio_max")):
                rows.append(C.check(rid, None, ctx.th.meta[p]["op"], th(p), param=p, dimension="edges", skipped="the reference has no edge pixels"))
        else:
            rows.append(_tcheck(ctx, "edges.density_ratio_min", ec / er, "edges.density_ratio_min", note="the candidate is flatter than the reference"))
            rows.append(_tcheck(ctx, "edges.density_ratio_max", ec / er, "edges.density_ratio_max", note="the candidate is noisier than the reference"))
    return _report("compare_to_reference", rows, _round(measured), _round(detail), warnings=warnings, ctx=ctx,
                   not_measured=["whether the candidate and reference depict the same thing", "mask quality on a busy background"])


# --- palette ---------------------------------------------------------------------------------------------------------------------------------
def palette_distance(ctx: Ctx, path: str, target_palette: list[str] | None = None, profile: str | None = None, weights: list[float] | None = None) -> dict:
    if bool(target_palette) == bool(profile):
        raise ValueError("give exactly one of target_palette (hex colours) or profile (a saved target profile name)")
    im = ctx.load(path)
    valid = _valid_or_refuse(im)
    dom = P.dominant_colors(im.rgb.astype(np.float64)[valid], int(ctx.th("palette.k")))
    if profile:
        prof = ctx.profile(profile)
        if not prof.get("palette"):
            raise ValueError(f"profile '{profile}' has no palette")
        tgt, w = [t["hex"] for t in prof["palette"]], [t["share"] for t in prof["palette"]]
    else:
        tgt, w = target_palette, weights
        for h in tgt:
            P.hex_to_rgb(h)
    pd = P.palette_distance(dom, tgt, w)
    rows = [_tcheck(ctx, "palette.distance", pd["distance"], "palette.distance_max", note="dominant colours are far from the target palette (CIE76 delta-E)")]
    measured = {"distance": pd["distance"], "image_to_target": pd["image_to_target"], "target_to_image": pd["target_to_image"], "dominant": [f"{d['hex']} {d['share']:.3f}" for d in dom],
                "worst_image_colour": max(pd["pairs"], key=lambda r: r["delta_e"]), "worst_target_colour": max(pd["targets"], key=lambda r: r["delta_e"])}
    return _report("palette_distance", rows, _round(measured, 2), _round({"pairs": pd["pairs"], "targets": pd["targets"]}, 2), ctx=ctx,
                   not_measured=["whether the colours are used in the right places", "colour harmony as a person sees it"])


# --- tiling ----------------------------------------------------------------------------------------------------------------------------------
def check_tiling(ctx: Ctx, path: str) -> dict:
    im = ctx.load(path, exact=True)
    s = TL.seam(im.rgb.astype(np.float64))
    rep = TL.repetition(P.luminance(im.rgb), ctx.th("tiling.min_lag_fraction"), ctx.th("tiling.lobe_cut"))
    rows = [_tcheck(ctx, "tiling.seam_ratio", s["ratio"], "tiling.seam_ratio_max", note=f"the {s['worst_axis']} wrap-around edge steps {s['ratio']:.1f}x more than its neighbours")]
    if rep["peak"] is None:
        why = "the tile is flat (no variance)" if rep["flat"] else rep.get("note", "no repeat distance to test")
        rows.append(C.check("tiling.repetition_peak", None, "<=", ctx.th("tiling.repetition_peak_max"), param="tiling.repetition_peak_max", dimension="tiling", skipped=why))
    else:
        rows.append(_tcheck(ctx, "tiling.repetition_peak", rep["peak"], "tiling.repetition_peak_max", note=f"the tile repeats itself at an offset of {rep['lag']} px"))
    measured = {"size": list(im.size), "seam_ratio": s["ratio"], "worst_axis": s["worst_axis"], "repetition_peak": rep["peak"], "repeat_lag_px": rep["lag"]}
    detail = {"horizontal": s["horizontal"], "vertical": s["vertical"], "main_lobe_radius_px": rep.get("lobe_radius_px"), "mode": im.mode}
    warn = ["alpha is ignored: the seam is measured on the colour channels"] if im.has_alpha else []
    return _report("check_tiling", rows, _round(measured), _round(detail), warnings=warn, ctx=ctx,
                   not_measured=["whether a seam is visible at the scale it is used", "pattern repetition across MANY tiles (only inside this one tile)", "texel-density consistency on a model"])


# --- PBR -------------------------------------------------------------------------------------------------------------------------------------
def check_pbr_ranges(ctx: Ctx, albedo: str | None = None, roughness: str | None = None, metalness: str | None = None, normal: str | None = None) -> dict:
    given = {k: v for k, v in (("albedo", albedo), ("roughness", roughness), ("metalness", metalness), ("normal", normal)) if v}
    if not given:
        raise ValueError("give at least one map path: albedo, roughness, metalness or normal")
    maps = {k: ctx.load(v, exact=True) for k, v in given.items()}
    rows, meas = PB.analyse(maps, ctx.th)
    warnings = []
    for k, im in maps.items():
        if k in ("roughness", "metalness", "normal") and im.bit_depth == 8 and im.mode in ("P",):
            warnings.append(f"{k}: palette image: values were converted to RGB")
    return _report("check_pbr_ranges", rows, _round(meas), {}, warnings=warnings, ctx=ctx, not_measured=PB.NOT_MEASURED,
                   extra={"verify_against_current_docs": True})


# --- diff ------------------------------------------------------------------------------------------------------------------------------------
def diff_images(ctx: Ctx, before: str, after: str, resize: bool = False) -> dict:
    a, b = _load_exact_or_reduced(ctx, before), _load_exact_or_reduced(ctx, after)
    warnings: list[str] = []
    if a.rgb.shape != b.rgb.shape:
        if not resize:
            raise ValueError(f"the images have different sizes ({a.size[0]}x{a.size[1]} vs {b.size[0]}x{b.size[1]}): pass resize=true to resample 'after' onto 'before', or crop them to match")
        b = IO.resized_to(b, a.rgb.shape[1], a.rgb.shape[0])
        warnings.append("'after' was resampled onto the size of 'before': differences include resampling error")
    nd = D.numeric(a, b, ctx.th("diff.pixel_threshold"))
    if tuple(nd["size"]) != tuple(a.size):
        warnings.append(f"the images are {a.size[0]}x{a.size[1]}, above limits.max_side_exact: they were area-averaged to {nd['size'][0]}x{nd['size'][1]} before differencing")
    rows = [_tcheck(ctx, "diff.changed_fraction", nd["changed_fraction"], "diff.changed_fraction_max", note=f"{nd['changed_px']} pixels changed by more than {ctx.th('diff.pixel_threshold'):.4f}")]
    measured = {k: nd[k] for k in ("size", "identical", "mae", "rmse", "psnr_db", "changed_fraction", "changed_bbox")}
    return _report("diff_images", rows, _round(measured), _round({"max_abs": nd["max_abs"], "mean_signed_by_channel": nd["mean_signed_by_channel"], "changed_px": nd["changed_px"]}), warnings=warnings,
                   ctx=ctx, not_measured=["whether the change is the intended change", "perceptual difference (these are pixel differences, not SSIM or a vision model)"],
                   verdict_note="the images are pixel-identical" if nd["identical"] else "")


def _load_exact_or_reduced(ctx: Ctx, path: str) -> IO.Loaded:
    th = ctx.th
    im = IO.load(path, ctx.roots, max_bytes=int(th("limits.max_file_bytes")), max_pixels=int(th("limits.max_pixels")), max_side=int(th("limits.max_side_exact")))
    ctx.images.append({"name": im.name, "sha256": im.sha256[:12], "size": list(im.size)})
    return im


RUNNERS = {"measure_image": measure_image, "compare_to_reference": compare_to_reference, "silhouette_iou": silhouette_iou, "palette_distance": palette_distance,
           "check_tiling": check_tiling, "check_pbr_ranges": check_pbr_ranges, "diff_images": diff_images}


# --- rendering ------------------------------------------------------------------------------------------------------------------------------
def render(ctx: Ctx, rep: dict, detail: bool = False, place_state: dict | None = None) -> dict:
    """The compact tool answer. The summary line comes first; failed checks are ranked and capped; every number carries the limit it was compared with."""
    cap = int(ctx.th("limits.findings_cap"))
    rows = rep["rows"]
    found, more = C.findings(rows, cap)
    ranked = C.rank(rows)
    summ = C.verdict(rows, rep["subject"], ctx.label, limit=cap, extra=rep.get("note", ""))
    out: dict[str, Any] = {"summary": summ, **(place_state or {})}
    out["images"] = rep["images"]
    out["findings"] = found
    if more:
        out["more_findings"] = more
    out["passed"] = [f"{r['id']} {r['value']} {r['op']} {r['limit']}" + (f" ({r['source']})" if r.get("source") else "") for r in ranked if r["passed"] is True]
    skipped = [f"{r['id']}: {r['skipped']}" for r in ranked if r["passed"] is None]
    if skipped:
        out["skipped"] = skipped
    out["measured"] = rep["measured"]
    if rep["warnings"]:
        out["warnings"] = rep["warnings"][:cap]
    out["not_measured"] = rep["not_measured"] if detail else rep["not_measured"][:5]
    if len(rep["not_measured"]) > 5 and not detail:
        out["more_not_measured"] = len(rep["not_measured"]) - 5
    learned = {r["param"]: r["source"] for r in rows if r.get("source") in ("project", "global", "override") and r.get("param")}
    if learned:
        out["learned_thresholds"] = learned
    out.update(rep.get("extra") or {})
    if detail:
        out["detail"] = rep["detail"]
        out["checks"] = [C.public(r) for r in ranked]
    return out


def compact_rows(rows: list[dict]) -> list[dict]:
    """What is stored with a recorded run: each check's id, parameter, operator, limit, measured value, result and dimension (no free text, no paths)."""
    return [{k: r[k] for k in ("id", "param", "op", "limit", "value", "passed", "dimension") if k in r} for r in rows]
