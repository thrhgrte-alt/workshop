# Snapshot and collector format

`placemap-snapshot/1` is plain JSON in `workspace/projects/<project_id>/<place_id>/snapshots/`: `instances` (path, name, class, attrs without secrets, tags, position, child count), `scripts` (path, class,
kind server|client|module, content `hash`, quick `fp`, length, requires, remotes fired/handled, DataStore names and keys, instance-name references, string tokens, summary) and metadata (`taken_at`,
`taken_at_source`, `full`, `roots`, `truncated`, `source.parser_status`). Source code is not stored, only its hash and analysis.

Paths are instance names joined by `/` below the data model: `ServerScriptService/Services/ShopService`. A `/` or `%` inside a name is written `%2F` / `%25`; the second sibling with the same name gets `#2`.
Answers prefix the place alias: `dive-and-mine::ServerScriptService/Services/ShopService`.

The collector's output format `placemap-collect/1` is documented in `samples/README.md`. All parsers are `schema_unverified` until real hub captures are saved there.
