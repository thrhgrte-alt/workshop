"""Measure rendered material maps against the style's numeric ranges and the rubric.

Everything here is a *measurement of exported maps* (PNG/JPG/TGA... anything Pillow opens). It cannot
see inside Designer. Subjective criteria stay manual: a model can fill them from the preview, but
that judgment is recorded as subjective and is not ground truth.
"""

from __future__ import annotations

from pathlib import Path

from sdai.core import imaging
from sdai.core.rubric import score_rubric
from sdai.core.style import check_ranges

EXPECTED_MAPS = ("baseColor", "normal", "roughness", "height")


def _normal_map_stats(arr) -> dict:
    """Decode a tangent-space normal map and report how plausible it is."""
    import numpy as np

    n = arr * 2.0 - 1.0
    length = np.linalg.norm(n, axis=-1)
    return {"mean_length": float(length.mean()), "length_std": float(length.std()),
            "mean_z": float(n[..., 2].mean()), "flat_fraction": float((n[..., 2] > 0.99).mean())}


def measure_material(maps: dict[str, str], style: dict) -> dict:
    """``maps``: usage -> image path. Returns raw measurements per map and the flat metric dict for style ranges."""
    palette = style.get("traits", {}).get("palette_hex", [])
    per_map: dict[str, dict] = {}
    for usage, path in maps.items():
        if not Path(path).exists():
            raise FileNotFoundError(f"{usage} map not found: {path}")
        arr = imaging.load_rgb(path, 512)
        stats = {"seam_score": imaging.seam_score(arr), "size": [arr.shape[1], arr.shape[0]]}
        if usage == "baseColor":
            lum = imaging.luminance_stats(arr)
            stats.update(contrast=lum["contrast"], saturation=imaging.saturation_mean(arr),
                         detail_frequency=imaging.detail_frequency(arr), palette=imaging.palette(arr, 5))
            if palette:
                stats["palette_adherence"] = imaging.palette_adherence(arr, palette)
        elif usage == "normal":
            stats.update(_normal_map_stats(arr))
            stats["detail_frequency"] = imaging.detail_frequency(arr)
        elif usage in ("roughness", "metallic", "height", "ambientOcclusion", "opacity"):
            lum = imaging.luminance_stats(arr)
            stats.update(mean=lum["mean"], std=lum["std"], spread=lum["contrast"])
        per_map[usage] = stats
    flat: dict[str, float] = {}
    for usage, s in per_map.items():
        flat[f"{usage}_seam_score"] = s["seam_score"]
    base = per_map.get("baseColor", {})
    for key in ("contrast", "saturation", "detail_frequency", "palette_adherence"):
        if key in base:
            flat[f"basecolor_{key}"] = base[key]
    if "roughness" in per_map:
        flat["roughness_mean"] = per_map["roughness"]["mean"]
        flat["roughness_spread"] = per_map["roughness"]["spread"]
    if "height" in per_map:
        flat["height_spread"] = per_map["height"]["spread"]
    if "normal" in per_map:
        flat["normal_mean_length"] = per_map["normal"]["mean_length"]
        flat["normal_mean_z"] = per_map["normal"]["mean_z"]
    return {"per_map": per_map, "metrics": flat}


def compare_to_rubric(maps: dict[str, str], style: dict, rubric: dict, manual: dict | None = None) -> dict:
    missing = [m for m in style.get("required_maps", EXPECTED_MAPS) if m not in maps]
    measured = measure_material(maps, style)
    findings = check_ranges(measured["metrics"], style.get("ranges", {}))
    for m in missing:
        findings.append({"metric": f"map:{m}", "severity": "error", "message": f"required map '{m}' was not provided"})
    bad = {f["metric"] for f in findings if f.get("severity") in ("error", "warning")}

    def ok(*metrics: str) -> bool:
        return not any(m in bad for m in metrics)

    auto = {
        "maps_present": not missing,
        "tileable": ok(*[f"{u}_seam_score" for u in maps]),
        "value_contrast": ok("basecolor_contrast"),
        "palette_match": ok("basecolor_palette_adherence"),
        "detail_level": ok("basecolor_detail_frequency", "height_spread"),
        "roughness_range": ok("roughness_mean", "roughness_spread"),
        "normal_valid": ok("normal_mean_length", "normal_mean_z"),
        "matches_style": not bad,  # measured half of the hybrid criterion; a manual verdict must agree
    }
    scored = score_rubric(rubric, auto=auto, manual=manual)
    return {"measurements": measured["per_map"], "metrics": measured["metrics"], "findings": findings,
            "rubric": scored}
