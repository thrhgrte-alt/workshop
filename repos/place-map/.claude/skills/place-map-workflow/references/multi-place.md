# Several places

- One index and snapshot folder per place. Ids: the Roblox place id, or `local-<alias>` until the place is published. The registry is `projects.yaml` (optionally plus `places.yaml`, same format).
- `find_in_place` searches the active place only. `find_across_places`, `compare_places` and `find_shared_code` are separate tools and stay inside ONE project (the places of one game).
- `find_shared_code` matches scripts by content hash across places and lists modules with the same name and different hashes as drifted. A cut or unread source cannot be compared and is skipped.
- Landmarks, labels and corrections belong to one place unless marked global. The summary cache (keyed by content hash) is shared; anything about what exists in a place is not.
- Before collecting, the open Studio place must match the named one (by Roblox place id, else by Studio name). If it does not, the tool refuses and says which place is open.
