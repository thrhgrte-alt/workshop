"""Projects and places: the registry (projects.yaml), scope resolution and the layered ruleset.

Nothing is guessed: a tool that reads or writes project data must be given a project_id that is in the registry (and, when given, a place_id
that belongs to that project). Otherwise it refuses and says what is missing.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..guide_adapter import Project

GLOBAL_LAYER = "global"


@dataclass
class Scope:
    project_id: str
    alias: str
    place_id: str | None  # the place's registry id, or None when no place was named
    places: list[dict] = field(default_factory=list)
    ruleset: str = ""
    universe_id: int | None = None
    synthetic: bool = False

    def label(self) -> str:
        return f"{self.project_id}" + (f"/{self.place_id}" if self.place_id else "")

    def to_dict(self) -> dict:
        place = next((p for p in self.places if p["id"] == self.place_id), None)
        return {"project_id": self.project_id, "alias": self.alias, "place_id": self.place_id, "place_roblox_id": place.get("place_id") if place else None,
                "synthetic_example": self.synthetic}


def load_registry(root: Path) -> dict:
    f = Path(root) / "projects.yaml"
    if not f.exists():
        raise ValueError(f"{f} is missing: create it with your projects (id, alias, place ids, ruleset path). See the synthetic examples in the repository")
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    seen = set()
    for p in data.get("projects", []):
        if not p.get("id") or p["id"] in seen:
            raise ValueError(f"{f}: every project needs a unique id")
        seen.add(p["id"])
        pl = [x.get("id") for x in p.get("places", [])]
        if len(pl) != len(set(pl)) or not all(pl):
            raise ValueError(f"{f}: project '{p['id']}' has missing or duplicate place ids")
    return data


def resolve_scope(project: Project, project_id: str | None, place_id: str | int | None = None) -> Scope:
    reg = load_registry(project.root)
    known = [p["id"] for p in reg.get("projects", [])]
    if not project_id:
        raise ValueError(f"project_id is required (this tool reads or writes project data and will not guess one). Known projects: {known}. Add yours to projects.yaml")
    entry = next((p for p in reg["projects"] if p["id"] == project_id), None)
    if entry is None:
        near = difflib.get_close_matches(project_id, known, n=2, cutoff=0.5)
        raise ValueError(f"unknown project_id '{project_id}'. Known projects: {known}." + (f" Did you mean {near}?" if near else "") + " Add it to projects.yaml first")
    places = entry.get("places", [])
    pid = None
    if place_id not in (None, ""):
        hit = next((p for p in places if p["id"] == str(place_id) or (p.get("place_id") is not None and str(p["place_id"]) == str(place_id))), None)
        if hit is None:
            raise ValueError(f"place '{place_id}' does not belong to project '{project_id}'. Its places: {[p['id'] for p in places]}")
        pid = hit["id"]
    return Scope(project_id, entry.get("alias", project_id), pid, places, entry.get("ruleset") or f"projects/{project_id}/ruleset.yaml", entry.get("universe_id"),
                 bool(entry.get("synthetic")))


def place_for_file(scope: Scope, relpath: str) -> str | None:
    """The place a reviewed file belongs to: the named place_id if one was given, else the registry place whose `path` prefixes the file's path."""
    if scope.place_id:
        return scope.place_id
    rel = relpath.replace("\\", "/").lstrip("./")
    best = None
    for p in scope.places:
        prefix = (p.get("path") or "").strip("/")
        if prefix and (rel == prefix or rel.startswith(prefix + "/") or f"/{prefix}/" in "/" + rel):
            if best is None or len(prefix) > len(best[1]):
                best = (p["id"], prefix)
    return best[0] if best else None


def global_ruleset_path(project: Project) -> Path:
    return project.root / "projects" / "_global" / "ruleset.yaml"


def project_ruleset_path(project: Project, scope: Scope) -> Path:
    return project.root / scope.ruleset


def place_ruleset_path(project: Project, scope: Scope, place: str | None) -> Path | None:
    if not place:
        return None
    entry = next((p for p in scope.places if p["id"] == place), {})
    return project.root / entry["ruleset"] if entry.get("ruleset") else project.root / "projects" / scope.project_id / "places" / f"{place}.ruleset.yaml"


def layer_paths(project: Project, scope: Scope, place: str | None) -> list[tuple[str, Path]]:
    out = [(GLOBAL_LAYER, global_ruleset_path(project)), ("project", project_ruleset_path(project, scope))]
    pp = place_ruleset_path(project, scope, place)
    if pp is not None:
        out.append(("place", pp))
    return out


def write_roots(project: Project) -> list[Path]:
    """Where project data may be WRITTEN: the private workspace plus this repository's projects/ folder (and LUAUREV_ALLOWED_PATHS)."""
    return [*project.allowed_roots, (project.root / "projects").resolve()]


def fp_store_path(project: Project, scope: Scope | None, global_: bool = False) -> Path:
    if global_ or scope is None:
        return project.feedback_dir / "global" / "false_positives.jsonl"
    return project.feedback_dir / "projects" / scope.project_id / "false_positives.jsonl"
