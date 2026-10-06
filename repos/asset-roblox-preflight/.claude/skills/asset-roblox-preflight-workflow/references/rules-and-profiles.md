# Rules and profiles

* Rules: `rules/*.yaml` (mesh, transform, names, uvs, materials, rig, collision, upload). Each: id, severity, explanation, one-line fix, `safe`, limits with `verify_against_current_docs`.
  `list_profiles(include_rules=true)` lists them; `explain_finding` explains one.
* Profiles: `prop`, `tool`, `character_accessory`, `terrain_piece` in `rules/profiles/`. Projects layer overrides from `projects/<project_id>/profiles.yaml` (limits, severities, disabled rules, naming).
* Every number is a DEFAULT chosen by this tool's author. Do not quote them as Roblox limits.
* Rig rules run only for `tool` and `character_accessory`; elsewhere a rig is `RIG_UNEXPECTED`.
* `safe: true` rules (the only ones `apply_safe_fix` touches): XFM_UNAPPLIED_SCALE, XFM_UNAPPLIED_ROTATION, NAME_DUPLICATE (bone vs object), NAME_INVALID_CHARS, NAME_BLENDER_SUFFIX, RIG_ROOT_NAME, RIG_BONE_NAMING, RIG_SPIN_SETUP (rename only).
