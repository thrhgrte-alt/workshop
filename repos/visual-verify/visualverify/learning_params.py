"""This repository's tunable parameters, registered with the shared learning layer (``guide_core.params``).

The specs are DERIVED from ``style/thresholds.yaml``: the default of every parameter is the number in that file (there is no second copy that could drift), so with
nothing learned every result is exactly what the file says. Parameter names are the file's keys (for example ``silhouette.iou_min``).

* ``kind: check`` entries are pass/fail limits a user's accept or reject decisions can move, one bounded step at a time, through propose -> gate -> approve -> promote;
* ``kind: setting`` entries change how a number is measured (cut-offs, extraction settings); they are registered and versioned too, but no decision ever targets them;
* ``locked: true`` entries (``limits.*``) are safety rails: registered LOCKED so learning can never change them.

Nothing here applies a change. Values are changed only through guide-core's gated, approved path (see the ``learn`` CLI commands in hooks.py).
"""

from __future__ import annotations

import functools
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

from .guide_adapter import Project, feedback as fb, params as P, promote as PR, scope as S

SKILL_NAME = "visual-verify"


@functools.lru_cache(maxsize=8)
def _load(path: str, stamp: int) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data.get("thresholds"), dict) or not data["thresholds"]:
        raise ValueError(f"{path}: 'thresholds' must be a non-empty mapping")
    return data


def thresholds_file(project: Project) -> Path:
    return project.root / "style" / "thresholds.yaml"


def raw_thresholds(project: Project) -> dict:
    f = thresholds_file(project)
    return _load(str(f), f.stat().st_mtime_ns)["thresholds"]


def param_specs(project: Project) -> list[P.ParamSpec]:
    specs = []
    for name, d in raw_thresholds(project).items():
        specs.append(P.ParamSpec(name, d["default"], d["min"], d["max"], d.get("step"), locked=bool(d.get("locked")), group=d.get("dimension", ""),
                                 verify_against_current_docs=bool(d.get("verify_against_current_docs")), description=d.get("what", "")))
    return specs


def store(project: Project) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(project))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


class Thresholds:
    """The values in force for one scope: shipped default, or the learned value of that project/place (with where it came from)."""

    def __init__(self, project: Project, scope: S.Scope | None = None, overrides: dict[str, Any] | None = None, ps: P.ParamStore | None = None, use_store: bool = True):
        """``use_store=False`` ignores everything learned (the evals use it: they must give the same result whatever a user has learned)."""
        self.project, self.scope = project, scope
        ps = ps or store(project)
        snap = ps.snapshot(scope) if (use_store and ps.path.exists()) else {n: {"value": s.default, "source": "default", "version": None} for n, s in ps.specs.items()}
        self._v = {n: s["value"] for n, s in snap.items()}
        self._src = {n: s["source"] for n, s in snap.items()}
        for k, v in (overrides or {}).items():
            if k not in ps.specs:
                raise P.ParamError(f"unknown parameter '{k}'")
            self._v[k] = v
            self._src[k] = "override"
        self.meta = raw_thresholds(project)

    def __call__(self, name: str):
        return self._v[name]

    def source(self, name: str) -> str:
        return self._src.get(name, "default")

    def learned(self) -> dict[str, Any]:
        return {n: v for n, v in self._v.items() if self._src[n] not in ("default",)}


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill and the learn commands: a registered project (and place) of projects.yaml; anything else is refused."""
    from .domain import projects as PJ

    return PJ.resolve(project, scope_name, place_id)


def _version() -> str:
    try:
        return metadata.version("visual-verify")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .guide_adapter import all_tools
    from .hooks import HOOKS

    corr = fb.recent_corrections(project, scope, 5)
    return dict(
        name=SKILL_NAME, repo="visual-verify", repo_version=_version(),
        description=("Use when a render, screenshot or texture needs to be measured instead of eyeballed: silhouette overlap with a reference, palette distance, value structure, "
                     "edge density, tiling seams, PBR channel ranges, before/after differences. Pillow and NumPy only; numbers come with the thresholds used. "
                     "Carries the current learned threshold values and corrections for the chosen scope."),
        tools=all_tools(project, HOOKS),
        workflow=["Every tool needs project_id (place_id where it applies) from projects.yaml and refuses without one.",
                  "get_style_brief and find_past_corrections first; read the thresholds and what the user already accepted or rejected.",
                  "Screenshots: save the hub screen_capture result to disk (samples/README.md), ingest_capture (dry run, then apply) to get a local image path.",
                  "measure_image, compare_to_reference, silhouette_iou, palette_distance, check_tiling, check_pbr_ranges or diff_images; report the numbers WITH the limits and pass/fail.",
                  "Say what was not measured (taste, anatomy, perspective); never say an image looks good.",
                  "record_run (it re-measures deterministically) and record_decision with the user's own words; thresholds change only through the learn commands."],
        verified=["every measurement on generated images with known, hand-computed values (self-written evals: they show the tool agrees with itself, not real-world precision)",
                  "MCP tools in memory and over a real stdio process"],
        unverified=["the hub screen_capture result shape (schema_unverified, no real capture existed)", "no real Studio screenshot, Blender render or user texture was measured",
                    "every threshold is a placeholder until the user's own accept/reject decisions or a saved target profile replace it"],
        params=store(project), knowledge=knowledge(project), corrections=corr)
