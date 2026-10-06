"""Propose (step 2 of the improvement loop): group repeated patterns in the run log into structured diff proposals.

This module only READS observations and WRITES proposal records. It has no function that applies a proposal, changes a
parameter, edits a rule or touches a project file: a proposal becomes a change only through :mod:`guide_core.gate` (tests) and
:mod:`guide_core.promote` (explicit approval by a person). A language model may help draft the ``rationale`` and explain a
proposal, but it never applies one.

Signals. Run rows (see :mod:`guide_core.observe`) carry ``signals`` such as::

    {"kind": "override", "target": "param:rule.SEC003.confidence", "direction": "down"}      # user said it is over-confident
    {"kind": "override", "target": "rule:SEC003", "suggest": {"file": "rules/sec.yaml", "path": "SEC003.severity", "before": "error", "after": "warning"}}
    {"kind": "miss", "target": "synonym:rules/synonyms.yaml", "suggest": {"file": "rules/synonyms.yaml", "path": "vendor", "add": ["merchant"]}}

and ``considered`` (targets that were in play), so "overridden in 6 of 8 runs" has the right denominator. A pattern needs at least
``min_runs`` runs and a rate of at least ``min_rate``. If the same target is pushed in opposite directions with similar strength the
pattern is reported as a CONTRADICTION and no proposal is made.

Proposal shape (``kind`` is ``param``, ``rule`` or ``synonym``)::

    {"id": "prop-<hash>", "status": "proposed", "kind": "param", "scope": {...}, "target": "param:x",
     "change": {"op": "set", "param": "x", "before": 0.7, "after": 0.6, "step": 0.1},
     "rationale": "...", "evidence": {"run_ids": [...], "count": 6, "of": 8, "rate": 0.75, "projects": [...]}}
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .params import EPS, ParamError, ParamStore
from .scope import Scope

STATUSES = ("proposed", "gate_passed", "gate_rejected", "gate_inconclusive", "approved", "rejected", "promoted")


def find_patterns(rows: Iterable[dict], *, min_runs: int = 3, min_rate: float = 0.5, kinds: tuple[str, ...] = ("override", "false_positive", "miss")) -> list[dict]:
    """Repeated signals across runs. Each pattern: kind, target, direction, count, of, rate, run_ids, projects, description, contradiction."""
    rows = list(rows)
    seen_in: dict[str, set[str]] = defaultdict(set)  # target -> runs where it was considered
    hits: dict[tuple, dict] = {}
    for r in rows:
        targets_here = set(r.get("considered", []))
        for s in r.get("signals", []):
            targets_here.add(s["target"])
        for t in targets_here:
            seen_in[t].add(r["run_id"])
        for s in r.get("signals", []):
            if s["kind"] not in kinds:
                continue
            key = (s["kind"], s["target"], s.get("direction"))
            h = hits.setdefault(key, {"runs": set(), "projects": set(), "suggest": None})
            h["runs"].add(r["run_id"])
            if r.get("project_id"):
                h["projects"].add(r["project_id"])
            if s.get("suggest") and h["suggest"] is None:
                h["suggest"] = s["suggest"]
    out = []
    for (kind, target, direction), h in hits.items():
        n, of = len(h["runs"]), len(seen_in[target]) or len(h["runs"])
        rate = n / of
        if n < min_runs or rate < min_rate:
            continue
        out.append({"kind": kind, "target": target, "direction": direction, "count": n, "of": of, "rate": round(rate, 3), "run_ids": sorted(h["runs"]),
                    "projects": sorted(h["projects"]), "suggest": h["suggest"], "contradiction": False,
                    "description": f"{kind} of {target}" + (f" ({direction})" if direction else "") + f" in {n} of {of} runs"})
    by_target: dict[tuple, list[dict]] = defaultdict(list)
    for p in out:
        by_target[(p["kind"], p["target"])].append(p)
    for group in by_target.values():
        dirs = {p["direction"] for p in group if p["direction"]}
        if len(dirs) > 1:
            for p in group:
                p["contradiction"] = True
                p["description"] += " - CONTRADICTED by evidence pushing the other way"
    return sorted(out, key=lambda p: (-p["count"], p["target"]))


def _proposal_id(scope: Scope, kind: str, target: str, change: Any) -> str:
    blob = json.dumps({"scope": scope.key, "kind": kind, "target": target, "change": change}, sort_keys=True, default=str)
    return "prop-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def _step_target(store: ParamStore, name: str, scope: Scope, direction: str) -> tuple[Any, Any, float]:
    spec = store.spec(name)
    base = store.resolve(name, scope).value
    up = direction in ("up", "increase", "raise", "+")
    if spec.choices:
        i = spec.choices.index(base) + (1 if up else -1)
        if not 0 <= i < len(spec.choices):
            raise ParamError(f"{name} is already at the end of its choices")
        return base, spec.choices[i], 1
    raw = float(base) + (spec.step if up else -spec.step)
    after = min(max(raw, float(spec.min)), float(spec.max))  # type: ignore[arg-type]
    after = int(round(after)) if spec.kind == "int" else round(after, 10)
    if abs(after - float(base)) <= EPS:
        raise ParamError(f"{name} is already at the limit of its range")
    return base, after, spec.step


def make_proposals(patterns: Iterable[dict], store: ParamStore, scope: Scope) -> dict:
    """Turn patterns into proposals. Returns ``{"proposals": [...], "skipped": [{"target", "reason"}]}``. Applies NOTHING."""
    proposals, skipped = [], []
    now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for p in patterns:
        evidence = {"run_ids": p["run_ids"], "count": p["count"], "of": p["of"], "rate": p["rate"], "projects": p["projects"]}
        if p.get("contradiction"):
            skipped.append({"target": p["target"], "reason": "contradicting evidence (surfaced, not merged)", "pattern": p["description"]})
            continue
        target = p["target"]
        kind, _, rest = target.partition(":")
        if kind == "param":
            try:
                spec = store.spec(rest)
            except ParamError as exc:
                skipped.append({"target": target, "reason": str(exc)})
                continue
            if spec.locked:
                skipped.append({"target": target, "reason": "parameter is locked"})
                continue
            if not p.get("direction"):
                skipped.append({"target": target, "reason": "signal has no direction (up/down)"})
                continue
            try:
                before, after, step = _step_target(store, rest, scope, p["direction"])
            except ParamError as exc:
                skipped.append({"target": target, "reason": str(exc)})
                continue
            change = {"op": "set", "param": rest, "before": before, "after": after, "step": step}
            ptype = "param"
        elif kind in ("rule", "synonym"):
            sug = p.get("suggest")
            if not sug:
                skipped.append({"target": target, "reason": "no structured diff was supplied with the signals; a person must write one"})
                continue
            ptype = kind
            change = {"op": "add" if "add" in sug else "set", **{k: sug[k] for k in ("file", "path", "before", "after", "add") if k in sug}}
        else:
            skipped.append({"target": target, "reason": f"unknown target kind '{kind}' (expected param:, rule: or synonym:)"})
            continue
        proposals.append({"schema": SCHEMA_VERSION, "id": _proposal_id(scope, ptype, target, change), "created": now, "status": "proposed", "kind": ptype,
                          "scope": scope.to_dict(), "target": target, "change": change, "rationale": p["description"], "evidence": evidence})
    return {"proposals": proposals, "skipped": skipped}


class ProposalStore:
    """Append-only proposal records (JSONL). States only move forward by adding events; nothing is rewritten or deleted."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def _events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def add(self, proposal: dict) -> dict:
        existing = self.get(proposal["id"])
        if existing is not None:
            return existing  # idempotent: the same pattern gives the same id
        self._append({"event": "proposed", "proposal": proposal})
        return proposal

    def set_status(self, proposal_id: str, status: str, **info: Any) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if self.get(proposal_id) is None:
            raise ValueError(f"unknown proposal '{proposal_id}'")
        self._append({"event": "status", "id": proposal_id, "status": status, "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "info": info})
        return self.get(proposal_id)  # type: ignore[return-value]

    def get(self, proposal_id: str) -> dict | None:
        found = None
        for e in self._events():
            if e["event"] == "proposed" and e["proposal"]["id"] == proposal_id:
                found = dict(e["proposal"], history=[])
            elif e["event"] == "status" and e["id"] == proposal_id and found is not None:
                found["status"] = e["status"]
                found["history"].append({"status": e["status"], "at": e["at"], **e["info"]})
        return found

    def all(self, status: str | None = None) -> list[dict]:
        ids = list(dict.fromkeys(e["proposal"]["id"] for e in self._events() if e["event"] == "proposed"))
        rows = [self.get(i) for i in ids]
        return [r for r in rows if r and (status is None or r["status"] == status)]
