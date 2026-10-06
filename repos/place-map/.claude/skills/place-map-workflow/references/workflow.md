# Workflow in more detail

1. `list_places` shows the registry (project, place alias, Studio name, whether a snapshot exists). It reads no place data.
2. First session in a place: `plan_refresh` (needs `studios`) -> `roblox_studio_execute_luau` -> `ingest_snapshot`. The Luau is read-only, guarded to the named place, skips attributes that look like secrets
   and caps its own work (instances, source characters); if it truncates it says so and the snapshot is flagged `truncated`.
3. Later sessions: `find_in_place` straight away. Incremental refresh embeds the fingerprints of scripts already stored, so only changed scripts are sent back; unchanged scripts keep their cached summaries
   (cache key: content hash, shared across places).
4. Snapshots: the latest plus a few older ones are kept (`snapshots.keep_older`); `prune_snapshots` (dry run first) removes the rest. `diff_snapshots` compares any two.
5. Output size: results are capped at 10 entries; `detail=true` lifts the cap. Large answers cost context; ask for detail only when the short answer is not enough.
6. Errors are refusals with the reason (unknown place, wrong open place, missing snapshot, bad path). Do not work around them: tell the user what is missing.
