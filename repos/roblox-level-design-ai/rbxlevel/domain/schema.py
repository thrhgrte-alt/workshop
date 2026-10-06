"""Level-design fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "level_type": {"enum": ["arena", "hub", "linear", "maze", "open", "vertical", "mixed"]},
        "template": {"type": "string", "description": "Template id in recipes/ this level was built from, if any"},
        "spec_file": {"type": "string", "description": "Path to the level spec (JSON/YAML) kept outside or inside the repo"},
        "rooms": {"type": "integer", "minimum": 1},
        "loops": {"type": "integer", "minimum": 0},
        "teams": {"type": "integer", "minimum": 0},
        "players": {"type": "string", "description": "e.g. '2-4' or '1'"},
        "footprint_studs": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "measurements": {"type": "object"},
        "themes": {"type": "array", "items": {"type": "string"}},
    },
}
