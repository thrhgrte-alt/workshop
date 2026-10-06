"""Mesh, transform and collision checks. Measure, compare with the resolved limit, report. No severities or numbers live here."""

from __future__ import annotations

import math

from .engine import Ctx, implements
from .mathx import length, quat_angle_deg, quat_to_euler_deg


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


def _vec(v, n=4):
    return "(" + ", ".join(f"{round(c, n):g}" for c in v) + ")"


@implements("MESH_NO_GEOMETRY", "MESH_NON_FINITE")
def geometry_present(ctx: Ctx):
    if not ctx.asset.get("geometry_measured", True) and not ctx.meshes:
        return  # UPL_NOT_MEASURED explains why nothing was measured
    if not ctx.visual or (ctx.visual_triangles is not None and ctx.visual_triangles == 0):
        ctx.add("MESH_NO_GEOMETRY", None, 0, ">= 1 visible mesh with triangles", message="no visible mesh with triangles in the export")
    for o in ctx.visual:
        n = ctx.metrics[o["id"]].get("non_finite")
        if n:
            ctx.add("MESH_NON_FINITE", o["name"], n, 0, message=f"{n} vertices have NaN or infinite coordinates")


@implements("MESH_TRI_BUDGET", "MESH_TRI_BUDGET_TOTAL", "MESH_OBJECT_COUNT")
def budgets(ctx: Ctx):
    lim = ctx.limit("MESH_TRI_BUDGET", "max_triangles")
    for o in ctx.visual:
        m = ctx.metrics[o["id"]]
        if m["triangles"] is not None and m["triangles"] > lim:
            ctx.add("MESH_TRI_BUDGET", o["name"], m["triangles"], lim, source=m["source"], limit_key="max_triangles",
                    message=f"{m['triangles']} triangles exceeds the {ctx.profile_name} budget of {lim}")
    total = ctx.visual_triangles
    tlim = ctx.limit("MESH_TRI_BUDGET_TOTAL", "max_triangles_total")
    if total is not None and total > tlim and len(ctx.visual) > 1:
        ctx.add("MESH_TRI_BUDGET_TOTAL", None, total, tlim, limit_key="max_triangles_total", message=f"all visible meshes together have {total} triangles")
    elif total is not None and total > tlim and len(ctx.visual) == 1 and total <= lim:
        ctx.add("MESH_TRI_BUDGET_TOTAL", None, total, tlim, limit_key="max_triangles_total", message=f"the asset has {total} triangles in total")
    cnt = ctx.limit("MESH_OBJECT_COUNT", "max_mesh_objects")
    if len(ctx.visual) > cnt:
        ctx.add("MESH_OBJECT_COUNT", None, len(ctx.visual), cnt, limit_key="max_mesh_objects", message=f"{len(ctx.visual)} separate visible mesh objects")


@implements("MESH_LOOSE_GEOMETRY", "MESH_NON_MANIFOLD", "MESH_FLIPPED_NORMALS", "MESH_NGONS")
def topology(ctx: Ctx):
    max_orphans = ctx.limit("MESH_LOOSE_GEOMETRY", "max_orphan_vertices")
    max_degen = ctx.limit("MESH_LOOSE_GEOMETRY", "max_degenerate_triangles")
    max_nm = ctx.limit("MESH_NON_MANIFOLD", "max_non_manifold_edges")
    max_flip = ctx.limit("MESH_FLIPPED_NORMALS", "max_flipped_ratio")
    max_ngon = ctx.limit("MESH_NGONS", "max_ngons")
    ngon_unknown = []
    for o in ctx.visual:
        m = ctx.metrics[o["id"]]
        src = m["source"]
        orph, deg = m.get("orphan_vertices"), m.get("degenerate_tris")
        if (orph is not None and orph > max_orphans) or (deg is not None and deg > max_degen):
            ctx.add("MESH_LOOSE_GEOMETRY", o["name"], f"{orph or 0} loose vertices, {deg or 0} degenerate triangles", f"{max_orphans} / {max_degen}", source=src,
                    message=f"{orph or 0} vertices are used by no triangle and {deg or 0} triangles have no area")
        nm = m.get("non_manifold_edges")
        if nm is not None and nm > max_nm:
            det = f"{m['boundary_edges']} open edges, {m['multi_edges']} edges shared by 3+ faces" if m.get("boundary_edges") is not None else None
            ctx.add("MESH_NON_MANIFOLD", o["name"], nm, max_nm, source=src, detail=det, message=f"{nm} open or non-manifold edges" + (f" ({det})" if det else ""))
        ratio = m.get("flipped_ratio")
        ratio_bad = ratio is not None and ratio > max_flip
        if ratio_bad or m.get("inside_out"):
            parts = []
            if ratio_bad:
                parts.append(f"{round(ratio * 100, 1)}% of triangles have a normal opposing their winding" + (f" ({m['flipped_faces']} faces)" if m.get("flipped_faces") is not None else ""))
            if m.get("inside_out"):
                parts.append(f"the closed mesh is inside out (signed volume {_r(m['signed_volume_local'], 4)})")
            measured = _r(ratio, 3) if ratio_bad else "inside out (negative volume)"
            ctx.add("MESH_FLIPPED_NORMALS", o["name"], measured, max_flip if ratio_bad else "volume > 0", source=src, message="; ".join(parts),
                    fix_vars={"measured": f"{m.get('flipped_faces', '?')} of {m['triangles']} faces flipped" if ratio_bad else "the closed mesh is inside out"})
        if m.get("ngons") is not None:
            if m["ngons"] > max_ngon:
                ctx.add("MESH_NGONS", o["name"], m["ngons"], max_ngon, source=src, message=f"{m['ngons']} faces have more than four sides")
        else:
            ngon_unknown.append(o["name"])
    if ngon_unknown and ctx.enabled("MESH_NGONS"):
        nm = {x["what"] for x in ctx.asset["not_measurable"]}
        if "n-gons" not in nm:
            ctx.asset["not_measurable"].append({"what": "n-gons", "why": f"not recorded in this {ctx.asset['format']} input"})


def _sizes(ctx: Ctx):
    lo, hi, dims = ctx.visual_dims_world()
    if dims is None:
        return None
    return tuple(d * ctx.studs for d in dims)


@implements("MESH_SCALE_RANGE", "MESH_BBOX_PROFILE", "XFM_UNITS_SUSPECT")
def size_checks(ctx: Ctx):
    size = _sizes(ctx)
    if size is None or max(size) == 0:
        return
    big = max(size)
    mn, mx = ctx.limit("MESH_SCALE_RANGE", "min_dimension_studs"), ctx.limit("MESH_SCALE_RANGE", "max_dimension_studs")
    pmin, pmax = ctx.limit("MESH_BBOX_PROFILE", "min_largest_dimension_studs"), ctx.limit("MESH_BBOX_PROFILE", "max_largest_dimension_studs")
    src = "reported" if ctx.visual and ctx.metrics[ctx.visual[0]["id"]]["source"] == "reported" else "computed"
    scale_bad = big < mn or big > mx
    prof_bad = big < pmin or big > pmax
    if scale_bad:
        ctx.add("MESH_SCALE_RANGE", None, _r(big, 4), f"{mn} to {mx}", source=src, limit_key="max_dimension_studs",
                message=f"largest dimension is {_r(big, 4)} studs (size {_vec(size)})")
    elif prof_bad:
        ctx.add("MESH_BBOX_PROFILE", ", ".join(o["name"] for o in ctx.visual[:3]) or None, _r(big, 4), f"{pmin} to {pmax}", source=src,
                message=f"largest dimension {_r(big, 4)} studs is outside the {ctx.profile_name} range (size {_vec(size)})")
    if scale_bad or prof_bad:
        for f in ctx.limit("XFM_UNITS_SUSPECT", "factors"):
            if pmin <= big * f <= pmax and mn <= big * f <= mx:
                ctx.add("XFM_UNITS_SUSPECT", None, _r(big, 4), f"x{f}", source=src, message=f"multiplying the size by {f} would fit the {ctx.profile_name} range")
                break


@implements("XFM_UNAPPLIED_SCALE", "XFM_NEGATIVE_SCALE", "XFM_UNAPPLIED_ROTATION", "XFM_ROOT_OFFSET", "XFM_AXIS_UP", "XFM_ORIGIN_PLACEMENT")
def transforms(ctx: Ctx):
    tol_s = ctx.limit("XFM_UNAPPLIED_SCALE", "tolerance")
    tol_r = ctx.limit("XFM_UNAPPLIED_ROTATION", "tolerance_degrees")
    max_off = ctx.limit("XFM_ROOT_OFFSET", "max_offset_units")
    up = ctx.limit("XFM_AXIS_UP", "expected_up_axis")
    declared = ctx.asset.get("up_axis")
    if declared and str(declared).upper() != str(up).upper():
        ctx.add("XFM_AXIS_UP", None, f"declared up axis {declared}", up, limit_key="expected_up_axis", message=f"the summary declares {declared} up; {up} up is expected")
    axis_flagged = set()
    for o in ctx.objects:
        if o["kind"] == "bone":
            continue
        s = o["scale"]
        name = o["name"]
        if any(c < 0 for c in s):
            ctx.add("XFM_NEGATIVE_SCALE", name, _vec(s), "all components > 0", message=f"scale {_vec(s)} mirrors the object")
        elif any(abs(c - 1.0) > tol_s for c in s):
            ctx.add("XFM_UNAPPLIED_SCALE", name, _vec(s), f"1 +/- {tol_s}", message=f"object scale is {_vec(s)}", fix_op={"op": "apply_scale", "object": name, "kind": o["kind"], "skinned": o.get("skin") is not None})
        if o["kind"] in ("mesh", "empty"):
            ang = quat_angle_deg(o["rotation"])
            eul = quat_to_euler_deg(o["rotation"])
            if o["parent"] is None and abs(abs(eul[0]) - 90) < 1.0 and abs(eul[1]) < 1.0 and abs(eul[2]) < 1.0:
                axis_flagged.add(name)
                ctx.add("XFM_AXIS_UP", name, f"rotation {_vec(eul)} degrees", up, limit_key="expected_up_axis",
                        message="a 90 degree rotation about X on the root looks like a leftover Z-up to Y-up conversion")
            elif ang > tol_r:
                ctx.add("XFM_UNAPPLIED_ROTATION", name, _vec(quat_to_euler_deg(o["rotation"])), f"0 +/- {tol_r} degrees", message=f"object is rotated {_r(ang, 2)} degrees",
                        fix_op={"op": "apply_rotation", "object": name, "kind": o["kind"], "skinned": o.get("skin") is not None})
        if o["parent"] is None and o["kind"] in ("mesh", "empty", "armature"):
            off = length(o["translation"])
            if off > max_off:
                ctx.add("XFM_ROOT_OFFSET", name, _r(off, 3), max_off, message=f"root object is {_r(off, 3)} units from the world origin at {_vec(o['translation'])}")
    mode = ctx.limit("XFM_ORIGIN_PLACEMENT", "mode")
    tol = ctx.limit("XFM_ORIGIN_PLACEMENT", "tolerance")
    if mode != "any":
        for o in ctx.visual:
            m = ctx.metrics[o["id"]]
            big = max(m["dims_local"]) if m.get("dims_local") else 0
            if not big:
                continue
            if m.get("bbox_local"):
                lo, hi = m["bbox_local"]
                anchor = ((lo[0] + hi[0]) / 2, lo[1] if mode == "bottom_center" else (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2)
                off = length(anchor) / big
                what = _vec(anchor)
            elif m.get("origin_offset_reported") is not None:
                off = length(m["origin_offset_reported"]) / big
                what = _vec(m["origin_offset_reported"])
            else:
                continue
            if off > tol:
                ctx.add("XFM_ORIGIN_PLACEMENT", o["name"], _r(off, 3), mode, limit_key="mode", source=m["source"],
                        message=f"the {mode} point is {what} away from the origin ({round(off * 100, 1)}% of the size, tolerance {round(tol * 100, 1)}%)")


@implements("COL_PROXY_MISSING", "COL_PROXY_BUDGET", "COL_PROXY_BOUNDS")
def collision(ctx: Ctx):
    thr = ctx.limit("COL_PROXY_MISSING", "required_above_triangles")
    tris = ctx.visual_triangles
    pre = (ctx.asset.get("extras") or {}).get("preflight")
    declared_absent = (ctx.collision_intent == "absent" or (isinstance(pre, dict) and pre.get("collision") == "none")
                       or any((o.get("extras") or {}).get("preflight_collision") == "none" for o in ctx.visual))
    if ctx.proxies:
        status = "present"
    elif declared_absent:
        status = "declared_absent"
    elif ctx.collision_intent == "present" or (tris is not None and tris > thr):
        status = "missing"
    else:
        status = "not_required"
    ctx.state["collision_status"] = status
    if status == "missing":
        reason = "collision was expected (collision_intent=present)" if ctx.collision_intent == "present" else f"{tris} triangles is above the {thr} threshold"
        ctx.add("COL_PROXY_MISSING", ctx.visual[0]["name"] if ctx.visual else None, tris, thr, limit_key="required_above_triangles", message=f"no collision proxy and {reason}")
    pmax = ctx.limit("COL_PROXY_BUDGET", "max_proxy_triangles")
    tol = ctx.limit("COL_PROXY_BOUNDS", "tolerance")
    _, _, vd = ctx.visual_dims_world()
    for o in ctx.proxies:
        m = ctx.metrics[o["id"]]
        if m["triangles"] is not None and m["triangles"] > pmax:
            ctx.add("COL_PROXY_BUDGET", o["name"], m["triangles"], pmax, limit_key="max_proxy_triangles", source=m["source"], message=f"proxy has {m['triangles']} triangles")
        if vd and m.get("dims_world"):
            worst = max((abs(m["dims_world"][i] - vd[i]) / vd[i]) for i in range(3) if vd[i] > 1e-9) if any(v > 1e-9 for v in vd) else 0.0
            if worst > tol:
                ctx.add("COL_PROXY_BOUNDS", o["name"], _r(worst, 3), tol, message=f"proxy size {_vec(m['dims_world'])} differs from the visible mesh {_vec(vd)} by up to {round(worst * 100)}%")
