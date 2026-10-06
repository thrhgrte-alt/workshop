"""Projects and places: the registry (projects.yaml), scope resolution, and the folders each project may read images from.

A tool never guesses a project: ``resolve`` refuses an empty or unknown project_id and an unknown place_id. Images may be read from the shipped synthetic
``examples/shared`` and ``samples/`` folders, the private workspace, the folders in VISUALVERIFY_ALLOWED_PATHS, and the ``image_roots`` listed for that project
(or place) in projects.yaml - never from another project's ``image_roots``. Everything the tools WRITE goes under the private workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..guide_adapter import Project, scope as S


def registry_path(project: Project) -> Path:
    return Path(project.env("PROJECTS") or (project.root / "projects.yaml")).expanduser()


def load_registry(project: Project) -> S.ProjectRegistry:
    return S.ProjectRegistry.load(registry_path(project))


def resolve(project: Project, project_id: str | None, place_id: str | None = None) -> S.Scope:
    """A Scope for a registered project (and place). Refuses (ScopeError, a ValueError) otherwise."""
    return S.require_scope(project_id, place_id or None, registry=load_registry(project))


def _under(base: Path, rel: str) -> Path:
    p = Path(rel).expanduser()
    return (p if p.is_absolute() else base / p).resolve()


def read_roots(project: Project, registry: S.ProjectRegistry, scope: S.Scope) -> list[Path]:
    roots = [*project.allowed_roots, project.workspace.resolve(), (project.root / "examples" / "shared").resolve(), (project.root / "samples").resolve()]
    base = registry.path.parent if registry.path else project.root
    entry = registry.project(scope.project_id)
    extra = list(entry.extra.get("image_roots") or [])
    if scope.place_id:
        pl = entry.place(scope.place_id)
        extra += list((pl.extra.get("image_roots") if pl else None) or [])
    roots += [_under(base, str(r)) for r in extra]
    seen: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.append(r)
    return seen


def scope_dir(project: Project, scope: S.Scope) -> Path:
    """The private folder of one scope: ``workspace/projects/<project>/<place or _project>/``."""
    return S.project_for_scope(project, scope).workspace


def describe(registry: S.ProjectRegistry, scope: S.Scope) -> dict:
    entry = registry.project(scope.project_id)
    out = {"project_id": scope.project_id, "place_id": scope.place_id}
    if entry.synthetic:
        out["synthetic_project"] = True
    return out


def known(registry: S.ProjectRegistry) -> list[dict]:
    return [{"project_id": p.project_id, "alias": p.alias, "synthetic": p.synthetic, "places": [pl.place_id for pl in p.places]} for p in registry.projects]


def env_flag(project: Project, key: str) -> bool:
    return (project.env(key) or "").lower() in ("1", "true", "yes")


__all__ = ["registry_path", "load_registry", "resolve", "read_roots", "scope_dir", "describe", "known", "env_flag", "os"]
