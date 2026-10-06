"""The place registry: projects.yaml (shared guide-core format) plus an optional places.yaml of the same shape, and everything that needs a RESOLVED place.

Every tool calls :func:`resolve` first. It refuses (never guesses) when the project or place is missing or unknown, and returns a :class:`PlaceCtx` that names the project, the place, the
stable id (the Roblox place id, or ``local-<alias>`` until the place is published) and the private workspace folder of that one place. A tool that needs Studio then calls
:func:`match_open` with the hub's ``list_roblox_studios`` output, which refuses when the open place is not the named one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..guide_adapter import Project, scope as S
from .util import PLACE_SEP, is_place_id


def registry_path(project: Project) -> Path:
    env = project.env("PROJECTS")
    return Path(env).expanduser() if env else project.root / "projects.yaml"


def places_path(project: Project) -> Path:
    env = project.env("PLACES")
    return Path(env).expanduser() if env else registry_path(project).parent / "places.yaml"


def load_registry(project: Project) -> S.ProjectRegistry:
    """projects.yaml, with places.yaml (if present) merged in: same format; projects with the same id have their places combined."""
    path = registry_path(project)
    if not path.exists():
        raise S.ScopeError(f"{path} is missing: create it with your projects and places (alias, place id, universe, local file). Nothing is guessed.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    extra_path = places_path(project)
    if extra_path.exists() and extra_path.resolve() != path.resolve():
        extra = yaml.safe_load(extra_path.read_text(encoding="utf-8")) or {}
        by_id = {(p.get("project_id") or p.get("id")): p for p in data.get("projects") or []}
        for p in extra.get("projects") or []:
            pid = p.get("project_id") or p.get("id")
            if pid in by_id:
                by_id[pid].setdefault("places", []).extend(p.get("places") or [])
            else:
                data.setdefault("projects", []).append(p)
    return S.ProjectRegistry.from_dict(data, path)


@dataclass
class PlaceCtx:
    project: Project
    registry: S.ProjectRegistry
    scope: S.Scope
    project_entry: S.ProjectEntry
    place_entry: S.PlaceEntry
    active_by: str = "named"  # named | open_studio

    @property
    def project_id(self) -> str:
        return self.scope.project_id

    @property
    def place_id(self) -> str:
        return self.scope.place_id or ""

    @property
    def stable_id(self) -> str:
        return str(self.place_entry.roblox_place_id) if self.place_entry.roblox_place_id else f"local-{self.place_id}"

    @property
    def synthetic(self) -> bool:
        return bool(self.project_entry.synthetic)

    @property
    def wproject(self) -> Project:
        """The project re-rooted at this place's private workspace: workspace/projects/<project_id>/<place_id>/."""
        return S.scoped_project(self.project, self.project_id, self.place_id)

    @property
    def universe(self) -> int | None:
        return self.project_entry.universe_id

    def head(self) -> dict:
        d = {"project_id": self.project_id, "place_id": self.place_id}
        if self.synthetic:
            d["synthetic_place"] = True
        return d

    def prefixed(self, path: str) -> str:
        return f"{self.place_id}{PLACE_SEP}{path}"

    def landmarks_file(self) -> Path | None:
        return self.registry.profile_path(self.scope, "landmarks")

    def local_file(self) -> str | None:
        return self.place_entry.local_file or self.project_entry.local_file


def _check_id(kind: str, value: object) -> None:
    if value is not None and not is_place_id(value):
        raise S.ScopeError(f"{kind} {value!r} is not a valid registry id (letters, digits, '.', '_', '-'; no whitespace or newline)")


def resolve(project: Project, project_id: str | None, place_id: str | None, *, studios: object = None, registry: S.ProjectRegistry | None = None) -> PlaceCtx:
    """Resolve project and place or refuse. Without ``place_id`` the active place may be taken from ``studios`` (the hub's list_roblox_studios output): the ONE registered place of the
    project that is open in Studio; zero or several matches refuse and say which places exist."""
    _check_id("project_id", project_id)
    _check_id("place_id", place_id)
    reg = registry or load_registry(project)
    if not project_id:
        raise S.ScopeError(f"project_id is required: this tool reads project data and will not guess one. Known projects: {reg.project_ids()}")
    entry = reg.project(project_id)
    by = "named"
    if not place_id:
        if not studios:
            raise S.ScopeError(f"place_id is required for project '{project_id}' (places: {[p.place_id for p in entry.places]}). The tool will not guess the place; "
                               f"pass place_id, or pass studios (the output of the hub's list_roblox_studios) to use the place that is open in Studio.")
        hits = []
        for pl in entry.places:
            try:
                S.match_open_studio(pl, studios, label=pl.place_id)
                hits.append(pl)
            except S.ScopeError:
                continue
        if len(hits) != 1:
            raise S.ScopeError(f"cannot tell the active place: {len(hits)} of project '{project_id}' places match the open Studio instances "
                               f"(places: {[p.place_id for p in entry.places]}). Name the place with place_id.")
        place_id, by = hits[0].place_id, "open_studio"
    sc = reg.resolve(project_id, place_id)
    pe = reg.place(sc)
    return PlaceCtx(project, reg, sc, entry, pe, by)


def match_open(ctx: PlaceCtx, studios: object) -> dict:
    """The open Studio instance that IS this place, or a refusal that says which place is open (never a guess)."""
    return S.match_open_studio(ctx.place_entry, studios, label=ctx.place_id)


def known(project: Project) -> list[dict]:
    reg = load_registry(project)
    out = []
    for pe in reg.projects:
        for pl in pe.places:
            out.append({"project_id": pe.project_id, "place_id": pl.place_id, "alias": pl.alias, "roblox_place_id": pl.roblox_place_id, "studio_name": pl.studio_name,
                        "universe_id": pe.universe_id, "synthetic": pe.synthetic, "stable_id": str(pl.roblox_place_id) if pl.roblox_place_id else f"local-{pl.place_id}"})
    return out


def sibling_places(ctx: PlaceCtx) -> list[PlaceCtx]:
    """Every registered place of the same project (the universe of places that cross-place tools may touch). Never other projects."""
    return [PlaceCtx(ctx.project, ctx.registry, S.Scope(ctx.project_id, pl.place_id), ctx.project_entry, pl) for pl in ctx.project_entry.places]


def project_dir(project: Project, project_id: str) -> Path:
    """projects/<project_id>/ in the repository: per-project synonyms.yaml and roles.yaml live here (tracked, shareable)."""
    return project.root / "projects" / S.safe_name(project_id)
