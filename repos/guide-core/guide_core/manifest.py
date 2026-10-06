"""Reference-asset manifest: schema, validation, and a small JSONL store.

The manifest records *metadata and provenance*. It never uploads or copies the
asset itself. Large binaries stay wherever the user keeps them (a local
folder, Git LFS, or an object store) and are referenced by ``path``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .project import Project

ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"
STATUSES = ("candidate", "curated", "rejected")
POLARITIES = ("positive", "negative")
OWNERS = ("user", "user-approved", "third-party", "unknown")
DETAIL_LEVELS = ("low", "medium", "high")


def base_schema(kinds: Iterable[str], domain_schema: dict | None = None) -> dict:
    """JSON Schema for one manifest entry. ``domain`` holds repo-specific fields."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Reference asset manifest entry",
        "type": "object",
        "required": ["id", "kind", "title", "path", "license", "status"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "pattern": ID_PATTERN},
            "kind": {"enum": list(kinds)},
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "path": {"type": "string", "minLength": 1,
                     "description": "Local path (absolute, or relative to <PREFIX>_ASSET_ROOT / repo) or remote URI."},
            "preview": {"type": ["string", "null"]},
            "source_file": {"type": ["string", "null"],
                            "description": "Related scene, graph, script or project file."},
            "application": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
            },
            "project": {"type": "string"},
            "license": {
                "type": "object",
                "required": ["owner"],
                "additionalProperties": False,
                "properties": {
                    "owner": {"enum": list(OWNERS)},
                    "spdx": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "provenance": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "creator": {"type": "string"},
                    "source_url": {"type": "string"},
                    "added_at": {"type": "string"},
                    "origin": {"enum": ["user-made", "user-supplied", "ai-generated", "third-party", "synthetic-example"]},
                    "run_id": {"type": "string"},
                },
            },
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "style": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "traits": {"type": "array", "items": {"type": "string"}},
                    "palette": {"type": "array", "items": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}},
                    "shape_language": {"type": "string"},
                    "detail_density": {"enum": list(DETAIL_LEVELS)},
                    "composition": {"type": "string"},
                    "intended_use": {"type": "string"},
                },
            },
            "rating": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
            "status": {"enum": list(STATUSES)},
            "polarity": {"enum": list(POLARITIES), "default": "positive"},
            "correction_notes": {"type": "array", "items": {"type": "string"}},
            "related": {"type": "array", "items": {"type": "string"}},
            "embedding": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"provider": {"type": "string"}, "model": {"type": "string"}},
            },
            "domain": domain_schema or {"type": "object"},
        },
    }


def load_schema(project: Project) -> dict:
    return json.loads(project.schema_file.read_text(encoding="utf-8"))


def validate_asset(asset: dict, schema: dict) -> list[str]:
    """Schema errors plus the semantic rules a schema cannot express."""
    validator = Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(asset), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors
    if asset["status"] == "curated":
        owner = asset["license"]["owner"]
        if owner in ("unknown",):
            errors.append("license.owner: curated assets need a known owner (user, user-approved or third-party)")
        if not asset.get("description"):
            errors.append("description: curated assets need a plain-language description")
        if asset.get("polarity", "positive") == "positive" and not asset.get("tags"):
            errors.append("tags: curated positive examples need at least one tag")
    if asset.get("polarity") == "negative" and not (asset.get("correction_notes") or asset.get("description")):
        errors.append("description: negative examples must explain what to avoid")
    return errors


def slugify(text: str, maxlen: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:maxlen].strip("-") or "asset"


def new_asset_id(title: str, salt: str = "") -> str:
    digest = hashlib.sha1((title + salt).encode("utf-8")).hexdigest()[:6]
    return f"{slugify(title, 40)}-{digest}"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: invalid JSON ({exc.msg})") from exc
    return rows


def write_jsonl_atomic(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class LibraryStore:
    """Tracked synthetic examples + the user's private local library."""

    def __init__(self, project: Project, include_examples: bool = True):
        self.project = project
        self.include_examples = include_examples

    def load(self) -> list[dict]:
        rows: dict[str, dict] = {}
        if self.include_examples:
            for row in read_jsonl(self.project.examples_library_file):
                rows[row["id"]] = {**row, "_origin": "examples"}
        for row in read_jsonl(self.project.library_file):
            rows[row["id"]] = {**row, "_origin": "local"}
        return list(rows.values())

    def get(self, asset_id: str) -> dict | None:
        return next((a for a in self.load() if a["id"] == asset_id), None)

    def resolve_path(self, value: str | None) -> Path | None:
        """Resolve a manifest path; returns None for remote URIs or empty values."""
        if not value or re.match(r"^[a-z][a-z0-9+.-]*://", value):
            return None
        p = Path(value).expanduser()
        if p.is_absolute():
            return p
        for base in (self.project.asset_root, self.project.root):
            if base and (base / p).exists():
                return base / p
        return (self.project.asset_root or self.project.root) / p

    def upsert(self, asset: dict, *, schema: dict | None = None) -> dict:
        """Validate and write to the *local* library (never to tracked examples)."""
        schema = schema or load_schema(self.project)
        clean = {k: v for k, v in asset.items() if not k.startswith("_")}
        errors = validate_asset(clean, schema)
        if errors:
            raise ValueError("invalid asset: " + "; ".join(errors))
        rows = [r for r in read_jsonl(self.project.library_file) if r["id"] != clean["id"]]
        rows.append(clean)
        write_jsonl_atomic(self.project.library_file, sorted(rows, key=lambda r: r["id"]))
        return clean

    def validate_all(self, check_files: bool = False) -> dict:
        schema = load_schema(self.project)
        problems: dict[str, list[str]] = {}
        seen: set[str] = set()
        rows = self.load()
        for row in rows:
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            errs = validate_asset(clean, schema)
            if clean.get("id") in seen:
                errs.append("id: duplicate id")
            seen.add(clean.get("id", ""))
            if check_files and not errs:
                for key in ("path", "preview", "source_file"):
                    resolved = self.resolve_path(clean.get(key))
                    if clean.get(key) and resolved is not None and not resolved.exists():
                        errs.append(f"{key}: file not found: {resolved}")
            if errs:
                problems[clean.get("id", "<no id>")] = errs
        return {"checked": len(rows), "problems": problems, "ok": not problems}

    def ingest_dir(
        self,
        directory: Path,
        *,
        kind: str,
        extensions: Iterable[str],
        owner: str = "unknown",
        application: dict | None = None,
        project_name: str | None = None,
        tags: Iterable[str] = (),
        dry_run: bool = True,
        preview_exts: Iterable[str] = (".png", ".jpg", ".jpeg", ".webp"),
    ) -> list[dict]:
        """Create *candidate* records for files in a folder. Nothing is copied or uploaded.

        Candidates are not returned by default searches until curated, so a
        bulk ingest cannot silently pollute the style library.
        """
        exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in extensions}
        records = []
        for file in sorted(Path(directory).expanduser().rglob("*")):
            if not file.is_file() or file.suffix.lower() not in exts:
                continue
            preview = next((str(file.with_suffix(e)) for e in preview_exts if file.with_suffix(e).exists()), None)
            rec: dict[str, Any] = {
                "id": new_asset_id(file.stem, str(file)),
                "kind": kind,
                "title": file.stem.replace("_", " ").replace("-", " "),
                "path": str(file),
                "preview": preview,
                "license": {"owner": owner},
                "status": "candidate",
                "polarity": "positive",
                "tags": sorted(set(tags)),
                "provenance": {"origin": "user-supplied"},
            }
            if application:
                rec["application"] = application
            if project_name:
                rec["project"] = project_name
            records.append(rec)
        if not dry_run:
            for rec in records:
                self.upsert(rec)
        return records
