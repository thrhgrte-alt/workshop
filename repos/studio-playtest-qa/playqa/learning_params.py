"""This repository's tunable numbers on guide-core's learning layer (``guide_core.params``).

Every setting and acceptance range in ``style/style.yaml`` is registered as a named parameter whose DEFAULT is the number already in that file, so there is no second copy that
could drift, and with nothing learned every verdict is unchanged:

* ``setting.<name>``              knobs (boot window, probe distances, timeouts, repeats N, cap on findings ...);
* ``threshold.<name>.<min|max>``  acceptance bounds (error/warning lines at boot, move fraction, end gap, economy caps, performance percentages ...).

All are PLACEHOLDERS (the user has not supplied pacing or budgets). ``boot_error_lines.max`` is ``locked``. The two pathfinding agent defaults carry
``verify_against_current_docs``. Values change only through guide-core's gated, approved path (propose -> gate -> promote); nothing here applies a change. ``resolved_style`` is the
one place values reach the checks: shipped style, then the project's style overlay, then learned values for the scope, then explicit numbers in the place's playtest.yaml.

Not registered: assertion ids, the cause rules and log patterns in rules/ (rules and recipes change only through the improvement loop as reviewed diffs), the Luau text, and anything in
a game's own playtest.yaml apart from ``overrides``.
"""

from __future__ import annotations

import copy
from importlib import metadata

from .guide_adapter import Project, config as gc, feedback as fb, params as P, promote as PR, scope as S


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def param_specs(style: dict) -> list[P.ParamSpec]:
    specs: list[P.ParamSpec] = []
    for name, e in style.get("settings", {}).items():
        specs.append(P.ParamSpec(f"setting.{name}", e["value"], e["min"], e["max"], max_step=e["step"], group="settings", description=e.get("note", ""),
                                 verify_against_current_docs=bool(e.get("verify_against_current_docs"))))
    for name, r in style.get("ranges", {}).items():
        lo, hi = r.get("bounds", [None, None])
        for f in ("min", "max"):
            v = r.get(f)
            if _num(v):
                if lo is None:
                    lo, hi = 0, max(v * 4, v + 1)
                specs.append(P.ParamSpec(f"threshold.{name}.{f}", v, lo, hi, max_step=r.get("step", max(1, round(v / 10))), locked=bool(r.get("locked")), group="thresholds",
                                         description=f"{name} ({f}): {r.get('note', '')}".strip()))
    return specs


def global_style(project: Project) -> dict:
    return gc.load_style(project)


def store(project: Project) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(global_style(project)))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


def apply_params(style: dict, ps: P.ParamStore, scope: S.Scope | None) -> dict:
    """The style with the learned values in force for ``scope``; the very same object when nothing is learned."""
    if not ps.path.exists():
        return style
    changed = {n: s["value"] for n, s in ps.snapshot(scope).items() if s["source"] != "default"}
    if not changed:
        return style
    out = copy.deepcopy(style)
    for name, value in changed.items():
        kind, _, rest = name.partition(".")
        if kind == "setting" and rest in out["settings"]:
            out["settings"][rest]["value"] = value
            out["settings"][rest]["source"] = "learned"
        elif kind == "threshold":
            rname, _, f = rest.rpartition(".")
            if rname in out["ranges"] and f in out["ranges"][rname]:
                out["ranges"][rname][f] = value
                out["ranges"][rname]["source"] = "learned"
    return out


def resolved_style(project: Project, scope: S.Scope, cfg: dict | None = None) -> dict:
    """Shipped style, per-project overlay, learned values, then the place's explicit overrides (in that order; later wins)."""
    from .domain import config as C

    style = S.load_layered_style(project, scope)
    style = {k: v for k, v in style.items() if k != "_layers"}
    style = apply_params(style, store(project), scope)
    return C.apply_overrides(style, cfg or {})


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill: a registered project (and place); anything else is refused."""
    from .domain import places

    reg = places.load_registry(project)
    return reg.resolve(scope_name, place_id)


def _version() -> str:
    try:
        return metadata.version("studio-playtest-qa")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .guide_adapter import all_tools
    from .hooks import HOOKS

    return dict(
        name="studio-playtest-qa", repo="studio-playtest-qa", repo_version=_version(),
        description=("Use when scripting a playtest of a Roblox place in the open Studio session and judging it with evidence: boot errors and warnings, spawn, reachability of named areas, remotes, "
                     "an economy smoke test, a data round trip in TEST MODE, and a performance snapshot against a stored baseline, for one named place with the studio-playtest-qa MCP server or CLI. "
                     "Carries the current learned thresholds and corrections for the chosen scope."),
        tools=all_tools(project, HOOKS),
        workflow=["Fix the scope: every tool needs project_id AND place_id from projects.yaml; the report says which place it is for. Get the open place from the hub's list_roblox_studios; it must be the named one.",
                  "list_checks, then plan_playtest(studios=...): it names the hub calls in order and re-runs flaky checks N times. generate_check_script(dry_run=false) gives the Luau for execute_luau.",
                  "Run the plan in the open Studio session only (never a live server); save each raw answer, the console text and any screenshot path.",
                  "explain_failure(check_id, results, console_log, screenshots): one-line verdict first; every failure has the check, the evidence and the most likely cause (a heuristic). A flaky check is called only after N runs.",
                  "A data check runs only in TEST MODE and is refused without a test-mode switch. Every threshold is a placeholder until the user supplies theirs.",
                  "Save outcomes with record_result and the user's verdicts with record_decision (dimension threshold, cause, flaky ...). Never claim anything ran in Studio without the hub's answers."],
        verified=["script generation, the Luau lint, execution on the lupa mock world with planted faults (unreachable room, erroring remote, boot error, currency bug, ...) and the evaluators on those results (self-written evals)",
                  "console parsing, flaky aggregation, baselines and refusals on synthetic inputs"],
        unverified=["never run against real Studio, the real hub or a real game", "every parser of hub output is schema_unverified (execute_luau, get_console_output, screen_capture, list_roblox_studios)",
                    "every threshold is a placeholder; the economy ranges are not the user's pacing", "causes are heuristics, not diagnoses"],
        params=store(project), knowledge=knowledge(project), corrections=_corrections(project, scope))


def _corrections(project: Project, scope: S.Scope, k: int = 5) -> list[dict]:
    return fb.recent_corrections(project, scope, k)
