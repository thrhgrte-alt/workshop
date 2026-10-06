"""Scope resolution for every tool: project, place, config, thresholds in force, and the open-Studio match.

``resolve`` is the single refusal point. It needs a project_id AND a place_id that exist in projects.yaml (shared guide-core format), a ``playtest`` profile for the place, and
(for tools that generate scripts or plan a run) the output of the hub's ``list_roblox_studios`` in which EXACTLY ONE open place matches the named one. Nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import learning_params as LP
from ..guide_adapter import Project, scope as S
from . import config as C


@dataclass
class PlaceCtx:
    scope: Any
    entry: Any
    project_entry: Any
    cfg: dict
    style: dict
    config_path: Path
    studio: dict | None = None

    @property
    def label(self) -> str:
        return self.scope.key

    @property
    def synthetic(self) -> bool:
        return bool(self.project_entry.synthetic)


def registry_path(project: Project) -> Path:
    explicit = project.env("PROJECTS")
    return Path(explicit).expanduser() if explicit else project.root / "projects.yaml"


def load_registry(project: Project) -> Any:
    return S.ProjectRegistry.load(registry_path(project))


def known_places(reg: Any) -> list[str]:
    return sorted(f"{p.project_id}/{pl.place_id}" for p in reg.projects for pl in p.places)


def resolve(project: Project, project_id: str | None, place_id: str | None, *, studios: Any = None, need_studio: bool = False) -> PlaceCtx:
    reg = load_registry(project)
    if not project_id and not place_id:
        raise S.ScopeError(f"missing project_id and place_id: every tool needs both so that checks, baselines and feedback are never mixed between places. Known places: {known_places(reg)}")
    if not project_id:
        raise S.ScopeError(f"missing project_id (got only place_id '{place_id}'): every tool needs both and will not guess the project. Known places: {known_places(reg)}")
    if not isinstance(project_id, str) or (place_id is not None and not isinstance(place_id, str)):
        raise S.ScopeError("project_id and place_id must be strings")
    if place_id in (None, ""):
        raise S.ScopeError(f"missing place_id for project '{project_id}': this tool is place-specific and will not guess one. Known places: {known_places(reg)}")
    sc = S.require_scope(project_id, place_id, need_place=True, registry=reg)
    entry = reg.place(sc)
    cfg_path = reg.profile_path(sc, "playtest")
    if cfg_path is None:
        raise S.ScopeError(f"place '{sc.key}' has no profiles.playtest in {registry_path(project)}: name its playtest.yaml there (see projects/demo_mine/main/playtest.yaml)")
    if not cfg_path.exists():
        raise S.ScopeError(f"place '{sc.key}': the playtest config {cfg_path} does not exist")
    cfg = C.load(cfg_path)
    style = LP.resolved_style(project, sc, cfg)
    studio = None
    if need_studio:
        studio = S.match_open_studio(entry, studios, label=sc.key)
    return PlaceCtx(sc, entry, reg.project(sc.project_id), cfg, style, cfg_path, studio)


def head(ctx: PlaceCtx, summary: str, **rest: Any) -> dict:
    res: dict[str, Any] = {"summary": summary, "project_id": ctx.scope.project_id, "place_id": ctx.scope.place_id}
    if ctx.synthetic:
        res["synthetic_place"] = True
    res.update({k: v for k, v in rest.items() if v is not None})
    return res
