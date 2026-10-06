"""Safe-by-default file and change handling: path allowlists, versions, plans."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project


class PathNotAllowed(PermissionError):
    pass


def resolve_inside(path: str | Path, roots: list[Path]) -> Path:
    """Resolve ``path`` and require it to sit inside one of ``roots`` (no traversal, no escaping symlinks)."""
    resolved = Path(path).expanduser().resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    allowed = ", ".join(str(r) for r in roots)
    raise PathNotAllowed(f"'{path}' is outside the allowed directories ({allowed}). "
                         f"Set <PREFIX>_ALLOWED_PATHS to widen this deliberately.")


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not cleaned:
        raise ValueError(f"'{name}' is not a usable name")
    return cleaned[:80]


@dataclass
class Plan:
    """A reviewable list of changes. Tools return a Plan when ``dry_run`` is true."""

    summary: str
    steps: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, op: str, target: str, detail: str = "") -> "Plan":
        self.steps.append({"op": op, "target": target, "detail": detail})
        return self

    def to_dict(self, dry_run: bool = True) -> dict:
        return {"dry_run": dry_run, "summary": self.summary, "steps": self.steps, "warnings": self.warnings}


class Versioner:
    """Copy-on-write checkpoints so any saved output can be restored."""

    def __init__(self, project: Project):
        self.project = project

    def _dir(self, name: str) -> Path:
        return self.project.versions_dir / safe_name(name)

    def save(self, name: str, content: str | bytes, *, suffix: str = ".json", label: str = "") -> dict:
        d = self._dir(name)
        d.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        digest = hashlib.sha256(data).hexdigest()[:12]
        index = self._index(name)
        if index and index[-1]["sha"] == digest:
            return {**index[-1], "unchanged": True}
        number = len(index) + 1
        file = d / f"v{number:04d}-{digest}{suffix}"
        file.write_bytes(data)
        entry = {"version": number, "file": str(file), "sha": digest, "label": label,
                 "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
        index.append(entry)
        (d / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        return {**entry, "unchanged": False}

    def _index(self, name: str) -> list[dict]:
        f = self._dir(name) / "index.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def history(self, name: str) -> list[dict]:
        return self._index(name)

    def load(self, name: str, version: int | None = None) -> str:
        index = self._index(name)
        if not index:
            raise FileNotFoundError(f"no versions saved for '{name}'")
        entry = index[-1] if version is None else next((e for e in index if e["version"] == version), None)
        if entry is None:
            raise FileNotFoundError(f"'{name}' has no version {version}")
        return Path(entry["file"]).read_text(encoding="utf-8")

    def restore(self, name: str, version: int, dest: Path) -> Path:
        index = self._index(name)
        entry = next((e for e in index if e["version"] == version), None)
        if entry is None:
            raise FileNotFoundError(f"'{name}' has no version {version}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(entry["file"], dest)
        return dest
