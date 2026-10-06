"""Economy fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "spec_file": {"type": "string", "description": "Path to the economy.yaml this entry describes"},
        "expected_verdict": {"enum": ["pass", "fail"], "description": "check_economy verdict recorded when the entry was made"},
        "expected_codes": {"type": "array", "items": {"type": "string"}, "description": "error/warning finding codes recorded then"},
        "days": {"type": "integer", "minimum": 1},
        "seed": {"type": "integer"},
        "archetypes": {"type": "array", "items": {"type": "string"}},
        "changes": {"type": "array", "items": {"type": "object"}, "description": "For rebalance entries: [{path, before, after}]"},
        "measurements": {"type": "object", "description": "Headline metrics from check_economy, re-checked by a test"},
    },
}
