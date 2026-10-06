"""Exact measurements on a loaded asset. Pure functions: no rules, no severities, no judgement.

Every metric records whether it was ``computed`` here from geometry or ``reported`` by a hub summary; checks say which.
"""

from __future__ import annotations

import math

from .mathx import cross, dot, length, mat_apply, sub

GRID = 128
WELD_REL = 1e-5


def _weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float:
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0]


def _raster(uvs: list[tuple[tuple[float, float], ...]], grid: int = GRID) -> tuple[int, int]:
    """(covered cells, cells covered more than once) of UV triangles in the 0-1 square (pixel-centre sampling, strict inside)."""
    cover = bytearray(grid * grid)
    for (ax, ay), (bx, by), (cx, cy) in uvs:
        det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(det) < 1e-14:
            continue
        x0 = max(0, int(math.floor(min(ax, bx, cx) * grid - 0.5)))
        x1 = min(grid - 1, int(math.ceil(max(ax, bx, cx) * grid - 0.5)))
        y0 = max(0, int(math.floor(min(ay, by, cy) * grid - 0.5)))
        y1 = min(grid - 1, int(math.ceil(max(ay, by, cy) * grid - 0.5)))
        for j in range(y0, y1 + 1):
            py = (j + 0.5) / grid
            for i in range(x0, x1 + 1):
                px = (i + 0.5) / grid
                l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / det
                l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / det
                l3 = 1 - l1 - l2
                if l1 > 1e-9 and l2 > 1e-9 and l3 > 1e-9:
                    k = j * grid + i
                    if cover[k] < 2:
                        cover[k] += 1
    covered = sum(1 for c in cover if c)
    multi = sum(1 for c in cover if c > 1)
    return covered, multi


def measure_mesh(obj: dict) -> dict:
    mesh = obj["mesh"]
    rep = obj.get("reported")
    if mesh.get("reported_only") or rep is not None and not mesh["positions"]:
        return _from_report(obj)
    pos, tris = mesh["positions"], mesh["triangles"]
    nt = len(tris) // 3
    world = obj["world"]
    wpos = [mat_apply(world, p) for p in pos]
    m: dict = {"source": "computed", "triangles": nt, "vertices": len(pos), "world_vertices": wpos}
    used_ids = sorted(set(tris)) or list(range(len(pos)))
    if pos:  # bounds use only vertices some triangle references, so a loose vertex cannot stretch the box
        lo = tuple(min(wpos[j][i] for j in used_ids) for i in range(3))
        hi = tuple(max(wpos[j][i] for j in used_ids) for i in range(3))
        llo = tuple(min(pos[j][i] for j in used_ids) for i in range(3))
        lhi = tuple(max(pos[j][i] for j in used_ids) for i in range(3))
        m.update({"bbox_world": (lo, hi), "dims_world": tuple(hi[i] - lo[i] for i in range(3)), "bbox_local": (llo, lhi), "dims_local": tuple(lhi[i] - llo[i] for i in range(3))})
    else:
        m.update({"bbox_world": None, "dims_world": (0.0, 0.0, 0.0), "bbox_local": None, "dims_local": (0.0, 0.0, 0.0)})
    m["non_finite"] = sum(1 for p in pos if not all(math.isfinite(c) for c in p))
    used = set(tris)
    m["orphan_vertices"] = len(pos) - len(used)
    diag = length(m["dims_local"]) or 1.0
    q = diag * WELD_REL
    weld: dict = {}
    wid = []
    for p in pos:
        key = (round(p[0] / q) if q else 0, round(p[1] / q) if q else 0, round(p[2] / q) if q else 0)
        wid.append(weld.setdefault(key, len(weld)))
    parent = list(range(len(weld)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    edges: dict = {}
    degenerate = 0
    flipped = 0
    measured_normals = 0
    vol = 0.0
    wtris_area: list[float] = []
    for t in range(nt):
        i0, i1, i2 = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
        w0, w1, w2 = wid[i0], wid[i1], wid[i2]
        p0, p1, p2 = pos[i0], pos[i1], pos[i2]
        n = cross(sub(p1, p0), sub(p2, p0))
        area_local = 0.5 * length(n)
        wa = 0.5 * length(cross(sub(wpos[i1], wpos[i0]), sub(wpos[i2], wpos[i0])))
        wtris_area.append(wa)
        if w0 == w1 or w1 == w2 or w0 == w2 or area_local < 1e-12 * max(diag * diag, 1e-30):
            degenerate += 1
            continue
        for a, b in ((w0, w1), (w1, w2), (w2, w0)):
            e = (a, b) if a < b else (b, a)
            edges[e] = edges.get(e, 0) + 1
        for a, b in ((w0, w1), (w1, w2)):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        vol += dot(p0, cross(p1, p2)) / 6.0
        nor = mesh["normals"]
        if nor is not None and nor[i0] is not None and nor[i1] is not None and nor[i2] is not None:
            avg = tuple(nor[i0][k] + nor[i1][k] + nor[i2][k] for k in range(3))
            measured_normals += 1
            if dot(n, avg) < 0:
                flipped += 1
    m["degenerate_tris"] = degenerate
    good = nt - degenerate
    m["boundary_edges"] = sum(1 for c in edges.values() if c == 1)
    m["multi_edges"] = sum(1 for c in edges.values() if c > 2)
    m["non_manifold_edges"] = m["boundary_edges"] + m["multi_edges"]
    m["closed"] = bool(good) and m["non_manifold_edges"] == 0
    det = (world[0] * (world[5] * world[10] - world[6] * world[9]) - world[4] * (world[1] * world[10] - world[2] * world[9]) + world[8] * (world[1] * world[6] - world[2] * world[5]))
    m["world_det_sign"] = -1 if det < 0 else 1
    m["signed_volume_local"] = vol
    m["inside_out"] = bool(m["closed"] and vol < 0)
    m["flipped_faces"] = flipped if measured_normals else None
    m["flipped_ratio"] = (flipped / measured_normals) if measured_normals else None
    comps = {find(wid[i]) for i in used} if used else set()
    m["islands"] = len(comps)
    m["ngons"] = mesh.get("ngons")
    m["quads"] = mesh.get("quads")
    m["uv_layers"] = mesh.get("uv_sets", 0)
    # --- UVs ---
    uvs = mesh["uvs"]
    if uvs is None or not tris:
        m.update({"has_uv": False, "uv_out_of_range_fraction": None, "uv_overlap_ratio": None, "uv_coverage": None, "uv_zero_area_tris": None, "texel_spread": None, "texel_median": None})
    else:
        tol = 1e-4
        ref = [i for i in used if uvs[i] is not None]
        bad = sum(1 for i in ref if not (-tol <= uvs[i][0] <= 1 + tol and -tol <= uvs[i][1] <= 1 + tol))
        m["has_uv"] = bool(ref)
        m["uv_out_of_range_fraction"] = (bad / len(ref)) if ref else None
        tri_uv, ratios, zero_uv = [], [], 0
        for t in range(nt):
            a, b, c = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
            if uvs[a] is None or uvs[b] is None or uvs[c] is None:
                continue
            ua, ub, uc = uvs[a], uvs[b], uvs[c]
            uva = abs((ub[0] - ua[0]) * (uc[1] - ua[1]) - (uc[0] - ua[0]) * (ub[1] - ua[1])) / 2.0
            tri_uv.append((ua, ub, uc))
            if wtris_area[t] > 1e-9 and uva < 1e-10:
                zero_uv += 1
            elif wtris_area[t] > 1e-9:
                ratios.append((math.sqrt(uva / wtris_area[t]), wtris_area[t]))
        covered, multi = _raster(tri_uv)
        m["uv_coverage"] = covered / (GRID * GRID)
        m["uv_overlap_ratio"] = (multi / covered) if covered else 0.0
        m["uv_zero_area_tris"] = zero_uv
        if len(ratios) >= 2:
            p5, p95 = _weighted_percentile(ratios, 0.05), _weighted_percentile(ratios, 0.95)
            m["texel_spread"] = (p95 / p5) if p5 > 0 else None
            m["texel_median"] = _weighted_percentile(ratios, 0.5)
            m["texel_p5"], m["texel_p95"] = p5, p95
        else:
            m["texel_spread"] = None
            m["texel_median"] = ratios[0][0] if ratios else None
    # --- skin weights ---
    j, w = mesh.get("joints"), mesh.get("weights")
    if j is not None and w is not None:
        max_inf, bad_sum, unweighted = 0, 0, 0
        per_joint: dict[int, list[float]] = {}
        for i in used:
            ws = w[i]
            nz = [(jj, ww) for jj, ww in zip(j[i], ws) if ww > 1e-4]
            max_inf = max(max_inf, len(nz))
            s = sum(ws)
            if s < 1e-6:
                unweighted += 1
            elif abs(s - 1.0) > 0.01:
                bad_sum += 1
            for jj, ww in nz:
                per_joint.setdefault(jj, []).append(ww / s if s > 1e-6 else ww)
        m["skin"] = {"max_influences": max_inf, "weight_sum_bad": bad_sum, "unweighted_vertices": unweighted,
                     "joint_vertices": {jj: len(v) for jj, v in per_joint.items()},
                     "joint_rigid_vertices": {jj: sum(1 for x in v if x >= 0.99) for jj, v in per_joint.items()}, "vertices": len(used)}
    else:
        m["skin"] = None
    return m


def _from_report(obj: dict) -> dict:
    r = obj.get("reported", {})
    m: dict = {"source": "reported", "triangles": r.get("triangles"), "vertices": r.get("vertices"), "world_vertices": [], "bbox_world": None, "bbox_local": None}
    dims = tuple(r["dimensions"]) if r.get("dimensions") else None
    m["dims_world"] = dims
    m["dims_local"] = dims
    m["non_finite"] = 0
    m["orphan_vertices"] = r.get("loose_vertices")
    m["degenerate_tris"] = r.get("degenerate_faces")
    m["boundary_edges"] = None
    m["multi_edges"] = None
    m["non_manifold_edges"] = r.get("non_manifold_edges")
    m["closed"] = None
    m["world_det_sign"] = 1
    m["signed_volume_local"] = None
    m["inside_out"] = None
    f = r.get("flipped_normal_faces")
    m["flipped_faces"] = f
    m["flipped_ratio"] = (f / r["triangles"]) if f is not None and r.get("triangles") else (0.0 if f == 0 else None)
    m["islands"] = None
    m["ngons"] = obj["mesh"].get("ngons")
    m["quads"] = obj["mesh"].get("quads")
    m["uv_layers"] = r.get("uv_layers", obj["mesh"].get("uv_sets", 0))
    m["has_uv"] = (m["uv_layers"] or 0) > 0 if ("uv_layers" in r) else None
    m["uv_out_of_range_fraction"] = r.get("uv_out_of_range_fraction")
    m["uv_overlap_ratio"] = r.get("uv_overlap_ratio")
    m["uv_coverage"] = r.get("uv_coverage")
    m["uv_zero_area_tris"] = None
    m["texel_spread"] = r.get("texel_density_spread")
    m["texel_median"] = None
    m["origin_offset_reported"] = r.get("origin_offset")
    m["skin"] = None
    return m


def measure_asset(asset: dict) -> dict:
    """Attach ``asset['metrics'] = {object id: metrics}`` for every mesh object (idempotent)."""
    if "metrics" in asset:
        return asset["metrics"]
    asset["metrics"] = {o["id"]: measure_mesh(o) for o in asset["objects"] if o["kind"] == "mesh" and o.get("mesh") is not None}
    return asset["metrics"]
