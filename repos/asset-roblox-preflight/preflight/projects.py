"""Project registry, per-project configuration layering and per-project storage scopes.

A tool that reads or writes project data refuses without a resolved project (it never guesses). Storage for feedback, corrections, library
entries, overrides, saved reports and generated scripts lives under <workspace>/projects/<project_id>/ (or .../global/ when the user marks a record
global). The shared core is used unchanged: it just receives a Project whose workspace is that scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .guide_adapter import Project

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
GLOBAL = "global"


@dataclass(frozen=True)
class ScopedProject(Project):
    """A Project whose private workspace is one project's (or the global) folder."""

    scope: str = GLOBAL

    @property
    def workspace(self) -> Path:
        return super().workspace / "projects" / self.scope


def load_registry(root: Path) -> dict[str, dict]:
    f = Path(root) / "projects.yaml"
    if not f.exists():
        raise ValueError(f"projects.yaml is missing at {f}: register your projects there (id, place_id, universe, profile)")
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for p in data.get("projects", []):
        pid = p.get("id")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ValueError(f"projects.yaml: bad project id {pid!r} (lowercase letters, digits, hyphens; 3-40 chars)")
        if pid == GLOBAL or pid in out:
            raise ValueError(f"projects.yaml: project id '{pid}' is reserved or duplicated")
        for key in ("alias", "place_id", "universe", "profile"):
            if key not in p:
                raise ValueError(f"projects.yaml: project '{pid}' needs '{key}'")
        out[pid] = p
    return out


def resolve(root: Path, project_id: str | None, place_id: int | str | None = None) -> dict:
    """Return the registry entry, or raise saying exactly what is missing. Never guesses a project."""
    reg = load_registry(root)
    known = sorted(reg)
    if project_id is None or not str(project_id).strip():
        raise ValueError(f"project_id is required and was not given. Known projects: {known} (see list_projects, or add yours to projects.yaml)")
    if project_id not in reg:
        raise ValueError(f"unknown project_id '{project_id}'. Known projects: {known}. Add it to projects.yaml; the tool will not guess.")
    entry = reg[project_id]
    if place_id is not None and str(place_id) != str(entry["place_id"]):
        raise ValueError(f"place_id {place_id} does not match project '{project_id}', which is registered with place_id {entry['place_id']}. Check the place or update projects.yaml.")
    return entry


def scoped(project: Project, scope: str) -> ScopedProject:
    return ScopedProject(name=project.name, package=project.package, env_prefix=project.env_prefix, root=project.root, domain=project.domain, asset_kinds=project.asset_kinds, scope=scope)
