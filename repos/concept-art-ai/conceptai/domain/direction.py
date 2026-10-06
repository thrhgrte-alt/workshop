"""Visual directions: meaningfully different options for one brief, chosen by farthest-point sampling.

A *direction* fixes one option on each axis (silhouette, palette scheme, lighting, composition, materials, mood, ...). Asking a model for
"five variations" usually yields five near-identical images. Here the axes are explicit, so directions differ on purpose: candidates are
sampled with a seeded RNG, then picked greedily so each new direction is as far as possible from those already chosen. Environments and
props use different axes. Axes the brief locks are held fixed in every direction.

This module is deterministic (same brief + seed = same directions) and has no image dependencies.
"""

from __future__ import annotations

import colorsys
import hashlib
import random
import re
from itertools import product

KINDS = ("environment", "prop")
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,40}$")

# axis -> option -> short description used in prompts. Order is stable (determinism).
AXES: dict[str, dict[str, dict[str, str]]] = {
    "environment": {
        "silhouette": {
            "tall_vertical": "tall vertical masses that rise out of frame",
            "wide_horizontal": "a long low horizontal skyline",
            "layered_diagonal": "layered diagonal shapes stepping into depth",
            "central_mass": "one dominant central mass with open space around it",
        },
        "palette_scheme": {
            "analogous_warm": "analogous warm palette",
            "analogous_cool": "analogous cool palette",
            "complementary": "complementary palette with one clear accent",
            "triadic_muted": "muted triadic palette",
            "monochrome_accent": "near-monochrome palette with a single saturated accent",
        },
        "lighting": {
            "high_noon": "hard overhead midday light with short shadows",
            "golden_hour": "low golden-hour light with long warm shadows",
            "overcast_soft": "soft overcast light, gentle shadows",
            "night_lit": "night scene lit by a few practical light sources",
            "backlit_fog": "backlit atmospheric fog with glowing edges",
        },
        "composition": {
            "thirds_left": "focal point on a left third line",
            "centered_symmetry": "centered, near-symmetrical composition",
            "leading_lines": "strong leading lines toward the focal point",
            "framed_depth": "foreground framing elements that create depth",
        },
        "materials": {
            "weathered_stone": "weathered stone",
            "aged_wood": "aged timber",
            "overgrown": "plants and moss overgrowing structures",
            "metal_industrial": "riveted metal and industrial surfaces",
            "painted_plaster": "painted plaster and tile",
        },
        "mood": {
            "serene": "serene and inviting",
            "ominous": "ominous and tense",
            "whimsical": "whimsical and playful",
            "grand": "grand and awe-inspiring",
        },
    },
    "prop": {
        "silhouette": {
            "round_bulky": "round, bulky silhouette",
            "tall_slender": "tall, slender silhouette",
            "angular_wedge": "angular, wedge-like silhouette",
            "asymmetric_hooked": "asymmetric silhouette with a hooked or curved feature",
        },
        "palette_scheme": {
            "analogous_warm": "analogous warm palette",
            "analogous_cool": "analogous cool palette",
            "complementary": "complementary palette with one clear accent",
            "triadic_muted": "muted triadic palette",
            "monochrome_accent": "near-monochrome palette with a single saturated accent",
        },
        "material_focus": {
            "wood_dominant": "mostly wood",
            "metal_dominant": "mostly metal",
            "stone_dominant": "mostly stone or ceramic",
            "cloth_leather": "cloth and leather",
            "mixed_materials": "a clear mix of two or three materials",
        },
        "detail_level": {
            "simple_chunky": "simple, chunky forms with few details",
            "medium": "moderate detail, readable at small size",
            "ornate": "ornate detail concentrated on one area",
        },
        "view": {
            "three_quarter": "three-quarter view",
            "front_side": "front and side views on one sheet",
            "top_down": "top-down view",
        },
        "wear": {
            "pristine": "pristine",
            "worn": "worn with use",
            "damaged": "damaged and patched",
        },
    },
}

# how much each axis counts toward "meaningfully different"
WEIGHTS = {
    "environment": {"silhouette": 1.5, "palette_scheme": 2.0, "lighting": 2.0, "composition": 1.5, "materials": 1.0, "mood": 1.0},
    "prop": {"silhouette": 2.0, "palette_scheme": 1.5, "material_focus": 1.5, "detail_level": 1.0, "view": 0.5, "wear": 1.0},
}

# combinations that contradict each other (checked as (axis_a, option_a, axis_b, option_b))
INCOMPATIBLE = {
    "environment": [("lighting", "night_lit", "mood", "serene"), ("lighting", "night_lit", "palette_scheme", "analogous_warm"),
                    ("lighting", "golden_hour", "palette_scheme", "analogous_cool"),
                    ("lighting", "high_noon", "mood", "ominous")],
    "prop": [("detail_level", "ornate", "wear", "damaged"), ("detail_level", "simple_chunky", "view", "front_side")],
}

# palette scheme -> (hue offsets from the base hue in degrees, saturation, value) for dominant/secondary/accent
SCHEMES = {
    "analogous_warm": ([0, 20, 40], 0.45, 0.75),
    "analogous_cool": ([0, -25, -50], 0.40, 0.70),
    "complementary": ([0, 15, 180], 0.55, 0.75),
    "triadic_muted": ([0, 120, 240], 0.30, 0.70),
    "monochrome_accent": ([0, 5, 180], 0.20, 0.65),
}
WARM_BASE = (20, 45)
COOL_BASE = (190, 250)
DEFAULT_MIN_DIFFERENT = 3


def axes_for(kind: str) -> dict[str, dict[str, dict[str, str]]]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return AXES[kind]


def normalize_brief(brief: dict) -> dict:
    b = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in brief.items()}
    if not str(b.get("subject", "")).strip():
        raise ValueError("brief needs a non-empty 'subject' (e.g. 'a ruined lighthouse on a cliff')")
    if len(b["subject"]) > 400:
        raise ValueError("subject is too long (max 400 characters); put detail in 'notes'")
    b.setdefault("kind", "environment")
    if b["kind"] not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    b.setdefault("locked", {})
    b.setdefault("avoid", [])
    b.setdefault("reference_ids", [])
    b.setdefault("notes", "")
    lk = b["locked"]
    axes = AXES[b["kind"]]
    for axis, value in lk.get("axes", {}).items():
        if axis not in axes:
            raise ValueError(f"locked axis '{axis}' is not an axis of {b['kind']} (axes: {sorted(axes)})")
        if value not in axes[axis]:
            raise ValueError(f"locked axis '{axis}' has no option '{value}' (options: {sorted(axes[axis])})")
    if "palette" in lk and not (isinstance(lk["palette"], list) and 3 <= len(lk["palette"]) <= 8):
        raise ValueError("a locked palette needs 3-8 colours as '#rrggbb' (the first five are used as dominant, secondary, accent, shadow, highlight)")
    for h in lk.get("palette", []):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(h)):
            raise ValueError(f"locked palette entries must be '#rrggbb' (got {h!r})")
    if not isinstance(b["avoid"], list) or not all(isinstance(x, str) for x in b["avoid"]):
        raise ValueError("avoid must be a list of strings")
    return b


def _free_axes(brief: dict) -> dict[str, list[str]]:
    locked = brief["locked"].get("axes", {})
    return {a: list(o) for a, o in AXES[brief["kind"]].items() if a not in locked}


def _compatible(kind: str, combo: dict) -> bool:
    return not any(combo.get(a) == oa and combo.get(b) == ob for a, oa, b, ob in INCOMPATIBLE[kind])


def distance(kind: str, a: dict, b: dict) -> float:
    """Weighted number of axes on which two directions differ (axes dict only)."""
    w = WEIGHTS[kind]
    return sum(w[axis] for axis in w if a["axes"].get(axis) != b["axes"].get(axis))


def axes_differing(kind: str, a: dict, b: dict) -> int:
    return sum(1 for axis in WEIGHTS[kind] if a["axes"].get(axis) != b["axes"].get(axis))


def palette_for(scheme: str, base_hue: float) -> list[dict]:
    """Five roles (dominant, secondary, accent, shadow, highlight) derived from a scheme and base hue. Deterministic."""
    offsets, sat, val = SCHEMES[scheme]

    def hexc(h, s, v):
        r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, min(max(s, 0), 1), min(max(v, 0), 1))
        return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))

    dom, sec, acc = (base_hue + o for o in offsets)
    acc_sat = min(sat + 0.35, 0.95) if scheme in ("complementary", "monochrome_accent") else sat + 0.1
    return [{"role": "dominant", "hex": hexc(dom, sat, val)}, {"role": "secondary", "hex": hexc(sec, sat * 0.9, val * 0.85)},
            {"role": "accent", "hex": hexc(acc, acc_sat, min(val + 0.1, 0.95))}, {"role": "shadow", "hex": hexc(dom + 10, sat * 0.8, val * 0.28)},
            {"role": "highlight", "hex": hexc(sec, sat * 0.3, 0.96)}]


def _base_hue(scheme: str, lighting: str | None, rng: random.Random) -> float:
    """The scheme decides the hue family when it has one; otherwise the lighting nudges it (golden hour warm, night cool)."""
    if scheme == "analogous_warm":
        lo, hi = WARM_BASE
    elif scheme == "analogous_cool":
        lo, hi = COOL_BASE
    elif lighting == "golden_hour":
        lo, hi = WARM_BASE
    elif lighting == "night_lit":
        lo, hi = COOL_BASE
    else:
        lo, hi = 0, 360
    return rng.uniform(lo, hi)


def _stable_id(kind: str, axes: dict) -> str:
    digest = hashlib.sha1(("|".join(f"{k}={axes[k]}" for k in sorted(axes))).encode()).hexdigest()[:6]
    return f"{kind[:3]}_{digest}"


def generate_directions(brief: dict, n: int = 4, seed: int = 0, min_axes_different: int = DEFAULT_MIN_DIFFERENT) -> dict:
    """Pick ``n`` directions that differ from each other on at least ``min_axes_different`` axes where possible.

    Returns ``{"directions": [...], "min_pairwise_distance", "min_axes_differing", "warnings": [...], "locked_axes": {...}}``.
    """
    b = normalize_brief(brief)
    if not 1 <= n <= 12:
        raise ValueError("n must be between 1 and 12")
    kind = b["kind"]
    locked_axes = dict(b["locked"].get("axes", {}))
    free = _free_axes(b)
    names = list(AXES[kind])
    total = 1
    for opts in free.values():
        total *= len(opts)
    rng = random.Random(f"{seed}|{b['subject']}|{kind}|{sorted(locked_axes.items())}")
    if total <= 4000:
        pool = [dict(zip(free, vals)) for vals in product(*free.values())]
    else:
        pool = [{a: rng.choice(o) for a, o in free.items()} for _ in range(4000)]
    pool = [{**locked_axes, **c} for c in pool]
    pool = [c for c in pool if _compatible(kind, c)]
    if not pool:
        raise ValueError("no compatible combination remains; relax locked axes")
    rng.shuffle(pool)
    cand = [{"axes": {a: c[a] for a in names}} for c in pool]
    chosen = [cand[0]]
    while len(chosen) < min(n, len(cand)):
        best, best_key = None, None
        for c in cand:
            if c in chosen:
                continue
            ds = [distance(kind, c, s) for s in chosen]
            key = (min(ds), sum(ds))  # farthest from the nearest chosen one, ties by total spread
            if best_key is None or key > best_key:
                best, best_key = c, key
        chosen.append(best)
    warnings = []
    if len(chosen) < n:
        warnings.append(f"only {len(chosen)} compatible directions exist for this brief (asked for {n}); unlock an axis to get more")
    out = []
    for c in chosen:
        ax = c["axes"]
        scheme = ax["palette_scheme"]
        locked_pal = b["locked"].get("palette")
        palette = [{"role": r, "hex": h} for r, h in zip(("dominant", "secondary", "accent", "shadow", "highlight"), locked_pal)] if locked_pal else None
        if palette is None:
            prng = random.Random(f"{seed}|{_stable_id(kind, ax)}")
            palette = palette_for(scheme, _base_hue(scheme, ax.get("lighting"), prng))
        out.append({"id": _stable_id(kind, ax), "kind": kind, "axes": ax, "palette": palette, "palette_locked": bool(locked_pal),
                    "descriptions": {a: AXES[kind][a][ax[a]] for a in names}})
    pair = [(distance(kind, x, y), axes_differing(kind, x, y)) for i, x in enumerate(out) for y in out[i + 1:]]
    min_d = min((p[0] for p in pair), default=None)
    min_ax = min((p[1] for p in pair), default=None)
    # a locked axis can never differ; the requirement is capped by the number of free axes
    need = min(min_axes_different, len(free))
    if len(free) < min_axes_different:
        warnings.append(f"only {len(free)} axes are unlocked, so directions can differ on at most {len(free)} axes (wanted {min_axes_different}); unlock axes for more variety")
    if min_ax is not None and min_ax < need:
        warnings.append(f"some directions differ on only {min_ax} axes (wanted {need}); the available combinations are too similar - unlock axes or ask for fewer")
    return {"kind": kind, "seed": seed, "directions": out, "min_pairwise_distance": min_d, "min_axes_differing": min_ax,
            "locked_axes": locked_axes, "free_axes": sorted(free), "warnings": warnings}


def describe(direction: dict) -> str:
    return "; ".join(direction["descriptions"].values())
