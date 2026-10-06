"""Target profiles: numbers measured from a reference (or typed in), saved locally, used as the target of later measurements.

A profile holds NUMBERS only: dominant colours, ranges, edge density, silhouette descriptors, and the reference file's name and SHA-256. The reference image
itself is never copied into the profile and nothing is uploaded anywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import pixels as P

SCHEMA = "visualverify-profile/1"
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("a profile name is 1-40 characters: lowercase letters, digits, '-' and '_', starting with a letter or digit")
    return name


def _range(v: Any, label: str, lo: float = 0.0, hi: float = 1.0) -> list[float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
        raise ValueError(f"{label} must be [min, max]")
    a, b = float(v[0]), float(v[1])
    if not (lo <= a <= b <= hi):
        raise ValueError(f"{label} must satisfy {lo} <= min <= max <= {hi} (got {a}, {b})")
    return [a, b]


def validate(profile: dict) -> dict:
    """Return the profile if it is well formed, else raise ValueError listing what is wrong."""
    if not isinstance(profile, dict) or profile.get("schema") != SCHEMA:
        raise ValueError(f"not a {SCHEMA} profile")
    check_name(profile.get("name", ""))
    pal = profile.get("palette") or []
    for c in pal:
        P.hex_to_rgb(c["hex"])
        if not isinstance(c.get("share"), (int, float)) or c["share"] < 0:
            raise ValueError("palette shares must be non-negative numbers")
    r = profile.get("ranges") or {}
    for k in ("saturation_mean", "value_mean", "luminance_mean", "edge_density"):
        if k in r:
            _range(r[k], f"ranges.{k}")
    if "contrast_min" in r and not 0 <= r["contrast_min"] <= 1:
        raise ValueError("ranges.contrast_min must be between 0 and 1")
    if not (pal or r or profile.get("silhouette")):
        raise ValueError("a profile needs at least a palette, a range or a silhouette descriptor")
    return profile


def build(name: str, *, description: str = "", source: dict | None = None, palette: list[dict] | None = None, measured: dict | None = None, margin: float = 0.10,
          overrides: dict | None = None, silhouette: dict | None = None) -> dict:
    """Make a profile. ``measured`` (from measure_image) sets ranges at measured value +/- ``margin``; ``overrides`` replaces any range or ``contrast_min`` explicitly."""
    ranges: dict[str, Any] = {}
    if measured:
        clamp = lambda x: min(max(x, 0.0), 1.0)  # noqa: E731
        around = lambda v: [round(clamp(v - margin), 4), round(clamp(v + margin), 4)]  # noqa: E731
        ranges["saturation_mean"] = around(measured["saturation"]["mean"])
        ranges["value_mean"] = around(measured["value"]["mean"])
        ranges["luminance_mean"] = around(measured["luminance"]["mean"])
        ranges["contrast_min"] = round(max(0.0, measured["luminance"]["contrast"] - margin), 4)
        ranges["edge_density"] = [round(max(0.0, measured["edges"]["density"] * (1 - 2 * margin)), 4), round(min(1.0, measured["edges"]["density"] * (1 + 2 * margin) + 0.005), 4)]
    ranges.update({k: v for k, v in (overrides or {}).items() if v is not None})
    prof = {"schema": SCHEMA, "name": check_name(name), "description": description, "source": source or {}, "palette": palette or [], "ranges": ranges}
    if silhouette:
        prof["silhouette"] = silhouette
    return validate(prof)


def dumps(profile: dict) -> str:
    return json.dumps(profile, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def path_for(base: Path, name: str) -> Path:
    return Path(base) / "profiles" / f"{check_name(name)}.json"
