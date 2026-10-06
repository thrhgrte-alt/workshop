# Rules and profiles

Everything the preflight judges is declared here. The code in `preflight/domain/checks.py` only *measures* and compares; severities,
explanations, one-line fixes, `safe` flags, enable switches and **every numeric limit** come from these files.

* `*.yaml` - rule files. Each rule has `id`, `severity` (`error|warn|info`), `title`, `explanation`, `fix` (one line; `{object}`, `{measured}`,
  `{limit}` are filled in), `safe` (`true` only if an automated fix exists that is safe to emit as a Blender script, see `apply_safe_fix`),
  optional `applies_to` (profile names), `enabled`, and `limits`.
* Every limit is `{default: ..., verify_against_current_docs: true|false, note: ...}`. **The numbers in this repository are DEFAULTS chosen by the
  author of this tool, not official Roblox limits.** Roblox changes its limits; check the current Creator documentation and edit the numbers
  here (or in a profile) before relying on a result. Limits marked `heuristic: true` are tool heuristics with no Roblox counterpart.
* `profiles/*.yaml` - `prop`, `tool`, `character_accessory`, `terrain_piece`. A profile overrides limits per rule id and sets `settings`
  (`studs_per_unit`, whether rig checks run, ...). A profile may also set `severity` overrides.
* Overrides the user records (`record_override`) are scoped to a profile and layered on top of these files at run time; they never edit
  these files. `suggest_profile_promotions` proposes editing a profile when the same override repeats.

A rule whose `id` has no implementation, or an implementation with no rule, fails the test suite.
