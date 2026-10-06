"""VFX-specific fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "effect_kind": {"enum": ["burst", "loop", "beam", "trail", "impact", "aura", "projectile", "ambient"]},
        "recipe": {"type": "string", "description": "Effect recipe id in recipes/ that builds this, if any"},
        "variant_of": {"type": "string"},
        "duration_seconds": {"type": "number", "minimum": 0},
        "particles_peak": {"type": "number", "minimum": 0},
        "texture_asset_ids": {"type": "array", "items": {"type": "string"},
                              "description": "rbxassetid:// ids this effect relies on (your own assets)"},
        "intended_distance_studs": {"type": "number", "minimum": 0},
        "platforms": {"type": "array", "items": {"enum": ["mobile", "desktop", "console"]}},
        "theme": {"type": "array", "items": {"type": "string"}},
        "measurements": {"type": "object"},
    },
}
