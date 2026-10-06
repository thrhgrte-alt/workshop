"""The improvement loop for thresholds, wired to guide-core (no learning logic of its own beyond the signals in domain/learning.py).

    decisions (record_decision)  ->  observations with signals            [tools.py]
    propose:  repeated signals -> ONE bounded-step proposal per threshold    guide_core.propose
    gate:     self-written evals + evals/real + every accepted/revised case  guide_core.gate   (a proposal that breaks a passing case is rejected, cases listed)
    promote:  only a gate-passed proposal, only with approved_by + confirm   guide_core.promote (version, changelog, provenance, rollback)
    monitor:  compare the next runs with the previous ones                  guide_core.promote.monitor_change

Everything here is invoked by the ``learn`` CLI commands; no MCP tool applies, approves or promotes a change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import evalsupport as ES
from . import learning_params as LP
from .guide_adapter import evals, feedback, gate as G, observe, promote as PR, propose as PP, scope as S


def paths(project) -> dict[str, Path]:
    d = project.workspace / "learning"
    return {"obs": d / "observations.jsonl", "proposals": d / "proposals.jsonl"}


def run_log(project) -> observe.RunLog:
    return observe.RunLog(paths(project)["obs"])


def proposals(project) -> PP.ProposalStore:
    return PP.ProposalStore(paths(project)["proposals"])


def propose(project, scope: S.Scope, min_runs: int = 3, min_rate: float = 0.5) -> dict[str, Any]:
    """Group the signals logged for ``scope`` into proposals and store them (status 'proposed'). Applies nothing."""
    rows = run_log(project).runs(scope)
    patterns = PP.find_patterns(rows, min_runs=min_runs, min_rate=min_rate)
    made = PP.make_proposals(patterns, LP.store(project), scope)
    store = proposals(project)
    kept = [store.add(p) for p in made["proposals"]]
    return {"runs_considered": len(rows), "patterns": [{k: p[k] for k in ("target", "direction", "count", "of", "rate", "contradiction")} for p in patterns],
            "proposals": [_brief(p) for p in kept], "skipped": made["skipped"]}


def _brief(p: dict) -> dict:
    c, ev = p["change"], p["evidence"]
    return {"id": p["id"], "status": p["status"], "param": c.get("param"), "before": c.get("before"), "after": c.get("after"), "scope": (p["scope"].get("project_id"), p["scope"].get("place_id")),
            "evidence": f"{ev['count']} of {ev['of']} runs ({ev['rate']:.0%})", "run_ids": ev["run_ids"][:5]}


def gate(project, scope_of_proposal: S.Scope | None, proposal_id: str) -> dict[str, Any]:
    """Run a stored proposal against the self-written evals, evals/real and every past accepted/revised case, and record the verdict on the proposal."""
    store = proposals(project)
    prop = store.get(proposal_id)
    if prop is None:
        raise ValueError(f"unknown proposal '{proposal_id}'")
    sc = S.scope_from_dict(prop["scope"])
    split = evals.load_split(project.root / "evals" / "tasks", project.root / "evals" / "real")
    corr, not_exec = G.correction_tasks(project, sc)
    # tasks that run a whole record/propose/gate flow in their own temporary workspace are not gate cases (they would recurse into this very gate)
    own = [t for t in split["self_written"] if t["input"].get("op") != "learning"]
    suites = {"self_written": own, "real": split["real"], "corrections": corr}
    res = G.gate_and_record(store, proposal_id, suites, ES.solver_for(project, sc), ES.checks(), root=project.root)
    out = res.to_dict()
    out["accepted_cases_without_executable_check"] = not_exec
    return out


def promote(project, proposal_id: str, approved_by: str, confirm: bool) -> dict[str, Any]:
    return PR.promote_proposal(proposals(project), proposal_id, params=LP.store(project), knowledge=LP.knowledge(project), approved_by=approved_by, confirm=confirm)


def reject(project, proposal_id: str, by: str, reason: str) -> dict[str, Any]:
    return PR.reject_proposal(proposals(project), proposal_id, rejected_by=by, reason=reason)


def rollback(project, param: str, scope: S.Scope, approved_by: str, reason: str, to_version: int | None = None) -> dict[str, Any]:
    return LP.store(project).rollback(param, scope, approved_by=approved_by, reason=reason, to_version=to_version)


def monitor(project, scope: S.Scope, change_at: str, n: int = 10) -> dict[str, Any]:
    return PR.monitor_change(run_log(project), scope, change_at, n=n)


def listing(project, scope: S.Scope) -> dict[str, Any]:
    ps = LP.store(project)
    snap = ps.snapshot(scope)
    return {"scope": scope.label, "learned": {n: {"value": s["value"], "version": s["version"], "source": s["source"], "default": s["default"]} for n, s in snap.items() if s["source"] != "default"},
            "proposals": [_brief(p) | {"status": p["status"]} for p in proposals(project).all()], "knowledge_version": ps.knowledge_version()}
