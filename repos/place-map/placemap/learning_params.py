"""This repository's tunable numbers, registered with the shared learning layer (``guide_core.params``).

The specs are DERIVED from ``rules/limits.yaml`` (limits, feature constants, learning and search settings) and ``rules/roles.yaml`` (+ ``projects/<id>/roles.yaml``): the default of every
parameter is the number already in those files, so with no learned value in force results are identical and there is no second copy that could drift.

* ``weight.<role>.<feature>`` - the role feature weights (range keeps the default's sign; a learned step is at most ``weight_step``);
* ``band.<role>.confident`` / ``band.<role>.candidate`` - the score bands;
* ``feature.*`` - repetition damping and Jaccard threshold, default token k, proximity distance;
* ``learning.eta`` / ``learning.weight_limit`` - the label update rule's step and clamp (weight_limit is read from the file when specs are built);
* ``snapshots.*`` / ``collector.*`` / ``search.*`` / ``output.*`` - staleness, retention, collector caps, search margins, result cap.

All of them are PLACEHOLDERS, exactly like the files they come from; those marked verify_against_current_docs depend on Studio/hub limits. Values change only through the label rule
(a person's explicit label, bounded step, versioned, undoable) or guide-core's gated, approved path. Not registered: the role feature structure itself (which features a role has), the
secret patterns, and finding text.
"""

from __future__ import annotations

from importlib import metadata

import yaml

from .domain import places, roles as R
from .guide_adapter import Project, params as P, promote as PR, scope as S, feedback as fb

WEIGHT_STEP = 0.25


def limits(project: Project) -> dict:
    return yaml.safe_load((project.root / "rules" / "limits.yaml").read_text(encoding="utf-8"))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def param_specs(project: Project, project_id: str | None = None) -> list[P.ParamSpec]:
    lim = limits(project)
    specs: list[P.ParamSpec] = []
    for section in ("snapshots", "collector", "features", "learning", "search", "output"):
        for key, body in (lim.get(section) or {}).items():
            if not (isinstance(body, dict) and _num(body.get("value"))):
                continue
            specs.append(P.ParamSpec(f"{section}.{key}" if section != "features" else f"feature.{key}", body["value"], body["min"], body["max"], max_step=body.get("step"),
                                     group=section, description=body.get("note", ""), verify_against_current_docs=bool(body.get("verify_against_current_docs"))))
    limit = float(((lim.get("learning") or {}).get("weight_limit") or {}).get("value", 6.0))
    roles = R.load_roles(project, project_id)
    for role in roles.values():
        for fid, feat in role.features.items():
            w = feat.w if _num(feat.w) else 0.0
            lo, hi = (0.0, max(limit, abs(w))) if w >= 0 else (-max(limit, abs(w)), 0.0)
            specs.append(P.ParamSpec(f"weight.{role.id}.{fid}", w, lo, hi, max_step=WEIGHT_STEP, group="weights",
                                     description=f"role {role.id}, feature {fid} ({feat.fn}) weight (PLACEHOLDER default)"))
        for b in ("confident", "candidate"):
            v = role.bands.get(b)
            if _num(v):
                specs.append(P.ParamSpec(f"band.{role.id}.{b}", v, 0.0, 1.0, max_step=0.05, group="bands", description=f"role {role.id}: score at or above which an instance is {b} (PLACEHOLDER)"))
    return specs


def store(project: Project, project_id: str | None = None) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(project, project_id))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


def values_for(project: Project, project_id: str | None, place_id: str | None, ps: P.ParamStore | None = None):
    """``pv(name)`` for a place: place value, then project value, then promoted global value, then the shipped default. Reads the store file once."""
    ps = ps or store(project, project_id)
    sc = S.Scope(project_id, place_id) if project_id else None
    snap = ps.snapshot(sc)

    def pv(name: str):
        v = snap[name]["value"]
        if name.startswith("band.") and name.endswith(".candidate"):  # a learned band may never invert
            conf = snap.get(name[:-len("candidate")] + "confident")
            if conf is not None:
                v = min(v, conf["value"])
        return v

    pv.snapshot = snap  # type: ignore[attr-defined]
    return pv


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill: a registered project (and place); anything else is refused."""
    reg = places.load_registry(project)
    if scope_name not in reg.project_ids():
        raise ValueError(f"unknown project_id '{scope_name}'. Known projects: {reg.project_ids()}. Add it to {places.registry_path(project)} first; the tool will not guess.")
    return reg.resolve(scope_name, place_id) if place_id else S.Scope(scope_name)


def _corrections(project: Project, scope: S.Scope, k: int = 5) -> list[dict]:
    """Newest corrections for the scope, from the per-place feedback folders (feedback is stored per place, so the folder is the scope) plus the global folder."""
    if scope.is_global:
        folders = [S.scoped_project(project, S.GLOBAL, S.GLOBAL)]
    elif scope.place_id:
        folders = [S.scoped_project(project, scope.project_id, scope.place_id), S.scoped_project(project, S.GLOBAL, S.GLOBAL)]
    else:
        folders = [S.scoped_project(project, scope.project_id, pl) for pl in [p["place_id"] for p in places.known(project) if p["project_id"] == scope.project_id]]
        folders.append(S.scoped_project(project, S.GLOBAL, S.GLOBAL))
    rows = [r for f in folders for r in fb.recent_corrections(f, None, k)]
    return sorted(rows, key=lambda r: r["at"], reverse=True)[:k]


def _version() -> str:
    try:
        return metadata.version("place-map")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .guide_adapter import all_tools
    from .hooks import HOOKS

    return dict(
        name="place-map", repo="place-map", repo_version=_version(),
        description=("Use when you need to find or understand something in a Roblox place without re-exploring it: where an object or script is, what fires a remote, what depends on a module, "
                     "what changed since the last snapshot, or which instances play a role (vendor, collectible, spawn ...), for one named place, with the place-map MCP server or CLI. "
                     "Carries the learned role weights and corrections for the chosen scope."),
        tools=all_tools(project, HOOKS),
        workflow=["Resolve the place: every tool needs project_id AND place_id (or the hub's list_roblox_studios output as studios); never guess. Paths come back as place::Path.",
                  "find_in_place first; read the one-line summary, the snapshot time and the stale flag. A stale snapshot offers plan_refresh.",
                  "plan_refresh returns Luau for the hub's execute_luau; it refuses when the open Studio place is not the named one. Ingest the result with ingest_snapshot (dry run first).",
                  "A request that names a thing returns that exact path only; near matches are listed as not selected. Ask when candidates are borderline; never widen.",
                  "label_landmark records the user's confirm/reject of a candidate and nudges that project's weights by a bounded, undoable step."],
        verified=["parsers, collector Luau (lint + run on the guide-core mock), role scoring, search, graph, diff and multi-place logic on synthetic places (self-written evals)"],
        unverified=["never run against real Studio or the hub: every parser is schema_unverified", "all weights, bands and limits are placeholders until confirmed on real places",
                    "self-written evals only show the tool agrees with itself; evals/real/ is empty until you add examples"],
        params=store(project, scope.project_id if not scope.is_global else None), knowledge=knowledge(project), corrections=_corrections(project, scope))
