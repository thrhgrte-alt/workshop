"""Concept-art fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subject_kind": {"enum": ["environment", "prop"]},
        "concept_only": {"const": True, "description": "Always true: a concept image is never a mesh, a graph or a finished scene."},
        "ai_generated": {"type": "boolean"},
        "gen_id": {"type": "string"},
        "output_id": {"type": "string"},
        "axes": {"type": "object", "description": "Direction axes this image was made from (silhouette, lighting, ...)."},
        "prompt": {"type": "string"},
        "seed": {"type": "integer"},
        "adapter": {"type": "string"},
        "model": {"type": "string", "description": "Model name as declared by the user. Not verified."},
        "measurements": {"type": "object", "description": "Pixel measurements recorded when the entry was curated; re-checked by tests for examples."},
        "intended_palette": {"type": "array", "items": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}, "description": "For avoid-examples that missed their palette: the palette they should have followed."},
        "palette_adherence_vs_intended": {"type": "number", "minimum": 0, "maximum": 1},
        "synthetic_fixture": {"type": "boolean", "description": "True for pipeline fixtures drawn by the placeholder renderer: not art, not a quality reference."},
    },
}
