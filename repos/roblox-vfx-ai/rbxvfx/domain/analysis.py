"""Budget, readability and timing analysis of an effect plan.

Everything here is a deterministic *estimate* from the plan's properties, not a measurement of a running
game. Budget limits and thresholds live in ``style/style.yaml`` and are **heuristic defaults** meant to be
tuned against your own device profiling; they are not Roblox's official limits. Where Roblox's default
for an unset property is unknown to this repository, the analysis assumes a value and lists it under
``assumptions`` so nothing is silently invented.
"""

from __future__ import annotations

import math
from typing import Any

from rbxvfx.core.retrieval import color_distance, rgb_to_lab
from rbxvfx.core.style import check_ranges

ASSUMED = {"Rate": 20.0, "Size": 1.0, "Lifetime": 1.0, "Segments": 10, "Width0": 1.0, "Width1": 1.0,
           "Transparency": 0.0, "Enabled": True}


def _lum(rgb) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _seq_values(seq: dict) -> list[float]:
    return [k[1] for k in seq["keys"]]


def _seq_mean(seq: dict) -> float:
    keys = seq["keys"]
    total = 0.0
    for a, b in zip(keys, keys[1:]):
        total += (a[1] + b[1]) / 2 * (b[0] - a[0])
    return total


def _seq_first_below(seq: dict, level: float) -> float:
    """First time (0-1) at which the sequence is at or below ``level`` (1.0 if never)."""
    keys = seq["keys"]
    if keys[0][1] <= level:
        return keys[0][0]
    for a, b in zip(keys, keys[1:]):
        if b[1] <= level:
            if a[1] == b[1]:
                return b[0]
            return a[0] + (b[0] - a[0]) * (a[1] - level) / (a[1] - b[1])
    return 1.0


def screen_fraction(size_studs: float, distance: float, fov_degrees: float) -> float:
    """Apparent height of an object as a fraction of screen height (pinhole camera)."""
    return size_studs / (2 * distance * math.tan(math.radians(fov_degrees) / 2))


def _get(props: dict, name: str, notes: list, where: str) -> Any:
    return props[name] if name in props else _assume(name, notes, where)


def _assume(name: str, notes: list, where: str) -> Any:
    notes.append(f"{where}: '{name}' not set; assumed {ASSUMED[name]}")
    return ASSUMED[name]


def analyze(plan: dict, style: dict, platform: str = "mobile", distance: float | None = None) -> dict:
    gameplay = style.get("gameplay", {})
    distance = distance or gameplay.get("distance_studs", 30)
    fov = gameplay.get("fov_degrees", 70)
    aspect = gameplay.get("aspect", 16 / 9)
    backgrounds = [_hex_rgb(c) for c in style.get("traits", {}).get("test_backgrounds_hex", [])]
    palette = style.get("traits", {}).get("palette_hex", [])
    notes: list[str] = []

    particles = coverage = 0.0
    biggest = 0.0
    beam_segments = beams = trails = lights = 0
    abrupt: list[str] = []
    attack_worst = 0.0
    colors: list[tuple[tuple, float]] = []  # (rgb, light_emission)
    radius = 0.5
    midpoint = [0.0, 0.0, 0.0]
    for inst in plan["instances"]:
        iid, cls, props = inst["id"], inst["class"], inst["properties"]
        if cls == "ParticleEmitter":
            enabled = _get(props, "Enabled", notes, iid)
            rate = _get(props, "Rate", notes, iid)
            lt = props.get("Lifetime") or {"min": _assume("Lifetime", notes, iid), "max": ASSUMED["Lifetime"]}
            size = props.get("Size") or {"keys": [[0, _assume("Size", notes, iid), 0], [1, ASSUMED["Size"], 0]]}
            transp = props.get("Transparency") or {"keys": [[0, _assume("Transparency", notes, iid), 0], [1, ASSUMED["Transparency"], 0]]}
            steady = rate * (lt["min"] + lt["max"]) / 2 if enabled else 0.0
            burst = plan["emit"].get(iid, 0)
            alive = steady + burst
            particles += alive
            size_max = max(_seq_values(size))
            f = screen_fraction(size_max, distance, fov)
            biggest = max(biggest, f)
            opacity = 1 - _seq_mean(transp)
            coverage += alive * (f * f / aspect) * max(opacity, 0.0)
            if alive > 0:
                if transp["keys"][-1][1] < 0.95 and size["keys"][-1][1] > 0.05 * size_max:
                    abrupt.append(iid)
                attack_worst = max(attack_worst, _seq_first_below(transp, 0.9))
            speed = props.get("Speed", {"max": 0})["max"]
            radius = max(radius, speed * lt["max"] * 0.6 + size_max)
            if "Color" in props:
                glow = props.get("LightEmission", 0.0)
                for _, rgb in props["Color"]["keys"]:
                    colors.append((tuple(rgb), glow))
        elif cls == "Beam":
            beams += 1
            beam_segments += _get(props, "Segments", notes, iid)
            width = max(_get(props, "Width0", notes, iid), _get(props, "Width1", notes, iid))
            biggest = max(biggest, screen_fraction(width, distance, fov))
            if "Color" in props:
                for _, rgb in props["Color"]["keys"]:
                    colors.append((tuple(rgb), props.get("LightEmission", 0.0)))
        elif cls == "Trail":
            trails += 1
            attachments = {i["id"]: i for i in plan["instances"] if i["class"] == "Attachment"}
            a0, a1 = (attachments.get(inst["refs"].get(n)) for n in ("Attachment0", "Attachment1"))
            sep = 1.0
            if a0 and a1 and "Position" in a0["properties"] and "Position" in a1["properties"]:
                sep = math.dist(a0["properties"]["Position"]["v"], a1["properties"]["Position"]["v"])
            biggest = max(biggest, screen_fraction(sep, distance, fov))
            transp = props.get("Transparency") or {"keys": [[0, 0, 0], [1, 0, 0]]}
            if transp["keys"][-1][1] < 0.9:
                abrupt.append(inst["id"])
            if "Color" in props:
                for _, rgb in props["Color"]["keys"]:
                    colors.append((tuple(rgb), props.get("LightEmission", 0.0)))
        elif cls == "PointLight":
            lights += 1
        elif cls == "Attachment" and iid == "end_point" and "Position" in props:
            midpoint = [c / 2 for c in props["Position"]["v"]]
            radius = max(radius, math.dist([0, 0, 0], props["Position"]["v"]) / 2)

    contrast_by_bg: dict[str, float] = {}
    separation_by_bg: dict[str, float] = {}
    if colors and backgrounds:
        for bg in backgrounds:
            key = "#%02x%02x%02x" % tuple(round(c * 255) for c in bg)
            lb = _lum(bg)
            lab_bg = rgb_to_lab([c * 255 for c in bg])
            best_ratio = best_de = 0.0
            for rgb, glow in colors:
                lifted = [c + (1 - c) * glow * 0.35 for c in rgb]  # additive blending lifts and desaturates colour
                le = _lum(lifted)
                best_ratio = max(best_ratio, contrast_ratio(le, lb))
                best_de = max(best_de, math.dist(rgb_to_lab([c * 255 for c in lifted]), lab_bg))
            contrast_by_bg[key] = round(best_ratio, 3)
            separation_by_bg[key] = round(best_de, 1)
    theme_distance = None
    if colors and palette:
        primary = max(colors, key=lambda c: max(c[0]) - min(c[0]))[0]  # the most saturated key colour
        hexed = "#%02x%02x%02x" % tuple(round(c * 255) for c in primary)
        theme_distance = min(color_distance(hexed, p) for p in palette)

    metrics = {
        "particles_peak": round(particles, 1),
        "beam_segments_total": beam_segments,
        "beams": beams,
        "trails": trails,
        "lights": lights,
        "screen_coverage": round(min(coverage, 1.0), 4),
        "apparent_height_fraction": round(biggest, 4),
        "abrupt_end_count": len(abrupt),
        "attack_fraction": round(attack_worst, 3),
    }
    if contrast_by_bg:
        metrics["luminance_contrast_min"] = min(contrast_by_bg.values())  # informational: ignores hue
        metrics["color_separation_min"] = min(separation_by_bg.values())
    if theme_distance is not None:
        metrics["theme_distance"] = round(theme_distance, 1)
    if plan.get("duration_estimate") is not None:
        metrics["duration_seconds"] = plan["duration_estimate"]

    ranges = dict(style.get("ranges", {}))
    platform_cfg = style.get("platforms", {}).get(platform)
    if platform_cfg is None:
        raise ValueError(f"unknown platform '{platform}'. Known: {sorted(style.get('platforms', {}))}")
    ranges.update(platform_cfg.get("ranges", {}))
    findings = check_ranges(metrics, ranges)
    findings = [f for f in findings if f["severity"] != "info"]
    for iid in abrupt:
        findings.append({"metric": "abrupt_end", "severity": "warning", "value": 1,
                         "message": f"'{iid}' ends abruptly (still visible at the end of its life); fade transparency to ~1 or shrink to ~0"})
    return {"platform": platform, "gameplay": {"distance_studs": distance, "fov_degrees": fov}, "metrics": metrics,
            "luminance_contrast_by_background": contrast_by_bg, "color_separation_by_background": separation_by_bg, "findings": findings, "assumptions": notes,
            "radius_estimate_studs": round(radius, 2), "focus_point": [round(c, 2) for c in midpoint],
            "disclaimer": "Estimates from the plan, with heuristic thresholds from style.yaml; profile on real devices."}


def camera_setups(analysis: dict, origin=(0.0, 5.0, 0.0)) -> list[dict]:
    """Camera positions/look-at targets (world space) to capture the effect at gameplay and close range."""
    fov = analysis["gameplay"]["fov_degrees"]
    d_game = analysis["gameplay"]["distance_studs"]
    r = analysis["radius_estimate_studs"]
    fit = r / math.tan(math.radians(fov) / 2) * 1.3  # distance at which the whole effect fits
    focus = [origin[i] + analysis["focus_point"][i] for i in range(3)]
    out = []
    for name, dist, az, el in (("gameplay", max(d_game, fit * 0.5), 35, 15), ("close", max(fit, 6.0), -40, 10),
                               ("top_down", max(fit, 8.0), 0, 80)):
        a, e = math.radians(az), math.radians(el)
        pos = [focus[0] + dist * math.cos(e) * math.sin(a), focus[1] + dist * math.sin(e), focus[2] + dist * math.cos(e) * math.cos(a)]
        out.append({"name": name, "position": [round(c, 2) for c in pos], "look_at": [round(c, 2) for c in focus],
                    "distance_studs": round(dist, 1)})
    return out
