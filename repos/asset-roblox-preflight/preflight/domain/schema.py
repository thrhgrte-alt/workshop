"""Preflight fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "profile": {"enum": ["prop", "tool", "character_accessory", "terrain_piece"], "description": "Target profile the export was checked against"},
        "format": {"enum": ["glb", "gltf", "obj", "fbx", "summary"], "description": "Export format"},
        "expected_result": {"enum": ["pass", "fail", "error"], "description": "What preflight_report must say for this example (error = the file is refused with a message)"},
        "expected_rules": {"type": "array", "items": {"type": "string"}, "description": "Rule ids that must be found"},
        "triangles": {"type": "integer", "minimum": 0},
        "measurements": {"type": "object"},
    },
}
