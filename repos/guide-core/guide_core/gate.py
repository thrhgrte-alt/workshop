"""Gate (step 3 of the improvement loop): test a proposal against everything we already know, before a person even looks at it.

A proposal is run through three suites:

* ``self_written``: the repository's own eval tasks (``evals/tasks``),
* ``real``: ``evals/real/`` - examples the user supplied (may be empty; the result then says so),
* ``corrections``: every past accepted correction that carries an executable ``regression_case`` (see
  :func:`guide_core.feedback.record_decision`), so a fix the user already approved can never be silently undone.

For each suite the baseline (no change) and the candidate (the proposal applied IN MEMORY via ``solver_for``) are run. If ANY
case that passed at baseline fails with the candidate, the verdict is ``reject`` and the failing cases are listed; nothing the
user must review reaches them. A gate that ran zero cases is ``inconclusive``, never a pass. Passing the gate does not apply
anything: approval and promotion are separate, explicit steps (:mod:`guide_core.promote`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import evals, feedback
from .project import Project
from .propose import ProposalStore
from .scope import Scope

CheckMap = dict[str, Callable[..., tuple[bool, str]]]
SolverFor = Callable[["dict | None"], Callable[[dict], Any]]


@dataclass
class GateResult:
    proposal_id: str
    verdict: str  # pass | reject | inconclusive
    suites: dict[str, dict] = field(default_factory=dict)
    regressions: list[dict] = field(default_factory=list)
    improvements: list[dict] = field(default_factory=list)
    cases_run: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def to_dict(self) -> dict:
        return {"proposal_id": self.proposal_id, "verdict": self.verdict, "cases_run": self.cases_run, "suites": self.suites,
                "regressions": self.regressions, "improvements": self.improvements, "notes": self.notes}


def correction_tasks(project: Project, scope: Scope | None = None) -> tuple[list[dict], int]:
    """(tasks, not_executable): past accepted corrections as eval tasks, and how many accepted corrections had no executable case."""
    cases = feedback.accepted_regression_cases(project, scope)
    tasks = [{"id": c["id"], "input": c["input"], "checks": c["checks"], "source": "correction"} for c in cases]
    total = len(feedback.accepted_decisions(project, scope))
    return tasks, max(0, total - len(tasks))


def run_gate(proposal: dict, suites: dict[str, list[dict]], solver_for: SolverFor, checks: CheckMap, *, root: Path) -> GateResult:
    """Run ``proposal`` against ``suites`` (``{"self_written": [...], "real": [...], "corrections": [...]}``).

    ``solver_for(None)`` must return the baseline solver and ``solver_for(proposal)`` the solver with the change applied in memory
    (for a parameter change: ``store.view(scope, {name: after})``). Both are called with each task and return the result the
    task's checks inspect."""
    res = GateResult(proposal["id"], "pass")
    base_solver, cand_solver = solver_for(None), solver_for(proposal)
    for name, tasks in suites.items():
        if not tasks:
            res.suites[name] = {"total": 0, "baseline_passed": 0, "candidate_passed": 0}
            res.notes.append(f"suite '{name}' has no cases" + (" (no real examples have been supplied yet)" if name == "real" else ""))
            continue
        base = evals.run_suite(tasks, base_solver, checks, label=f"{proposal['id']}.{name}.baseline", root=root)
        cand = evals.run_suite(tasks, cand_solver, checks, label=f"{proposal['id']}.{name}.candidate", root=root)
        cmp = evals.compare_detailed(base, cand)
        res.suites[name] = {"total": base["total"], "baseline_passed": base["passed"], "candidate_passed": cand["passed"]}
        res.cases_run += base["total"]
        res.regressions += [{"suite": name, "id": i, "messages": msgs} for i, msgs in cmp["regression_details"].items()]
        res.improvements += [{"suite": name, "id": i} for i in cmp["improvements"]]
    if res.regressions:
        res.verdict = "reject"
        res.notes.append(f"rejected automatically: {len(res.regressions)} previously passing case(s) now fail")
    elif res.cases_run == 0:
        res.verdict = "inconclusive"
        res.notes.append("no cases ran, so nothing was tested: this is not a pass")
    return res


def gate_and_record(proposals: ProposalStore, proposal_id: str, suites: dict[str, list[dict]], solver_for: SolverFor, checks: CheckMap, *, root: Path) -> GateResult:
    """Run the gate for a stored proposal and record the outcome as its status (``gate_passed``/``gate_rejected``/``gate_inconclusive``)."""
    prop = proposals.get(proposal_id)
    if prop is None:
        raise ValueError(f"unknown proposal '{proposal_id}'")
    res = run_gate(prop, suites, solver_for, checks, root=root)
    status = {"pass": "gate_passed", "reject": "gate_rejected", "inconclusive": "gate_inconclusive"}[res.verdict]
    proposals.set_status(proposal_id, status, gate=res.to_dict())
    return res
