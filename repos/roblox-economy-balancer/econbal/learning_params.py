"""This repository's tunable parameters, registered with the shared learning layer (``guide_core.params``).

The specs are DERIVED from ``style/style.yaml`` (the pacing bands, the check thresholds and the band-suggestion settings), so the default of every parameter is the number already in
that file: there is no second copy that could drift, and with no learned value in force every result is unchanged.

* ``band.<band_id>.min_minutes`` / ``band.<band_id>.max_minutes`` - the target pacing bands (active minutes per upgrade);
* ``threshold.<name>.max`` / ``threshold.<name>.min`` - the check thresholds in ``style.ranges`` (cost and time cliffs, wall factor, payback, content days, boost speed-up, ...);
* ``learning.min_votes`` / ``learning.shrink`` / ``learning.grow`` - how ``suggest_band_adjustments`` turns playtest notes into band changes.

All of them are PLACEHOLDERS, exactly like the file they come from. Not registered: finding severities (``rules/findings.yaml`` is a ruleset decision), the economy spec's own numbers
(they are the user's game data, with their own ``locked`` flags, and learning never touches them), and constants inside the simulator. A band id that a place's own ``bands.yaml`` does
not define is skipped for that place.

``apply_params`` is the one place learned values reach the analyses: with nothing learned it returns the very same style object, so default results cannot change. Values change only
through guide-core's gated, approved path (propose -> gate -> promote); nothing here applies a change.
"""

from __future__ import annotations

import copy
from importlib import metadata
from pathlib import Path

import yaml

from .domain import places
from .guide_adapter import Project, feedback as fb, params as P, promote as PR, scope as S


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _range(v: float, cap: float | None = None) -> tuple[float, float]:
    hi = max(v * 4, v + 1)
    return 0, (min(hi, cap) if cap is not None else hi)


def _step(v: float) -> float:
    return max(1, round(v / 10)) if isinstance(v, int) else max(0.01, round(v / 10, 4))


def param_specs(style: dict) -> list[P.ParamSpec]:
    specs: list[P.ParamSpec] = []
    for b in style.get("target_bands", []):
        for f in ("min_minutes", "max_minutes"):
            v = b[f]
            lo, hi = _range(v)
            specs.append(P.ParamSpec(f"band.{b['id']}.{f}", v, lo, hi, max_step=_step(v), group="bands",
                                     description=f"target band '{b['id']}' (tiers {b['tiers'][0]}-{b['tiers'][1]}): {f.replace('_', ' ')} per upgrade (PLACEHOLDER)"))
    for name, rng in style.get("ranges", {}).items():
        for f in ("min", "max"):
            v = rng.get(f)
            if _num(v):
                lo, hi = _range(v, cap=1.0 if (isinstance(v, float) and v <= 1.0) else None)
                specs.append(P.ParamSpec(f"threshold.{name}.{f}", v, lo, hi, max_step=_step(v), group="thresholds",
                                         description=f"{name} ({f}): {rng.get('note', '')}".strip()))
    learn = style.get("learning", {})
    for k, (lo, hi, step) in {"min_votes": (1, 10, 1), "shrink": (0.0, 0.9, 0.05), "grow": (0.0, 2.0, 0.05)}.items():
        if _num(learn.get(k)):
            specs.append(P.ParamSpec(f"learning.{k}", learn[k], lo, hi, max_step=step, group="learning", description=f"suggest_band_adjustments: {k}"))
    return specs


def global_style(project: Project) -> dict:
    return yaml.safe_load(Path(project.style_file).read_text(encoding="utf-8"))


def store(project: Project) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(global_style(project)))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


def apply_params(style: dict, ps: P.ParamStore, scope: S.Scope | None) -> dict:
    """The style with the learned values in force for ``scope``. The same object when nothing is learned. Refuses a result whose band minimum exceeds its maximum."""
    if not ps.path.exists():  # nothing was ever learned: the common case costs one stat call
        return style
    changed = {n: s["value"] for n, s in ps.snapshot(scope).items() if s["source"] != "default"}
    if not changed:
        return style
    out = copy.deepcopy(style)
    bands = {b["id"]: b for b in out.get("target_bands", [])}
    for name, value in changed.items():
        kind, _, rest = name.partition(".")
        if kind == "band":
            bid, _, field = rest.rpartition(".")
            if bid in bands:
                bands[bid][field] = value
        elif kind == "threshold":
            tname, _, field = rest.rpartition(".")
            if tname in out.get("ranges", {}) and field in out["ranges"][tname]:
                out["ranges"][tname][field] = value
        elif kind == "learning":
            if rest in out.get("learning", {}):
                out["learning"][rest] = value
    for b in bands.values():
        if b["min_minutes"] > b["max_minutes"]:
            raise ValueError(f"learned parameters make band '{b['id']}' invalid (min {b['min_minutes']} > max {b['max_minutes']}); roll one of them back with ParamStore.rollback")
    return out


def to_core_scope(entry: dict) -> S.Scope:
    return S.Scope(entry["project_id"], entry["place_id"])


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill: a registered place (project and place) or at least a registered project; anything else is refused."""
    if place_id:
        return to_core_scope(places.resolve(project, scope_name, place_id))
    reg = places.load_registry(project)
    if scope_name not in {p for p, _ in reg}:
        raise ValueError(f"unknown project_id '{scope_name}'. Known places: {places.known(reg)}. Add it to {places.registry_path(project)} first; the tool will not guess.")
    return S.Scope(scope_name)


def _corrections(project: Project, scope: S.Scope, k: int = 5) -> list[dict]:
    """Newest corrections for the scope, read from the per-place workspaces (this repository stores feedback per place, so the folder IS the scope)."""
    if scope.is_global:
        folders = [S.scoped_project(project, S.GLOBAL, S.GLOBAL)]
    elif scope.place_id:
        folders = [S.scoped_project(project, scope.project_id, scope.place_id), S.scoped_project(project, S.GLOBAL, S.GLOBAL)]
    else:
        reg = places.load_registry(project)
        folders = [S.scoped_project(project, p, pl) for p, pl in sorted(reg) if p == scope.project_id] + [S.scoped_project(project, S.GLOBAL, S.GLOBAL)]
    rows = [r for f in folders for r in fb.recent_corrections(f, None, k)]
    return sorted(rows, key=lambda r: r["at"], reverse=True)[:k]


def _version() -> str:
    try:
        return metadata.version("roblox-economy-balancer")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .guide_adapter import all_tools
    from .hooks import HOOKS

    return dict(
        name="roblox-economy-balancer", repo="roblox-economy-balancer", repo_version=_version(),
        description=("Use when modelling or checking a Roblox game's economy and progression (upgrade timing against target pacing bands, currency flow, dominant and dead options, progression walls, "
                     "monetisation pace) for one named place with the roblox-economy-balancer MCP server or CLI. Carries the current learned band and threshold values and corrections for the chosen scope."),
        tools=all_tools(project, HOOKS),
        workflow=["Resolve the place: every tool needs project_id AND place_id from projects.yaml; the report says which place it is for.",
                  "check_economy first; read the one-line summary, then the ranked findings. Bands and archetypes are assumptions (placeholders) until the user confirms them.",
                  "Every number in a reply comes from a tool result; never do arithmetic by hand.",
                  "propose_rebalance is a dry run with before/after timings; locked values are never changed; export_values_luau needs the hub's list_roblox_studios output and refuses the wrong open place.",
                  "Save playtest notes with record_decision (dimension pacing, tier, felt) and use suggest_band_adjustments before changing the bands."],
        verified=["simulator, analyses, rebalance proposals and Luau generation on toy economies with hand-computed expectations (self-written evals)", "generated Luau runs on a small lupa mock DataModel and passes the lint"],
        unverified=["never run against a real game, real values or live Studio", "archetype assumptions and target bands are placeholders", "predicts relative pacing only, not retention or revenue"],
        params=store(project), knowledge=knowledge(project), corrections=_corrections(project, scope))
