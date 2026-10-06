"""Snapshot schema ``placemap-snapshot/1``, an indexed read view of one, and the per-place snapshot store (latest plus a few older ones, the rest prunable).

A snapshot is plain JSON (no source code is stored, only a content hash and the analysis of each script, so it stays small and secret-free)::

    {"schema": "placemap-snapshot/1", "snapshot_id": "snap-20261006T101500-ab12cd", "place": {...}, "taken_at": ISO, "taken_at_source": "collector"|"ingest",
     "ingested_at": ISO, "source": {"kind": ..., "parser_status": "schema_unverified", ...}, "full": true, "roots": [...], "truncated": false, "skipped": {...},
     "instances": [{"path": "ServerScriptService/Vendors/Shop", "class": "Script", "attrs": {...}, "tags": [...], "pos": [x, y, z] | null, "n_children": 0}, ...],
     "scripts":   [{"path", "class", "kind": server|client|module, "hash", "fp", "length", "source_cut", "needs_read", <analysis fields>}, ...], "stats": {...}}

Paths are the instance names joined with ``/`` below the data model (``ServerScriptService/Vendors/Shop``); ``/`` and ``%`` inside a name are percent-escaped.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from .util import SEP, age_hours, is_under, parent_path

SCHEMA = "placemap-snapshot/1"
KEEP_FIELDS = ("requires", "remotes_fired", "remotes_handled", "datastores", "datastore_keys", "services", "functions", "name_refs", "string_tokens", "first_comment",
               "secret_like_strings_skipped", "summary", "lines")
REMOTE_CLASSES = {"RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent", "BindableEvent", "BindableFunction"}
SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}


class Snapshot:
    """Read view over snapshot data with the lookups every tool needs (built once, lazily)."""

    def __init__(self, data: dict):
        self.data = data
        self.instances: list[dict] = data["instances"]
        self.by_path: dict[str, dict] = {i["path"]: i for i in self.instances}
        self.scripts: dict[str, dict] = {s["path"]: s for s in data.get("scripts", [])}
        self._children: dict[str, list[str]] | None = None
        self._subclasses: dict[str, frozenset] | None = None

    # identity
    @property
    def id(self) -> str:
        return self.data["snapshot_id"]

    @property
    def taken_at(self) -> str:
        return self.data["taken_at"]

    def children_of(self, path: str) -> list[str]:
        if self._children is None:
            ch: dict[str, list[str]] = {}
            for p in sorted(self.by_path):
                ch.setdefault(parent_path(p), []).append(p)
            self._children = ch
        return self._children.get(path, [])

    def descendants(self, path: str) -> Iterator[str]:
        stack = list(reversed(self.children_of(path)))
        while stack:
            p = stack.pop()
            yield p
            stack.extend(reversed(self.children_of(p)))

    def subtree_classes(self, path: str) -> frozenset:
        """Classes of the instance and all its descendants."""
        if self._subclasses is None:
            memo: dict[str, set] = {}
            for p in sorted(self.by_path, key=lambda q: -q.count(SEP)):
                s = memo.setdefault(p, set())
                s.add(self.by_path[p]["class"])
                par = parent_path(p)
                if par in self.by_path or par == "":
                    memo.setdefault(par, set()).update(s)
            self._subclasses = {k: frozenset(v) for k, v in memo.items()}
        return self._subclasses.get(path, frozenset())

    def descendant_classes(self, path: str) -> frozenset:
        out: set = set()
        for c in self.children_of(path):
            out |= self.subtree_classes(c)
        return frozenset(out)

    def scripts_under(self, path: str) -> list[dict]:
        return [s for p, s in self.scripts.items() if is_under(p, path)]

    def remotes(self) -> dict[str, dict]:
        return {p: i for p, i in self.by_path.items() if i["class"] in REMOTE_CLASSES}


def new_id(taken_at: str, digest: str) -> str:
    return f"snap-{taken_at.replace('-', '').replace(':', '').replace('+0000', '')[:15]}-{digest[:6]}"


def content_digest(instances: list[dict], scripts: list[dict]) -> str:
    h = hashlib.sha256()
    for i in instances:
        h.update(json.dumps(i, sort_keys=True, separators=(",", ":")).encode())
    for s in scripts:
        h.update(json.dumps({k: s.get(k) for k in ("path", "class", "hash", "fp")}, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()


def staleness(taken_at: str, stale_hours: float, now=None) -> dict:
    age = age_hours(taken_at, now)
    return {"taken_at": taken_at, "age_hours": round(age, 1), "stale": age > stale_hours, "stale_after_hours": stale_hours}


class SnapshotStore:
    def __init__(self, wproject):
        self.dir = Path(wproject.workspace) / "snapshots"

    def _files(self) -> list[Path]:
        return sorted(self.dir.glob("snap-*.json")) if self.dir.is_dir() else []

    def list(self) -> list[dict]:
        rows = []
        for f in self._files():
            try:
                head = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append({"id": head["snapshot_id"], "taken_at": head["taken_at"], "ingested_at": head.get("ingested_at"), "full": head.get("full", True),
                         "instances": len(head["instances"]), "scripts": len(head.get("scripts", [])), "file": str(f)})
        return sorted(rows, key=lambda r: (r["taken_at"], r["id"]), reverse=True)

    def load(self, ref: str = "latest") -> Snapshot:
        rows = self.list()
        if not rows:
            raise FileNotFoundError("no snapshot has been stored for this place yet: run plan_refresh, let the hub run the Luau, then ingest_snapshot (dry_run=false)")
        if ref in ("latest", "", None):
            row = rows[0]
        elif ref == "previous":
            if len(rows) < 2:
                raise FileNotFoundError("there is only one snapshot for this place, so there is no previous one")
            row = rows[1]
        else:
            row = next((r for r in rows if r["id"] == ref or r["id"].startswith(ref)), None)
            if row is None:
                raise FileNotFoundError(f"unknown snapshot '{ref}'. Stored: {[r['id'] for r in rows][:8]}")
        return _load_cached(row["file"], os.stat(row["file"]).st_mtime_ns)

    def save(self, data: dict) -> tuple[Path, bool]:
        """Write the snapshot (atomic). Returns (path, already_existed). The same content gives the same id, so saving it twice changes nothing."""
        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.dir / f"{data['snapshot_id']}.json"
        if target.exists():
            return target, True
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".snap-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return target, False

    def prune_candidates(self, keep_older: int) -> list[dict]:
        rows = self.list()
        return rows[1 + max(0, keep_older):]

    def delete(self, snapshot_id: str) -> None:
        f = self.dir / f"{snapshot_id}.json"
        if f.parent.resolve() != self.dir.resolve() or not f.is_file():
            raise FileNotFoundError(snapshot_id)
        f.unlink()


@lru_cache(maxsize=16)
def _load_cached(file: str, mtime_ns: int) -> Snapshot:
    return Snapshot(json.loads(Path(file).read_text(encoding="utf-8")))
