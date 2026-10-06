# Projects and places

`projects.yaml` lists the user's games: `id`, `alias`, `universe_id`, `ruleset`, and `places` (each: `id`, `place_id`, `name`, `path`, optional `ruleset`). The bundled entries are SYNTHETIC examples (`example-obby`, `example-tycoon`, `example-sandbox`); the user replaces them.

- A tool that reads or writes project data without a known `project_id` refuses and lists the known projects. A `place_id` must belong to the project; it may be the registry id or the Roblox place id.
- **Ruleset layers**, in order: `projects/_global/ruleset.yaml` (every project) -> the project's ruleset -> the place's ruleset -> an explicit `ruleset` argument. Each layer can disable rules, override severities and hold suppressions. Later layers win for severities.
- **Where things are recorded:** `suppress_finding` writes to the project's ruleset (with `place_id` the place's; with `apply_globally` the global one). `mark_false_positive` stores the pattern in `workspace/feedback/projects/<project_id>/` (with `place_id` it applies to that place only; `apply_globally` stores it for every project).
- `review_folder` labels each file with the place whose `path` is a sub-folder of the reviewed folder (or the named `place_id`). Files that match no place are labelled with none, never guessed.
- Every report names the project and place and lists the layers, the suppressions that matched and the ones that matched nothing, so skipped findings are visible.
- This tool never talks to Studio, so it cannot check that the open place matches `place_id`; when you get a script from the hub, check the place name or id the hub reports before saving it.
