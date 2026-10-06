"""Set-dressing fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene_file": {"type": "string", "description": "Path to the scene JSON"},
        "kit": {"type": "string", "description": "Kit id the scene uses"},
        "region_studs": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "items": {"type": "integer", "minimum": 0},
        "coverage": {"type": "number", "minimum": 0},
        "seed": {"type": "integer"},
        "theme": {"type": "array", "items": {"type": "string"}},
        "footprint": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "height": {"type": "number", "minimum": 0},
        "socket_types": {"type": "array", "items": {"type": "string"}},
        "measurements": {"type": "object"},
    },
}
