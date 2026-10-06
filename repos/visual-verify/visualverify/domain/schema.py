"""Fields of a manifest entry's ``domain`` object for reference images, target profiles and measurement cases."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "image_kind": {"enum": ["render", "screenshot", "texture", "tile", "pbr_map", "mask"], "description": "What the image is."},
        "size": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 2, "maxItems": 2, "description": "Width and height in pixels."},
        "tool": {"type": "string", "description": "The visual-verify tool a measurement case exercises."},
        "expected": {"type": "object", "description": "Hand-computed or generator-known values a correct measurement must reproduce."},
        "generator": {"type": "object", "description": "The evalgen spec that produced a synthetic example (so it can be regenerated)."},
        "profile": {"type": "string", "description": "Name of the saved target profile this reference was used for."},
        "notes": {"type": "string"},
    },
}
