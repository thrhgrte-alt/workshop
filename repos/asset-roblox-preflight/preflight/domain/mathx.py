"""Small, dependency-free vector/matrix helpers. Matrices are 16-float lists in glTF (column-major) order."""

from __future__ import annotations

import math

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def mat_mul(a: list[float], b: list[float]) -> list[float]:
    """a * b for column-major 4x4 matrices (apply b first, then a)."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def mat_from_trs(t=(0, 0, 0), q=(0, 0, 0, 1), s=(1, 1, 1)) -> list[float]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - z * w)
    r02 = 2 * (x * z + y * w)
    r10 = 2 * (x * y + z * w)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - x * w)
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    sx, sy, sz = s
    return [r00 * sx, r10 * sx, r20 * sx, 0.0,
            r01 * sy, r11 * sy, r21 * sy, 0.0,
            r02 * sz, r12 * sz, r22 * sz, 0.0,
            t[0], t[1], t[2], 1.0]


def mat_apply(m: list[float], p) -> tuple[float, float, float]:
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


def mat_decompose(m: list[float]):
    """Return (translation, quaternion xyzw, scale) of a TRS matrix (no shear). A negative determinant gives a negative x scale."""
    t = (m[12], m[13], m[14])
    sx = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2)
    sy = math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2)
    sz = math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2)
    det = (m[0] * (m[5] * m[10] - m[6] * m[9]) - m[4] * (m[1] * m[10] - m[2] * m[9]) + m[8] * (m[1] * m[6] - m[2] * m[5]))
    if det < 0:
        sx = -sx
    isx, isy, isz = (1 / sx if sx else 0), (1 / sy if sy else 0), (1 / sz if sz else 0)
    r = [m[0] * isx, m[1] * isx, m[2] * isx, m[4] * isy, m[5] * isy, m[6] * isy, m[8] * isz, m[9] * isz, m[10] * isz]
    # r is column-major 3x3: columns c0 (0..2), c1 (3..5), c2 (6..8)
    r00, r10, r20, r01, r11, r21, r02, r12, r22 = r
    tr = r00 + r11 + r22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (r21 - r12) / s, (r02 - r20) / s, (r10 - r01) / s
    elif r00 > r11 and r00 > r22:
        s = math.sqrt(1.0 + r00 - r11 - r22) * 2
        w, x, y, z = (r21 - r12) / s, 0.25 * s, (r01 + r10) / s, (r02 + r20) / s
    elif r11 > r22:
        s = math.sqrt(1.0 + r11 - r00 - r22) * 2
        w, x, y, z = (r02 - r20) / s, (r01 + r10) / s, 0.25 * s, (r12 + r21) / s
    else:
        s = math.sqrt(1.0 + r22 - r00 - r11) * 2
        w, x, y, z = (r10 - r01) / s, (r02 + r20) / s, (r12 + r21) / s, 0.25 * s
    return t, (x, y, z, w), (sx, sy, sz)


def quat_angle_deg(q) -> float:
    """Rotation angle of a quaternion in degrees (0 for identity)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    return math.degrees(2 * math.acos(max(-1.0, min(1.0, abs(w / n)))))


def quat_to_euler_deg(q) -> tuple[float, float, float]:
    """XYZ Euler angles in degrees (intrinsic X then Y then Z convention used for display only)."""
    x, y, z, w = q
    sinr = 2 * (w * x + y * z)
    cosr = 1 - 2 * (x * x + y * y)
    rx = math.atan2(sinr, cosr)
    sinp = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    ry = math.asin(sinp)
    siny = 2 * (w * z + x * y)
    cosy = 1 - 2 * (y * y + z * z)
    rz = math.atan2(siny, cosy)
    return tuple(round(math.degrees(a), 4) for a in (rx, ry, rz))  # type: ignore[return-value]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def length(a) -> float:
    return math.sqrt(dot(a, a))
