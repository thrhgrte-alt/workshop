"""Dry-run pattern: a write function returns a :class:`Plan`; applying needs an explicit flag and records what changed.

::

    plan = Plan("rename 2 parts").add("rename", "Workspace.A", "-> B")
    result = run_plan(plan, applier, apply=False)          # {"dry_run": True, ...}: nothing happened
    result = run_plan(plan, applier, apply=True, journal=path)   # applier runs, the journal records plan + outcome

``Plan`` and ``Versioner`` are unchanged from the vendored kit (three repositories depend on them).
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .project import Project
from .scope import safe_name


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

    def fingerprint(self) -> str:
        """Stable id of the planned changes, so an apply can prove it applies the plan that was reviewed."""
        blob = json.dumps({"summary": self.summary, "steps": self.steps}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


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


def text_diff(before: str, after: str, name: str = "file") -> str:
    """Unified diff text (for showing a reviewer what a plan would change)."""
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{name}", f"b/{name}"))


def run_plan(plan: Plan, applier: Callable[[Plan], Any] | None = None, *, apply: bool = False,
             journal: Path | None = None, expect: str | None = None) -> dict:
    """Show or apply a plan.

    * ``apply=False`` (the default): returns ``plan.to_dict(dry_run=True)``; ``applier`` is NOT called and nothing is written.
    * ``apply=True``: needs an ``applier``; ``expect`` (a :meth:`Plan.fingerprint` the reviewer saw) must match if given.
      The applier's return value is recorded as ``changed``; with a ``journal`` path a JSONL row is appended with the plan,
      its fingerprint, the time and the outcome (also when the applier raises: ``error`` is recorded, then it is re-raised).
    """
    if not apply:
        return {**plan.to_dict(dry_run=True), "fingerprint": plan.fingerprint()}
    if applier is None:
        raise ValueError("apply=True needs an applier function")
    if expect is not None and expect != plan.fingerprint():
        raise ValueError(f"the plan changed since it was reviewed (expected {expect}, now {plan.fingerprint()}); show it again")
    row: dict = {"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "summary": plan.summary,
                 "fingerprint": plan.fingerprint(), "steps": plan.steps}
    try:
        changed = applier(plan)
        row["changed"] = changed if changed is not None else [s["target"] for s in plan.steps]
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        _journal(journal, row)
        raise
    _journal(journal, row)
    return {**plan.to_dict(dry_run=False), "fingerprint": row["fingerprint"], "changed": row["changed"], "journal": str(journal) if journal else None}


def _journal(path: Path | None, row: dict) -> None:
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) + "\n")
