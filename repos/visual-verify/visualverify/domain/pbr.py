"""Texture sanity: channel ranges for albedo, roughness and metalness, unit length of a normal map, and consistency between the maps.

These are conventions of metallic-roughness PBR (a base colour that is neither pitch black nor paper white, roughness that varies, metalness that is mostly 0
or 1, tangent-space normals of length 1 pointing out of the surface). Every limit is a placeholder in style/thresholds.yaml; whether a given engine wants
exactly these ranges is NOT verified here (verify_against_current_docs). Nothing is said about whether the surface looks like the material it is meant to be.
"""

from __future__ import annotations

import numpy as np

from . import checks as C
from . import imageio as IO
from . import pixels as P

KINDS = ("albedo", "roughness", "metalness", "normal")
NOT_MEASURED = ["normal-map green-channel convention (OpenGL vs DirectX: not determinable from pixels)", "whether the values are physically right for the material",
                "baked lighting or shadows inside the albedo", "how the maps look on a model"]


def _p2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def analyse(maps: dict[str, IO.Loaded], th) -> tuple[list[dict], dict]:
    """``(checks, measurements)`` for the supplied maps (any subset of KINDS). ``th(name)`` returns a threshold value."""
    rows: list[dict] = []
    meas: dict = {}
    for kind, im in maps.items():
        info = {"size": list(im.size), "mode": im.mode, "bit_depth": im.bit_depth, "power_of_two": _p2(im.size[0]) and _p2(im.size[1])}
        meas[kind] = info
        v = im.valid
        if kind == "albedo":
            lum = P.luminance(im.rgb)[v]
            lo, hi = th("pbr.albedo_lum_min"), th("pbr.albedo_lum_max")
            out = float(((lum < lo) | (lum > hi)).mean())
            info.update(luminance_p1=float(np.percentile(lum, 1)), luminance_p99=float(np.percentile(lum, 99)), luminance_mean=float(lum.mean()),
                        too_dark_fraction=float((lum < lo).mean()), too_bright_fraction=float((lum > hi).mean()), luminance_bounds=[lo, hi])
            rows.append(C.check("albedo.out_of_range_fraction", out, "<=", th("pbr.albedo_out_fraction_max"), param="pbr.albedo_out_fraction_max", dimension="texture",
                                note=f"{info['too_dark_fraction']:.1%} of pixels below luminance {lo:.3f}, {info['too_bright_fraction']:.1%} above {hi:.3f}"))
        elif kind in ("roughness", "metalness"):
            x = im.rgb[v]
            val = x.mean(-1)
            spread = float((x.max(-1) - x.min(-1)).mean())
            info.update(mean=float(val.mean()), std=float(val.std()), min=float(val.min()), max=float(val.max()), gray_spread=spread)
            rows.append(C.check(f"{kind}.gray_spread", spread, "<=", th("pbr.gray_spread_max"), param="pbr.gray_spread_max", dimension="texture",
                                note="the map has colour: it should be grayscale (channels are averaged for the other numbers)"))
            if kind == "roughness":
                rows.append(C.check("roughness.std", float(val.std()), ">=", th("pbr.roughness_std_min"), param="pbr.roughness_std_min", dimension="texture", note="constant map: no variation"))
                rows.append(C.check("roughness.mean_min", float(val.mean()), ">=", th("pbr.roughness_mean_min"), param="pbr.roughness_mean_min", dimension="texture", note="mean roughness near 0 (mirror)"))
                rows.append(C.check("roughness.mean_max", float(val.mean()), "<=", th("pbr.roughness_mean_max"), param="pbr.roughness_mean_max", dimension="texture", note="mean roughness near 1 (fully rough)"))
            else:
                mid = float(((val > th("pbr.metal_mid_lo")) & (val < th("pbr.metal_mid_hi"))).mean())
                info["mid_fraction"] = mid
                rows.append(C.check("metalness.mid_fraction", mid, "<=", th("pbr.metal_mid_fraction_max"), param="pbr.metal_mid_fraction_max", dimension="texture",
                                    note="metalness is mostly 0 or 1; many in-between pixels"))
        elif kind == "normal":
            n = im.rgb[v].astype(np.float64) * 2.0 - 1.0
            ln = np.linalg.norm(n, axis=-1)
            tol = th("pbr.normal_len_tol")
            bad = float((np.abs(ln - 1.0) > tol).mean())
            mean_err = float(abs(ln.mean() - 1.0))
            zneg = float((n[..., 2] < 0).mean())
            info.update(mean_length=float(ln.mean()), min_length=float(ln.min()), max_length=float(ln.max()), bad_length_fraction=bad, z_negative_fraction=zneg, mean_z=float(n[..., 2].mean()))
            rows.append(C.check("normal.mean_length_error", mean_err, "<=", th("pbr.normal_mean_len_err_max"), param="pbr.normal_mean_len_err_max", dimension="texture",
                                note=f"mean vector length {ln.mean():.3f}, expected 1"))
            rows.append(C.check("normal.bad_length_fraction", bad, "<=", th("pbr.normal_bad_fraction_max"), param="pbr.normal_bad_fraction_max", dimension="texture",
                                note=f"pixels whose length is not within {tol} of 1"))
            rows.append(C.check("normal.z_negative_fraction", zneg, "<=", th("pbr.normal_z_neg_fraction_max"), param="pbr.normal_z_neg_fraction_max", dimension="texture",
                                note="decoded z below 0: not a tangent-space normal map, or a channel is swapped"))
    if len(maps) > 1:
        sizes = {tuple(m.size) for m in maps.values()}
        rows.append(C.check("maps.distinct_sizes", len(sizes), "<=", 1, dimension="texture", note="the maps have different sizes: " + ", ".join(f"{k} {m.size[0]}x{m.size[1]}" for k, m in maps.items())))
    return rows, meas
