"""This repository's tunable parameters, registered with the shared learning layer (``guide_core.params``).

Every NUMERIC limit that a profile resolves for a rule is a parameter: ``limit.<profile>.<RULE_ID>.<limit_key>`` (for example ``limit.prop.MESH_TRI_BUDGET.max_triangles``). Specs are DERIVED from
``rules/*.yaml`` and ``rules/profiles/*.yaml``: the default of each parameter is the value the profile already gives (the profile's own limit, else the rule's default), so there is no second
copy of any number and, with nothing learned, every report is unchanged. Only rules that apply to a profile and are enabled for it are registered for that profile.

Precedence for a limit, strongest first (always shown by the parameter store as "which one won"): a value passed in the call (``limit_overrides``), a saved override the user recorded
(``record_override``), the project's own ``projects/<id>/profiles.yaml`` (explicit user configuration; a learned value never replaces a number that file sets), the learned parameter value for
the project, the shipped profile/rule default. Learned values are per project (the preflight does not need a place).

Not registered: severities, enable switches and ``safe`` flags (ruleset decisions), list/string/boolean limits (name patterns, ``mode``), and limits that are not numbers.

``apply_params`` is the one place learned values reach a report: with nothing learned it returns the very same Config object. Values change only through guide-core's gated, approved path
(propose -> gate -> promote); nothing here applies a change.
"""

from __future__ import annotations

import copy
from importlib import metadata
from pathlib import Path

from . import projects as PJ
from .domain import engine
from .domain.rules import Config
from .guide_adapter import Project, feedback as fb, params as P, promote as PR, scope as S

SKILL_NAME = "asset-roblox-preflight"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _applies(cfg: Config, profile: str, rid: str) -> bool:
    r = cfg.rules[rid]
    if not r.get("enabled", True) or rid in (cfg.profiles[profile].get("disabled_rules") or []):
        return False
    at = r.get("applies_to")
    return not at or profile in at


def effective_numeric_limits(cfg: Config) -> dict[tuple[str, str, str], tuple[float, bool]]:
    """``{(profile, rule, key): (value, verify_against_current_docs)}`` for every numeric limit a profile resolves."""
    out: dict[tuple[str, str, str], tuple[float, bool]] = {}
    for p in sorted(cfg.profiles):
        for rid in sorted(cfg.rules):
            if not _applies(cfg, p, rid):
                continue
            for key, spec in (cfg.rules[rid].get("limits") or {}).items():
                v = ((cfg.profiles[p].get("limits") or {}).get(rid) or {}).get(key, spec.get("default"))
                if _num(v):
                    out[(p, rid, key)] = (v, bool(spec.get("verify_against_current_docs")))
    return out


def param_specs(cfg: Config) -> list[P.ParamSpec]:
    specs = []
    for (p, rid, key), (v, verify) in effective_numeric_limits(cfg).items():
        hi = max(v * 4, v + 1)
        if isinstance(v, int) and v == 0:
            hi = 10
        if isinstance(v, float) and v <= 1.0 and any(w in key for w in ("fraction", "ratio", "share")):
            hi = 1.0  # a fraction cannot exceed 1 (a ratio above 1, such as a spread, keeps the general range)
        step = max(1, round(v / 10)) if isinstance(v, int) else max(0.01, round(v / 10, 4))
        specs.append(P.ParamSpec(f"limit.{p}.{rid}.{key}", v, 0, hi, max_step=step, verify_against_current_docs=verify, group=cfg.rules[rid]["category"],
                                 description=f"{rid} {key} for the {p} profile (a DEFAULT chosen by this tool, not an official Roblox limit)"))
    return specs


def store(project: Project) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(engine.load_config(project.root)))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


def apply_params(cfg: Config, base: Config, ps: P.ParamStore, scope: S.Scope | None) -> Config:
    """``cfg`` with the learned values in force for ``scope``. The very same object when nothing is learned. ``base`` is the global config (no project layer):
    a limit that the project's own profiles.yaml changed (cfg differs from base) is explicit user configuration and is left alone."""
    if not ps.path.exists():  # nothing was ever learned: the common case costs one stat call
        return cfg
    changed = {n: s["value"] for n, s in ps.snapshot(scope).items() if s["source"] != "default"}
    if not changed:
        return cfg
    profiles = copy.deepcopy(cfg.profiles)
    touched = False
    for name, value in changed.items():
        _, p, rest = name.split(".", 2)
        rid, _, key = rest.rpartition(".")
        if p not in profiles or rid not in cfg.rules:
            continue
        mine = ((cfg.profiles[p].get("limits") or {}).get(rid) or {}).get(key)
        theirs = ((base.profiles[p].get("limits") or {}).get(rid) or {}).get(key) if p in base.profiles else None
        if mine != theirs:  # the project's profiles.yaml sets this limit explicitly
            continue
        profiles[p].setdefault("limits", {}).setdefault(rid, {})[key] = value
        touched = True
    return Config(cfg.rules, profiles, cfg.style, cfg.project_id) if touched else cfg


def params_file(project: Project) -> Path:
    return project.workspace / "learning" / "params.json"


def effective_config(project: Project, project_id: str, cfg: Config) -> Config:
    """``cfg`` (already layered with the project's profiles.yaml) with this project's learned limits. One stat call when nothing was ever learned."""
    if not params_file(project).exists():
        return cfg
    return apply_params(cfg, engine.load_config(project.root), store(project), S.Scope(project_id))


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill: a registered project (``place`` must match its registered place id); anything else is refused."""
    PJ.resolve(project.root, scope_name, place_id)
    return S.Scope(scope_name)


def _corrections(project: Project, scope: S.Scope, k: int = 5) -> list[dict]:
    """Newest corrections for the scope, read from the per-project feedback folders (this repository stores feedback per project, so the folder IS the scope)."""
    folders = [PJ.scoped(project, PJ.GLOBAL)] if scope.is_global else [PJ.scoped(project, scope.project_id), PJ.scoped(project, PJ.GLOBAL)]
    rows = [r for f in folders for r in fb.recent_corrections(f, None, k)]
    return sorted(rows, key=lambda r: r["at"], reverse=True)[:k]


def _version() -> str:
    try:
        return metadata.version("asset-roblox-preflight")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .guide_adapter import all_tools
    from .hooks import HOOKS

    return dict(
        name=SKILL_NAME, repo="asset-roblox-preflight", repo_version=_version(),
        description=("Use when checking a Blender export (glb, gltf, obj, or a summary of an fbx) and its textures before it goes to Roblox: triangle budgets, transforms, names, UVs, materials, rigs and "
                     "collision, per project and asset profile, with the asset-roblox-preflight MCP server or CLI. Carries the current learned limits and corrections for the chosen scope."),
        tools=all_tools(project, HOOKS),
        workflow=["Resolve the project: every tool needs project_id from projects.yaml (place_id is optional and only recorded for the upload hand-off).",
                  "preflight_report (or a focused check_* tool) with the asset profile; read the one-line pass/fail, then the ranked findings.",
                  "Say ready_to_upload only when no errors remain, and say the limits are defaults to verify against current Roblox documentation; never upload or publish from here.",
                  "apply_safe_fix only emits a Blender script for fixes marked safe; run it on a copy of the .blend, re-export and re-check.",
                  "record_override with the user's own words, scoped to the profile; offer to promote repeated overrides only after repeats."],
        verified=["readers and rules on synthetic assets generated by script (self-written evals; not a measure of precision on real exports)"],
        unverified=["never run against real Blender or a real game asset export", "FBX geometry is not measured (a summary is used instead)", "all limits are defaults to verify against current Roblox docs",
                    "nothing was ever uploaded to Roblox"],
        params=store(project), knowledge=knowledge(project), corrections=_corrections(project, scope))
