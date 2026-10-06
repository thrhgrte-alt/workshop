"""Fields of a library manifest entry (the ``domain`` object) for the kinds snapshot, landmark and script_summary.

The place index itself lives in per-place workspace folders (snapshots, labels, summary cache), not in the shared example library; the library only holds reviewed REFERENCE entries
(for example a landmark pattern or a summary the user wants to keep) so that guide-core's retrieval and ``promote_run`` keep working for this repository.
"""

DOMAIN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "project_id": {"type": "string"},
        "place_id": {"type": "string", "description": "registry place alias (a record belongs to one place unless marked global)"},
        "snapshot_id": {"type": "string"},
        "path": {"type": "string", "description": "path inside the place, without the place prefix"},
        "role": {"type": "string", "description": "landmark role, for kind=landmark"},
        "content_hash": {"type": "string", "description": "sha256 of the script content, for kind=script_summary"},
        "global": {"type": "boolean", "description": "true when the user marked the entry as applying to every place"},
    },
}
