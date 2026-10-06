"""Prompt construction for concept art. Environments and props use different structures on purpose.

An *environment* prompt describes a place seen from a camera: depth layers, lighting, composition, scale cues. A *prop* prompt describes one
isolated object on a neutral background: silhouette, material breakdown, views, wear. Both end with a negative list built from the style
spec's exclusions and the brief's ``avoid``. Prompts are plain text plus structured sections; they contain no provider-specific syntax,
because adapters differ. Nothing here claims an image will be a mesh, a graph or a finished scene.
"""

from __future__ import annotations

import re

from . import direction as D

MAX_PROMPT = 1800
ASPECTS = {"environment": ("16:9", 1344, 768), "prop": ("1:1", 1024, 1024)}
ENV_NEGATIVE = ["text", "watermark", "signature", "logo", "user interface", "cropped subject", "characters unless requested"]
PROP_NEGATIVE = ["text", "watermark", "signature", "background scenery", "multiple unrelated objects", "cropped subject", "hands holding the object"]
FORBIDDEN_CLAIMS = re.compile(r"\b(production[- ]ready|game[- ]ready|ready for (?:production|engine)|final (?:asset|mesh|model)|ready to import)\b", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().strip(".")


def _palette_text(direction: dict) -> str:
    return ", ".join(f"{p['role']} {p['hex']}" for p in direction["palette"][:3])


def build_prompt(brief: dict, direction: dict, style: dict | None = None) -> dict:
    b = D.normalize_brief(brief)
    if direction["kind"] != b["kind"]:
        raise ValueError(f"direction is for kind '{direction['kind']}' but the brief is '{b['kind']}'")
    d, ds = direction, direction["descriptions"]
    subject, notes = _clean(b["subject"]), _clean(b.get("notes", ""))
    refs = b.get("reference_ids", [])
    if b["kind"] == "environment":
        sections = {
            "subject": f"Environment concept art of {subject}",
            "composition": f"Composition: {ds['composition']}; silhouette language: {ds['silhouette']}. Clear foreground, midground and background layers with value separation",
            "lighting": f"Lighting: {ds['lighting']}",
            "palette": f"Palette: {ds['palette_scheme']} ({_palette_text(d)})",
            "materials": f"Materials: {ds['materials']}",
            "mood": f"Mood: {ds['mood']}",
            "scale": "Include scale cues (doors, steps, railings) so a level artist can judge sizes; keep walkable areas and landmarks readable",
        }
        negative = list(ENV_NEGATIVE)
    else:
        sections = {
            "subject": f"Prop concept sheet of {subject}, a single object on a plain neutral grey background",
            "silhouette": f"Silhouette: {ds['silhouette']}; it must read clearly in solid black",
            "view": f"View: {ds['view']}",
            "palette": f"Palette: {ds['palette_scheme']} ({_palette_text(d)})",
            "materials": f"Materials: {ds['material_focus']}; label-free, with clearly separated material zones",
            "detail": f"Detail: {ds['detail_level']}; condition: {ds['wear']}",
            "usage": "Even, soft studio lighting with no cast scenery, so the form and materials can be read for modelling",
        }
        negative = list(PROP_NEGATIVE)
    if notes:
        sections["notes"] = f"Notes: {notes}"
    if refs:
        sections["references"] = f"Follow the visual language of reference ids: {', '.join(refs[:6])} (supplied as images to adapters that accept them; otherwise unused)"
    negative += [_clean(x) for x in b["avoid"]]
    if style:
        negative += [_clean(x) for x in style.get("exclusions", [])]
    seen, neg = set(), []
    for x in negative:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            neg.append(x)
    positive = ". ".join(sections.values()) + "."
    if len(positive) > MAX_PROMPT:
        raise ValueError(f"prompt is {len(positive)} characters (max {MAX_PROMPT}); shorten the subject or notes")
    claims = FORBIDDEN_CLAIMS.search(positive)
    if claims:
        raise ValueError(f"prompt must not claim production readiness ('{claims.group(0)}'); a generated image is concept art only")
    aspect, w, h = ASPECTS[b["kind"]]
    return {"kind": b["kind"], "direction_id": d["id"], "positive": positive, "negative": ", ".join(neg), "sections": sections, "aspect_ratio": aspect,
            "width": w, "height": h, "reference_ids": refs,
            "status": "concept_only",
            "note": "Concept art only: not a mesh, a Substance graph or a Roblox scene. A person or another tool must build from it."}
