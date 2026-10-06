"""Luau-review fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rule_ids": {"type": "array", "items": {"type": "string"}, "description": "Rule ids a correct review reports as defects for this script (empty for a clean script)."},
        "question_rule_ids": {"type": "array", "items": {"type": "string"}, "description": "Rule ids a correct review raises as low-confidence questions."},
        "strict": {"type": "boolean", "description": "Reviewed with strict=true."},
        "context": {"enum": ["server", "client", "module"], "description": "Where the script runs, from its file name."},
        "known_gap": {"type": "string", "description": "Set when the planted bug is one the heuristic checker does NOT catch."},
        "lines": {"type": "integer", "minimum": 0},
        "notes": {"type": "string"},
    },
}
