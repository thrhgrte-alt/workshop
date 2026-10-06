"""Designer-specific fields for a manifest entry (the ``domain`` object)."""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "material_type": {"type": "string", "description": "e.g. stone, wood, ground, metal, fabric"},
        "recipe": {"type": "string", "description": "Recipe id in recipes/ that builds this, if any"},
        "graph_summary": {
            "type": "object",
            "description": "Filled from sbsinfo.summarize_sbs(): node count, atomic filters and library instances used",
        },
        "outputs": {"type": "array", "items": {"enum": ["baseColor", "normal", "roughness", "metallic", "height",
                                                         "ambientOcclusion", "opacity", "emissive"]}},
        "tileable": {"type": "boolean"},
        "resolution": {"type": "integer", "minimum": 16},
        "exposed_parameters": {"type": "array", "items": {"type": "string"}},
        "measurements": {"type": "object", "description": "Optional image metrics from compare_material_to_rubric"},
    },
}
