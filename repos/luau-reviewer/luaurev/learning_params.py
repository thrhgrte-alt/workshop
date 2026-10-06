"""This repository's tunable parameters, registered with the shared learning layer (``guide_core.params``).

The specs are DERIVED from this repository's own config files (``rules/*.yaml`` and ``rules/_settings.yaml``), so the default of every parameter is
the number already in those files: there is no second copy that could drift, and with no learned value in force every result is unchanged.

Registered parameters (all start at the shipped default):

* ``rule.<ID>.confidence`` - the confidence of a rule's findings where the detector does not choose one itself (low < medium < high; one step per change);
* ``rule.<ID>.params.<name>`` - numeric limits in a rule's ``params`` (for example ``rule.DAT005.params.min_seconds``);
* ``learning.similarity_threshold`` / ``learning.max_confidence_steps`` / ``learning.ngram`` - the false-positive learning settings.

The numeric ``limits`` in ``_settings.yaml`` (file size, file count, findings cap, backend timeout and batch size) are registered as LOCKED: they are safety rails,
not tuning targets, so learning can never change them. Not registered: severities (a ruleset decision), ``strict_mode.escalate`` (a mapping, not a number),
and confidences that a detector sets per hit (those are code, not data).

``apply_params`` is the one place the effective values reach the review: with nothing learned it returns its inputs untouched (same objects), so
default results cannot change. Values are changed only through guide-core's gated, approved path (propose -> gate -> promote); nothing here applies a change.
"""

from __future__ import annotations

import copy
import dataclasses
import functools
from importlib import metadata
from pathlib import Path
from typing import Any

from .domain import projects as PJ
from .domain.rules import CONFIDENCES, Rule, load_rules, load_settings
from .guide_adapter import Project, feedback as fb, params as P, promote as PR, scope as S

LOCKED_LIMITS = ("max_file_bytes", "max_files", "max_findings_returned", "backend_timeout_seconds", "backend_batch_size")
SKILL_NAME = "luau-reviewer"


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def param_specs(root: Path) -> list[P.ParamSpec]:
    """Specs for every tunable number in the rules and settings files, with the file's own value as default (cached until a rules file changes)."""
    d = Path(root) / "rules"
    stamp = tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.yaml")))
    return list(_param_specs(str(root), stamp))


@functools.lru_cache(maxsize=8)
def _param_specs(root: str, stamp: tuple) -> tuple[P.ParamSpec, ...]:
    return tuple(_build_specs(Path(root)))


def _build_specs(root: Path) -> list[P.ParamSpec]:
    rules = load_rules(Path(root))
    settings = load_settings(Path(root))
    specs: list[P.ParamSpec] = []
    for r in rules.values():
        specs.append(P.ParamSpec(f"rule.{r.id}.confidence", r.confidence, choices=tuple(CONFIDENCES), group=r.category,
                                 description=f"{r.id} {r.title}: default confidence of findings the detector gives none of its own"))
        for k, v in r.params.items():
            if k == "verify_against_current_docs" or not _is_number(v):
                continue
            hi = max(v * 4, v + 10)
            specs.append(P.ParamSpec(f"rule.{r.id}.params.{k}", v, 0, hi, max_step=max(1, round(v / 10)) if isinstance(v, int) else max(0.1, v / 10),
                                     verify_against_current_docs=bool(r.params.get("verify_against_current_docs")), group=r.category,
                                     description=f"{r.id} {r.title}: params.{k}"))
    learn = settings.get("learning", {})
    if _is_number(learn.get("similarity_threshold")):
        specs.append(P.ParamSpec("learning.similarity_threshold", learn["similarity_threshold"], 0.0, 1.0, max_step=0.05, group="learning",
                                 description="false-positive pattern match: minimum token 3-gram Jaccard similarity"))
    if _is_number(learn.get("max_confidence_steps")):
        specs.append(P.ParamSpec("learning.max_confidence_steps", learn["max_confidence_steps"], 0, 3, max_step=1, group="learning",
                                 description="most confidence steps one rule can lose to false-positive matches"))
    if _is_number(learn.get("ngram")):
        specs.append(P.ParamSpec("learning.ngram", learn["ngram"], 1, 6, max_step=1, group="learning", description="n-gram size of the similarity"))
    for k in LOCKED_LIMITS:
        v = settings.get("limits", {}).get(k)
        if _is_number(v):
            specs.append(P.ParamSpec(f"limits.{k}", v, 0, max(v * 4, v + 10), locked=True, group="limits", description=f"safety rail ({k}): locked, never learned"))
    return specs


def store(project: Project) -> P.ParamStore:
    return P.ParamStore(project.workspace / "learning" / "params.json", param_specs(project.root))


def knowledge(project: Project) -> PR.KnowledgeStore:
    return PR.KnowledgeStore(project.workspace / "learning" / "knowledge")


def to_core_scope(scope: PJ.Scope) -> S.Scope:
    return S.Scope(scope.project_id, scope.place_id)


def resolve_scope(project: Project, scope_name: str, place_id: str | None = None) -> S.Scope:
    """``--scope`` for export-skill: a registered project (and place) of projects.yaml; anything else is refused."""
    return to_core_scope(PJ.resolve_scope(project, scope_name, place_id))


def apply_params(rules: dict[str, Rule], settings: dict, ps: P.ParamStore, scope: S.Scope | None) -> tuple[dict[str, Rule], dict]:
    """Rules and settings as they are with the learned values in force for ``scope``. Identity (the very same objects) when nothing is learned."""
    if not ps.path.exists():  # nothing was ever learned: the common case costs one stat call
        return rules, settings
    snap = ps.snapshot(scope)
    changed = {n: s["value"] for n, s in snap.items() if s["source"] != "default"}
    if not changed:
        return rules, settings
    new_rules = dict(rules)
    for name, value in changed.items():
        parts = name.split(".")
        if parts[0] == "rule" and parts[1] in new_rules:
            r = new_rules[parts[1]]
            if parts[2] == "confidence":
                new_rules[parts[1]] = dataclasses.replace(r, confidence=value)
            elif parts[2] == "params":
                new_rules[parts[1]] = dataclasses.replace(r, params={**r.params, ".".join(parts[3:]): value})
    new_settings = copy.deepcopy(settings)
    for name, value in changed.items():
        if name.startswith("learning."):
            new_settings.setdefault("learning", {})[name.split(".", 1)[1]] = value
    return new_rules, new_settings


def _corrections(project: Project, scope: S.Scope, k: int = 5) -> list[dict]:
    """Newest corrections for the scope. This repository's feedback rows are unscoped, so project and global are read from each run's recorded constraints."""
    runs = {r["run_id"]: r for r in fb.list_runs(project, 10_000)}
    out: list[dict] = []
    for run_id, run in reversed(list(runs.items())):
        c = run.get("constraints") or {}
        glob = bool(c.get("global"))
        if scope.is_global and not glob:
            continue
        if not scope.is_global and not glob and c.get("project_id") != scope.project_id:
            continue
        for d in reversed(fb.decisions_for(project, run_id)):
            if d.get("corrections"):
                out.append({"run_id": run_id, "request": run.get("request"), "decision": d["decision"], "reason": d.get("reason", ""),
                            "corrections": d["corrections"], "at": d["at"]})
        if len(out) >= k:
            break
    return out[:k]


def _version() -> str:
    try:
        return metadata.version("luau-reviewer")
    except metadata.PackageNotFoundError:
        return ""


def skill_kwargs(project: Project, scope: S.Scope) -> dict:
    """Repository-specific arguments for ``guide_core.skillgen.export_skill``."""
    from .hooks import HOOKS  # imported late: hooks imports this module's registry through the CLI
    from .guide_adapter import config

    return dict(
        name=SKILL_NAME, repo="luau-reviewer", repo_version=_version(),
        description=("Use when reviewing Roblox Luau scripts (a file, a folder, or a script exported from Studio) for correctness, security, DataStore and purchase handling, performance, "
                     "leaks and deprecated API with the luau-reviewer MCP server or CLI. Carries the current learned parameter values and corrections for the chosen scope."),
        tools=config.all_tools(project, HOOKS),
        workflow=["Resolve the project: every tool needs project_id (and place_id where it applies) from projects.yaml and refuses without one.",
                  "review_file or review_folder first; read the one-line summary, then the ranked findings (questions are low-confidence, not defects).",
                  "explain_finding for detail on one finding; suggest_patch returns diff text only and never edits a file.",
                  "mark_false_positive or suppress_finding only with the user's verdict and a reason.",
                  "record_run and record_decision with the user's own words."],
        verified=["own rules, tokenizer and ruleset layers on synthetic fixtures (self-written evals; not a measure of precision on real code)",
                  "backend output parsing against stub executables"],
        unverified=["never run against the real luau-analyze, selene or stylua", "never run on a real game's scripts (real precision unknown)",
                    "never connected to Studio (scripts must be saved to disk first)"],
        params=store(project), knowledge=knowledge(project), corrections=_corrections(project, scope))
