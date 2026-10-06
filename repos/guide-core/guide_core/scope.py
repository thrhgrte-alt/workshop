"""Scope: which project (and place) a tool call is about.

Every tool that reads or writes project data builds a :class:`Scope` first. An unresolved scope is a refusal
(:class:`ScopeError`), never a guess. This module also holds the path allow-list helpers, the shared
``projects.yaml`` registry loader, the open-Studio match, per-scope private workspaces and the per-project
style overlay.

Shared ``projects.yaml`` format (see docs/api.md)::

    version: 1
    projects:
      - project_id: demo_mine              # required, unique
        alias: Demo Mine                   # human name
        universe_id: 123                   # optional (0/null = unknown or unpublished)
        local_file: games/demo.rbxl        # optional local place file
        profiles: {ruleset: projects/demo_mine/ruleset.yaml, style: projects/demo_mine/style.yaml}
        places:
          - place_id: main                 # registry id (a slug)
            alias: Main mine
            roblox_place_id: 456           # 0/null = unpublished: the Studio name is matched instead
            studio_name: "Demo Mine Main"
            local_file: games/demo-main.rbxl
            profiles: {spec: projects/demo_mine/main/economy.yaml}

Legacy spellings that the three first repositories used are accepted: ``id`` for ``project_id``,
``universe`` for ``universe_id``, ``profile``/``ruleset``/``spec``/``bands`` for entries of ``profiles``, a
numeric ``place_id`` for ``roblox_place_id``.
"""

from __future__ import annotations

import copy
import dataclasses
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from .project import Project

GLOBAL = "_global"
STUDIO_INPUT_SCHEMA_VERIFIED = False  # see match_open_studio
PROJECT_LEVEL = "_project"  # workspace folder of a project-level scope (no place)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PROFILE_KEYS = ("profile", "ruleset", "spec", "bands", "style", "landmarks", "profiles_file")


class PathNotAllowed(PermissionError):
    pass


class ScopeError(ValueError):
    """The project or place could not be resolved. The message says what is missing."""


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


# --- Scope ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Scope:
    """A resolved project (and optionally place). ``is_global`` marks records that apply everywhere."""

    project_id: str
    place_id: str | None = None
    is_global: bool = False

    def __post_init__(self):
        if self.is_global:
            if self.project_id != GLOBAL or self.place_id is not None:
                raise ScopeError("a global scope has project_id '_global' and no place")
            return
        if not isinstance(self.project_id, str) or not _ID_RE.match(self.project_id):
            raise ScopeError(f"project_id {self.project_id!r} is not usable: it must be a non-empty name (letters, digits, . _ -)")
        if self.place_id is not None and (not isinstance(self.place_id, str) or not _ID_RE.match(self.place_id)):
            raise ScopeError(f"place_id {self.place_id!r} is not usable: it must be a registry id (letters, digits, . _ -) or None")

    @classmethod
    def make_global(cls) -> "Scope":
        return cls(GLOBAL, None, True)

    @property
    def key(self) -> str:
        """Stable string for stores: ``_global``, ``project`` or ``project/place``."""
        return GLOBAL if self.is_global else (self.project_id if not self.place_id else f"{self.project_id}/{self.place_id}")

    @property
    def label(self) -> str:
        return "global" if self.is_global else self.key

    def to_dict(self) -> dict:
        return {"project_id": self.project_id, "place_id": self.place_id, "global": self.is_global}

    def covers(self, other: "Scope") -> bool:
        """True if a record scoped ``self`` is visible when working in ``other`` (global covers all; a project covers its places)."""
        if self.is_global:
            return True
        if other.is_global:
            return False
        if self.project_id != other.project_id:
            return False
        return self.place_id is None or self.place_id == other.place_id


def scope_from_dict(d: dict | None) -> Scope | None:
    if not d:
        return None
    if d.get("global"):
        return Scope.make_global()
    if not d.get("project_id"):
        return None
    return Scope(d["project_id"], d.get("place_id"))


def require_scope(project_id: str | None, place_id: str | None = None, *, need_place: bool = False,
                  registry: "ProjectRegistry | None" = None, is_global: bool = False) -> Scope:
    """Build a Scope or refuse. With a ``registry`` the project (and place) must be registered."""
    if is_global:
        return Scope.make_global()
    if not project_id:
        known = f" Known projects: {registry.project_ids()}." if registry else ""
        raise ScopeError("project_id is required: this tool reads or writes project data and will not guess one." + known)
    if need_place and not place_id:
        raise ScopeError(f"place_id is required for project '{project_id}' (this tool is place-specific and will not guess one)")
    if registry is not None:
        return registry.resolve(project_id, place_id)
    return Scope(project_id, place_id or None)


# --- registry --------------------------------------------------------------------------------------------
@dataclass
class PlaceEntry:
    place_id: str
    alias: str = ""
    roblox_place_id: int | None = None
    studio_name: str | None = None
    local_file: str | None = None
    profiles: dict[str, str] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class ProjectEntry:
    project_id: str
    alias: str = ""
    universe_id: int | None = None
    local_file: str | None = None
    profiles: dict[str, str] = field(default_factory=dict)
    places: list[PlaceEntry] = field(default_factory=list)
    synthetic: bool = False
    extra: dict = field(default_factory=dict)

    def place(self, place_id: str) -> PlaceEntry | None:
        return next((p for p in self.places if p.place_id == place_id), None)


def _num(v: Any) -> int | None:
    if v in (None, "", 0, "0"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _profiles(d: dict) -> dict[str, str]:
    out = {k: str(v) for k, v in (d.get("profiles") or {}).items()} if isinstance(d.get("profiles"), dict) else {}
    for k in _PROFILE_KEYS:
        if k != "profiles_file" and isinstance(d.get(k), str):
            out.setdefault(k, d[k])
    return out


class ProjectRegistry:
    """The parsed ``projects.yaml``. Paths in it are relative to the registry file."""

    def __init__(self, projects: list[ProjectEntry], path: Path | None = None):
        ids = [p.project_id for p in projects]
        if len(ids) != len(set(ids)):
            raise ScopeError("projects.yaml: duplicate project_id")
        self.projects = projects
        self.path = path

    # loading
    @classmethod
    def load(cls, path: str | Path) -> "ProjectRegistry":
        path = Path(path)
        if not path.exists():
            raise ScopeError(f"{path} is missing: create it with your projects (project_id, alias, places). Nothing is guessed.")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not isinstance(data.get("projects", []), list):
            raise ScopeError(f"{path}: expected a mapping with a 'projects' list")
        return cls.from_dict(data, path)

    @classmethod
    def from_dict(cls, data: dict, path: Path | None = None) -> "ProjectRegistry":
        projects = []
        for raw in data.get("projects") or []:
            pid = raw.get("project_id") or raw.get("id")
            if not isinstance(pid, str) or not _ID_RE.match(pid):
                raise ScopeError(f"projects.yaml: every project needs a usable project_id (got {pid!r})")
            places = []
            for rp in raw.get("places") or []:
                raw_pid = rp.get("id") if rp.get("id") is not None else rp.get("place_id")
                if isinstance(raw_pid, int) or (isinstance(raw_pid, str) and raw_pid.isdigit() and rp.get("id") is None):
                    slug, rbx = None, _num(raw_pid)  # numeric only: no slug given
                    slug = rp.get("alias_id") or f"p{raw_pid}"
                else:
                    slug, rbx = raw_pid, _num(rp.get("roblox_place_id"))
                    if rp.get("id") is not None and rp.get("place_id") is not None:
                        rbx = _num(rp.get("roblox_place_id")) or _num(rp.get("place_id"))
                if not isinstance(slug, str) or not _ID_RE.match(slug):
                    raise ScopeError(f"project '{pid}': a place needs a usable place_id (got {raw_pid!r})")
                places.append(PlaceEntry(slug, rp.get("alias") or rp.get("name") or slug, rbx, rp.get("studio_name") or rp.get("name"),
                                         rp.get("local_file") or rp.get("file"), _profiles(rp),
                                         {k: v for k, v in rp.items() if k not in {"id", "place_id", "alias", "name", "roblox_place_id", "studio_name", "local_file", "file", "profiles", *_PROFILE_KEYS}}))
            slugs = [p.place_id for p in places]
            if len(slugs) != len(set(slugs)):
                raise ScopeError(f"project '{pid}' has duplicate place ids")
            top_rbx = _num(raw.get("roblox_place_id")) or _num(raw.get("place_id"))
            if not places and top_rbx:  # legacy: one numeric place at project level
                places = [PlaceEntry("main", raw.get("alias") or pid, top_rbx, raw.get("studio_name"))]
            projects.append(ProjectEntry(pid, raw.get("alias") or pid, _num(raw.get("universe_id", raw.get("universe"))), raw.get("local_file"), _profiles(raw), places,
                                         bool(raw.get("synthetic")), {k: v for k, v in raw.items() if k not in {"id", "project_id", "alias", "universe", "universe_id", "local_file", "profiles", "places", "synthetic", "roblox_place_id", "place_id", "studio_name", *_PROFILE_KEYS}}))
        return cls(projects, path)

    # lookup
    def project_ids(self) -> list[str]:
        return [p.project_id for p in self.projects]

    def project(self, project_id: str) -> ProjectEntry:
        for p in self.projects:
            if p.project_id == project_id:
                return p
        near = difflib.get_close_matches(str(project_id), self.project_ids(), n=2, cutoff=0.5)
        raise ScopeError(f"unknown project_id '{project_id}'. Known projects: {self.project_ids()}." + (f" Did you mean {near}?" if near else "") + " Add it to projects.yaml first; the tool will not guess.")

    def resolve(self, project_id: str | None, place_id: str | int | None = None) -> Scope:
        """A Scope for a registered project (and place, by registry id or Roblox place id). Refuses otherwise."""
        if not project_id:
            raise ScopeError(f"project_id is required (this tool will not guess one). Known projects: {self.project_ids()}")
        entry = self.project(project_id)
        if place_id in (None, ""):
            return Scope(entry.project_id)
        hit = next((p for p in entry.places if p.place_id == str(place_id) or (p.roblox_place_id and str(p.roblox_place_id) == str(place_id))), None)
        if hit is None:
            raise ScopeError(f"place '{place_id}' does not belong to project '{project_id}'. Its places: {[p.place_id for p in entry.places]}")
        return Scope(entry.project_id, hit.place_id)

    def place(self, scope: Scope) -> PlaceEntry:
        if scope.is_global or not scope.place_id:
            raise ScopeError("this needs a place: give place_id")
        hit = self.project(scope.project_id).place(scope.place_id)
        if hit is None:
            raise ScopeError(f"place '{scope.place_id}' does not belong to project '{scope.project_id}'")
        return hit

    def profile_path(self, scope: Scope, key: str) -> Path | None:
        """Absolute path of a profile file for the place (if it names one) else the project; relative to the registry file."""
        base = self.path.parent if self.path else Path(".")
        entry = self.project(scope.project_id)
        rel = None
        if scope.place_id:
            pl = entry.place(scope.place_id)
            rel = pl.profiles.get(key) if pl else None
        rel = rel or entry.profiles.get(key)
        return None if rel is None else (Path(rel) if Path(rel).is_absolute() else base / rel)


def load_registry(path: str | Path) -> ProjectRegistry:
    return ProjectRegistry.load(path)


# --- open Studio match -----------------------------------------------------------------------------------
def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def match_open_studio(place: PlaceEntry, studios: Any, *, label: str = "") -> dict:
    """Pick the open Studio instance that IS this registry place; refuse if none (or more than one) matches.

    ``studios`` is DATA the caller got from the hub's ``list_roblox_studios`` (this package never calls it): a list of
    mappings with ``name|placeName|title``, ``place_id|placeId|PlaceId`` and ``studio_id|studioId|id``. A place matches by
    Roblox place id when it has one, otherwise (or additionally) by exact normalised name.

    ``STUDIO_INPUT_SCHEMA_VERIFIED`` is False: those key names are the spellings the first repositories handled, and no real ``list_roblox_studios`` capture was available to check them
    against. The result carries ``"input_schema": "schema_unverified"`` until that changes.
    """
    label = label or place.place_id
    if not isinstance(studios, list) or not studios:
        raise ScopeError(f"cannot confirm the open Studio place: pass the output of the hub's list_roblox_studios as a list of "
                         f"{{name, place_id, studio_id}} and open place '{label}' ({place.studio_name or place.roblox_place_id}) in Studio. Nothing was generated.")
    rows = []
    for s in studios:
        if not isinstance(s, dict):
            raise ScopeError("each studios entry must be a mapping like {name, place_id, studio_id}")
        rows.append({"name": s.get("name") or s.get("placeName") or s.get("title"), "place_id": s.get("place_id", s.get("placeId", s.get("PlaceId"))),
                     "studio_id": s.get("studio_id") or s.get("studioId") or s.get("id")})
    hits = []
    for r in rows:
        by_id = bool(place.roblox_place_id) and str(r["place_id"]) == str(place.roblox_place_id)
        by_name = bool(place.studio_name) and _norm(r["name"]) == _norm(place.studio_name)
        if by_id or by_name:
            hits.append({**r, "matched_by": "place_id" if by_id else "name", "input_schema": "schema_unverified"})
    if not hits:
        open_ = [f"{r['name']!r} (place_id {r['place_id']})" for r in rows]
        raise ScopeError(f"refusing: the open Studio instance(s) {open_} are not place '{label}' "
                         f"(expected name {place.studio_name!r} or place id {place.roblox_place_id}). Open the right place; nothing was generated.")
    if len(hits) > 1:
        raise ScopeError(f"refusing: {len(hits)} open Studio instances match place '{label}'; close the extra ones so the target is unambiguous.")
    return hits[0]


# --- per-scope workspace ---------------------------------------------------------------------------------
class ScopedProject(Project):
    """A Project whose private workspace is one scope's folder: ``workspace/projects/<project>/<place or _project>/``."""

    @property
    def workspace(self) -> Path:
        return Project.workspace.fget(self) / "projects" / self.scope_project / self.scope_place  # type: ignore[attr-defined]


def scoped_project(project: Project, project_id: str, place_id: str) -> Project:
    """``project`` re-rooted at one (project_id, place_id) workspace. ``'_global'`` names the shared pseudo scope."""
    sp = ScopedProject(**{f.name: getattr(project, f.name) for f in dataclasses.fields(Project)})
    keep = lambda v: v if v in (GLOBAL, PROJECT_LEVEL) else safe_name(v)  # noqa: E731  (these start with an underscore that safe_name would strip)
    object.__setattr__(sp, "scope_project", keep(project_id))
    object.__setattr__(sp, "scope_place", keep(place_id))
    return sp


def project_for_scope(project: Project, scope: Scope) -> Project:
    """Convenience over :func:`scoped_project` for a :class:`Scope` (a project-level scope uses the place folder ``_project``)."""
    if scope.is_global:
        return scoped_project(project, GLOBAL, GLOBAL)
    return scoped_project(project, scope.project_id, scope.place_id or PROJECT_LEVEL)


# --- style overlay ---------------------------------------------------------------------------------------
def overlay_style(base: dict, override: dict | None) -> dict:
    """Layer a project's style on the global one. Mappings merge key by key, constraints merge by id (the override wins),
    exclusions are unioned. The result records which layer set each top-level key in ``_layers``."""
    out = copy.deepcopy(base)
    layers = {k: "global" for k in out}
    for key, val in (override or {}).items():
        if key == "constraints":
            by_id = {c["id"]: c for c in out.get("constraints", [])}
            order = [c["id"] for c in out.get("constraints", [])]
            for c in val or []:
                if c["id"] not in by_id:
                    order.append(c["id"])
                by_id[c["id"]] = c
            out["constraints"] = [by_id[i] for i in order]
        elif key == "exclusions":
            out["exclusions"] = list(dict.fromkeys([*out.get("exclusions", []), *(val or [])]))
        elif isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **copy.deepcopy(val)}
        else:
            out[key] = copy.deepcopy(val)
        layers[key] = "project"
    out["_layers"] = layers
    return out


def load_layered_style(project: Project, scope: Scope | None = None, *, style_file_name: str = "style.yaml") -> dict:
    """Global ``style/style.yaml`` plus ``projects/<project_id>/style.yaml`` (and ``projects/<project_id>/<place_id>/style.yaml``) if present."""
    from .style import load_style, validate_style

    style = load_style(project)
    if scope is None or scope.is_global:
        return style
    layers = [project.root / "projects" / safe_name(scope.project_id) / style_file_name]
    if scope.place_id:
        layers.append(project.root / "projects" / safe_name(scope.project_id) / safe_name(scope.place_id) / style_file_name)
    for f in layers:
        if f.exists():
            style = overlay_style(style, yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    problems = validate_style({k: v for k, v in style.items() if k != "_layers"})
    if problems:
        raise ValueError(f"layered style for {scope.label}: " + "; ".join(problems))
    return style


def known_scopes(registry: ProjectRegistry) -> Iterable[Scope]:
    for p in registry.projects:
        yield Scope(p.project_id)
        for pl in p.places:
            yield Scope(p.project_id, pl.place_id)
