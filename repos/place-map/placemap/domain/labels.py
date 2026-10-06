"""Landmark labels (the user's explicit confirm or reject of a role) and the weight update they cause.

Update rule (the spec's repo-specific learning rule), applied to every feature i of the role with its value f_i(x) saved at label time::

    w_i <- w_i + eta * (label - score) * f_i(x)          label = 1 (confirm) or 0 (reject), score = the role score before the label

``eta`` (default 0.1) is a parameter; each new weight is clamped to its parameter range (the default's sign is kept) and to one parameter step from the old value, and is stored as a NEW VERSION
of ``weight.<role>.<feature>`` in a guide-core ParamStore, so the previous weights are kept and :func:`undo` can restore them exactly. Precedence when they disagree: the explicit label,
then the learned project weights, then the default weights. A learned adjustment goes to the global scope only when the user marks it so.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import yaml

from ..guide_adapter import params as P
from .util import clean_path_input, now_iso

SCHEMA = 1


class LabelStore:
    def __init__(self, ctx):
        self.ctx = ctx
        self.file = Path(ctx.wproject.workspace) / "labels.jsonl"

    def rows(self) -> list[dict]:
        out = []
        if self.file.is_file():
            for line in self.file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # a torn last line is skipped, never repaired
        return out

    def append(self, row: dict) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")

    def file_labels(self) -> list[dict]:
        """Labels from the registry's landmarks file (a tracked, shareable seed: ``landmarks: [{path, role, label?, alias?}]``). They count as explicit user labels."""
        f = self.ctx.landmarks_file()
        if f is None or not f.is_file():
            return []
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        out = []
        for item in data.get("landmarks", []):
            raw = str(item.get("path", ""))
            if "::" in raw and raw.split("::", 1)[0] != self.ctx.place_id:
                continue  # a landmark of another place never applies here
            out.append({"kind": "label", "label_id": f"file:{item.get('role')}:{clean_path_input(raw)}", "path": clean_path_input(raw), "role": item.get("role"),
                        "label": 0 if item.get("label") in (0, False, "reject") else 1, "alias": item.get("alias"), "source": "landmarks_file", "at": "file"})
        return out

    def active(self) -> dict[tuple[str, str], dict]:
        """(path, role) -> the latest label row that was not undone."""
        undone = {r["label_id"] for r in self.rows() if r.get("kind") == "undo"}
        act: dict[tuple[str, str], dict] = {}
        for r in self.file_labels() + [r for r in self.rows() if r.get("kind") == "label"]:
            if r["label_id"] not in undone:
                act[(r["path"], r["role"])] = r
        return act

    def positive(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for (path, role), r in self.active().items():
            if r["label"] == 1:
                out.setdefault(role, set()).add(path)
        return out

    def get(self, label_id: str) -> dict | None:
        return next((r for r in self.rows() if r.get("kind") == "label" and r["label_id"] == label_id), None)

    def undone(self, label_id: str) -> bool:
        return any(r.get("kind") == "undo" and r["label_id"] == label_id for r in self.rows())

    def aliases(self) -> dict[str, tuple[str, str]]:
        """normalised alias -> (path, role) for active positive labels that carry an alias."""
        out = {}
        for (path, role), r in self.active().items():
            if r.get("alias") and r["label"] == 1:
                out[" ".join(str(r["alias"]).lower().split())] = (path, role)
        return out


def plan_learning(row: dict, label: int, eta: float, ps: P.ParamStore, scope) -> list[dict]:
    """The weight changes a label would make (nothing is stored). ``row`` is the scored row with ``features``, ``score`` and ``weights``."""
    role = row["role"]
    steps = []
    for fid, f in row["features"].items():
        name = f"weight.{role}.{fid}"
        spec = ps.spec(name)
        before = float(ps.resolve(name, scope).value)
        delta = eta * (label - row["score"]) * f
        delta = max(-spec.step, min(spec.step, delta))
        after = round(max(spec.min, min(spec.max, before + delta)), 6)
        if abs(after - before) < 1e-9:
            continue
        steps.append({"param": name, "feature": fid, "f": f, "before": before, "after": after, "delta": round(after - before, 6)})
    return steps


def apply_learning(ps: P.ParamStore, scope, steps: list[dict], *, approved_by: str, reason: str, evidence: list[str]) -> list[dict]:
    done = []
    for s in steps:
        rec = ps.update(s["param"], s["after"], scope, approved_by=approved_by, reason=reason, evidence=evidence)
        done.append({**s, "version": rec["version"], "scope": scope.key})
    return done


def undo_learning(ps: P.ParamStore, scope, applied: list[dict], *, approved_by: str, reason: str) -> dict:
    """Roll each parameter back one version, but only if the version the label made is still the current one (a newer change is never undone silently)."""
    undone, blocked = [], []
    for a in applied:
        hist = ps.history(a["param"], scope)
        cur = next((h for h in hist if h["current"]), None)
        if cur is None or cur["version"] != a["version"]:
            blocked.append({"param": a["param"], "reason": "a newer change exists for this parameter; undo that one first (or roll back with guide-core params)"})
            continue
        res = ps.rollback(a["param"], scope, approved_by=approved_by, reason=reason)
        undone.append({"param": a["param"], "restored": res["value_now"], "to_version": res["to_version"]})
    return {"undone": undone, "blocked": blocked}


def new_label_id() -> str:
    return f"lbl-{now_iso().replace(':', '').replace('-', '')[:15]}-{uuid.uuid4().hex[:6]}"
