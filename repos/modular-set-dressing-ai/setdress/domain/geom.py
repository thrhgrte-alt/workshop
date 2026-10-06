"""Plan-view geometry for placed modules: oriented footprints, separating-axis overlap, sockets, snapping.

Conventions (match ``CFrame.Angles(0, math.rad(yaw), 0)`` and ``Part.Orientation = Vector3.new(0, yaw, 0)``):
``x`` and ``z`` are horizontal studs, ``y`` is up. A module's local +Z is its *forward* direction. ``yaw`` (degrees)
rotates local +Z toward +X, so the forward vector is ``(sin yaw, cos yaw)`` in (x, z), and a local point
``(lx, lz)`` maps to ``(lx*cos + lz*sin, -lx*sin + lz*cos)`` before adding the position.
Verify the orientation convention visually in Studio on the first placement of a new kit.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def rot(lx: float, lz: float, yaw_deg: float) -> Point:
    t = math.radians(yaw_deg)
    c, s = math.cos(t), math.sin(t)
    return lx * c + lz * s, -lx * s + lz * c


def to_world(local: Point, pos: Point, yaw_deg: float, scale: float = 1.0) -> Point:
    x, z = rot(local[0] * scale, local[1] * scale, yaw_deg)
    return pos[0] + x, pos[1] + z


def forward(yaw_deg: float) -> Point:
    t = math.radians(yaw_deg)
    return math.sin(t), math.cos(t)


def norm_yaw(a: float) -> float:
    """Wrap to (-180, 180]."""
    a = a % 360.0
    return a - 360.0 if a > 180.0 else a


def angle_diff(a: float, b: float) -> float:
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


def footprint_corners(pos: Point, yaw_deg: float, size: Point, scale: float = 1.0, offset: Point = (0.0, 0.0)) -> list[Point]:
    """Corners of the oriented footprint. ``offset`` is the footprint centre in module-local space (non-zero for corner pivots)."""
    hw, hd = size[0] * scale / 2, size[1] * scale / 2
    ox, oz = offset[0] * scale, offset[1] * scale
    local = [(ox - hw, oz - hd), (ox + hw, oz - hd), (ox + hw, oz + hd), (ox - hw, oz + hd)]
    out = []
    for lx, lz in local:
        wx, wz = rot(lx, lz, yaw_deg)
        out.append((pos[0] + wx, pos[1] + wz))
    return out


def _axes(poly: list[Point]) -> list[Point]:
    axes = []
    for i in range(len(poly)):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % len(poly)]
        ex, ez = x2 - x1, z2 - z1
        n = math.hypot(ex, ez) or 1.0
        axes.append((-ez / n, ex / n))
    return axes


def _project(poly: list[Point], axis: Point) -> tuple[float, float]:
    vals = [p[0] * axis[0] + p[1] * axis[1] for p in poly]
    return min(vals), max(vals)


def overlap_depth(a: list[Point], b: list[Point]) -> float:
    """Penetration depth of two convex polygons along the best separating axis (0 when separated or merely touching)."""
    best = math.inf
    for axis in _axes(a) + _axes(b):
        a0, a1 = _project(a, axis)
        b0, b1 = _project(b, axis)
        o = min(a1, b1) - max(a0, b0)
        if o <= 1e-9:
            return 0.0
        best = min(best, o)
    return best


def rect_corners(x: float, z: float, w: float, d: float) -> list[Point]:
    return [(x, z), (x + w, z), (x + w, z + d), (x, z + d)]


def segment_rect(a: Point, b: Point, width: float) -> list[Point]:
    """Rectangle of ``width`` around segment a-b, extended by half the width at both ends (a capsule's bounding box)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dz)
    if n < 1e-9:
        h = width / 2
        return [(a[0] - h, a[1] - h), (a[0] + h, a[1] - h), (a[0] + h, a[1] + h), (a[0] - h, a[1] + h)]
    ux, uz = dx / n, dz / n
    px, pz = -uz * width / 2, ux * width / 2
    ex, ez = ux * width / 2, uz * width / 2
    a2, b2 = (a[0] - ex, a[1] - ez), (b[0] + ex, b[1] + ez)
    return [(a2[0] + px, a2[1] + pz), (b2[0] + px, b2[1] + pz), (b2[0] - px, b2[1] - pz), (a2[0] - px, a2[1] - pz)]


def inside_rect(poly: list[Point], x: float, z: float, w: float, d: float, eps: float = 1e-6) -> bool:
    return all(x - eps <= px <= x + w + eps and z - eps <= pz <= z + d + eps for px, pz in poly)


def polygon_area(poly: list[Point]) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % len(poly)]
        s += x1 * z2 - x2 * z1
    return abs(s) / 2


def snap(value: float, step: float) -> float:
    return round(value / step) * step if step else value


def snap_yaw(yaw: float, step: float) -> float:
    return norm_yaw(round(yaw / step) * step) if step else norm_yaw(yaw)


def segment_polygon_intersects(a: Point, b: Point, poly: list[Point]) -> bool:
    """Does the 2-D segment a-b touch the convex polygon (Liang-Barsky style clipping against its edges)?"""
    # inside test for endpoints
    def inside(p: Point) -> bool:
        sign = 0
        for i in range(len(poly)):
            x1, z1 = poly[i]
            x2, z2 = poly[(i + 1) % len(poly)]
            cross = (x2 - x1) * (p[1] - z1) - (z2 - z1) * (p[0] - x1)
            if abs(cross) < 1e-9:
                continue
            s = 1 if cross > 0 else -1
            if sign and s != sign:
                return False
            sign = s
        return True

    if inside(a) or inside(b):
        return True

    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])

    def cross_seg(p1, p2, p3, p4):
        d1, d2 = ccw(p3, p4, p1), ccw(p3, p4, p2)
        d3, d4 = ccw(p1, p2, p3), ccw(p1, p2, p4)
        return (d1 * d2 < 0) and (d3 * d4 < 0)

    return any(cross_seg(a, b, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
