"""Promote (steps 4-6 of the improvement loop, and the generalisation rules): versioning, provenance, global candidates.

Nothing here happens by itself. Every change needs a person's name in ``approved_by`` and ``confirm=True``; there is no
auto-promote, no auto-apply and no delete (items are *archived*, never removed; rollbacks add events, they do not erase history).

Two stores:

* :func:`promote_proposal` applies an APPROVED, GATE-PASSED proposal: a parameter change goes through
  :meth:`guide_core.params.ParamStore.update` (bounded step, new version, provenance); a rule/synonym diff is recorded as an
  approved diff for a person to apply (guide-core never edits a project's files). Both write a changelog line.
* :class:`KnowledgeStore` holds learned items keyed by an ABSTRACT ``signature`` (features and roles such as
  ``has_interaction+purchase_remote``, not game names). An item starts in the project where it was learned. It becomes a *global
  candidate* only when it has been confirmed in at least ``k`` distinct projects (default 3) with no contradicting evidence and is
  not on the known-non-transferable list. A person still approves the promotion (:meth:`KnowledgeStore.promote_global`).

Staleness. Items carry ``last_confirmed`` and a confidence. :meth:`KnowledgeStore.retrievable` drops items not confirmed within
``max_age_days`` (default 180) from retrieval, and :meth:`KnowledgeStore.archive_stale` records that as ``archived`` - the item
and its history stay. Contradictions are listed by :meth:`KnowledgeStore.contradictions`, never merged silently.

Conflict order for any value: explicit user label, project value, promoted global value, shipped default - see
:meth:`guide_core.params.ParamStore.resolve`, which reports which one won.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .observe import RunLog, redact
from .params import AUTO_APPROVERS, ParamError, ParamStore
from .propose import ProposalStore
from .scope import Scope

DEFAULT_K = 3
DEFAULT_MAX_AGE_DAYS = 180


class PromotionError(ValueError):
    pass


def _now(now: _dt.datetime | None = None) -> _dt.datetime:
    return now or _dt.datetime.now(_dt.timezone.utc)


def _iso(now: _dt.datetime | None = None) -> str:
    return _now(now).isoformat(timespec="seconds")


def _approver(approved_by: str | None) -> str:
    who = (approved_by or "").strip()
    if who.lower() in AUTO_APPROVERS:
        raise PromotionError("promotion needs approved_by: the name of the person who approved it (automatic approval is not allowed)")
    return who


def _need_confirm(confirm: bool, what: str) -> None:
    if confirm is not True:
        raise PromotionError(f"{what} needs confirm=True after the user has said yes; without it nothing is changed")


def abstract_signature(*features: str) -> str:
    """Canonical signature for a set of abstract features/roles (sorted, lower-case), e.g. ``has_interaction+purchase_remote``."""
    cleaned = sorted({f.strip().lower().replace(" ", "_") for f in features if f and f.strip()})
    if not cleaned:
        raise PromotionError("a signature needs at least one abstract feature")
    return "+".join(cleaned)


class KnowledgeStore:
    """Learned items with provenance and a changelog, as append-only events in ``<dir>/knowledge.jsonl`` (+ ``changelog.jsonl``)."""

    def __init__(self, directory: str | Path, *, k: int = DEFAULT_K, max_age_days: int = DEFAULT_MAX_AGE_DAYS):
        if k < 1:
            raise ValueError("k must be at least 1")
        self.dir = Path(directory)
        self.k = k
        self.max_age_days = max_age_days

    # event log -------------------------------------------------------------------------------------------------
    @property
    def _events_file(self) -> Path:
        return self.dir / "knowledge.jsonl"

    @property
    def _changelog_file(self) -> Path:
        return self.dir / "changelog.jsonl"

    def _append(self, event: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": SCHEMA_VERSION, **event}, sort_keys=True, default=str) + "\n")

    def log_change(self, line: str, *, by: str, ref: str = "", version: int | None = None, provenance: dict | None = None, now: _dt.datetime | None = None) -> dict:
        row = {"at": _iso(now), "line": line, "by": by, "ref": ref, "version": version, "provenance": provenance or {}}
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._changelog_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return row

    def changelog(self) -> list[dict]:
        if not self._changelog_file.exists():
            return []
        return [json.loads(x) for x in self._changelog_file.read_text(encoding="utf-8").splitlines() if x.strip()]

    def version(self) -> int:
        """Knowledge version: number of changelog lines (grows with every promoted or recorded change)."""
        return len(self.changelog())

    def render_changelog(self) -> str:
        return "\n".join(f"- {r['at'][:10]} {r['line']} (by {r['by']}{', v' + str(r['version']) if r.get('version') else ''})" for r in self.changelog()) or "- (no changes yet)"

    def _replay(self) -> tuple[dict[str, dict], dict[str, dict]]:
        items: dict[str, dict] = {}
        nontransfer: dict[str, dict] = {}
        if not self._events_file.exists():
            return items, nontransfer
        for line in self._events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            t = e["event"]
            if t == "created":
                it = e["item"]
                items[it["id"]] = {**it, "version": 1, "confirmed_in": {it["home_project"]: {"count": 1, "last": e["at"], "run_ids": it["provenance"][0]["run_ids"]}},
                                   "contradicted_in": {}, "status": "active", "scope": "project", "last_confirmed": e["at"], "snapshots": []}
            elif t in ("confirmed", "contradicted", "promoted_global", "demoted_global", "archived", "unarchived", "revised"):
                it = items.get(e["id"])
                if it is None:
                    continue
                it["snapshots"].append({k: it[k] for k in ("version", "scope", "status", "confidence", "text")})
                it["version"] += 1
                if t == "confirmed":
                    c = it["confirmed_in"].setdefault(e["project_id"], {"count": 0, "last": e["at"], "run_ids": []})
                    c["count"] += 1
                    c["last"] = e["at"]
                    c["run_ids"] = sorted(set(c["run_ids"]) | set(e.get("run_ids", [])))
                    it["last_confirmed"] = e["at"]
                    if it["status"] == "archived":
                        it["status"] = "active"  # a fresh confirmation revives it
                    it["provenance"].append({"at": e["at"], "by": e.get("by", ""), "project_id": e["project_id"], "run_ids": e.get("run_ids", []), "what": "confirmed"})
                elif t == "contradicted":
                    it["contradicted_in"].setdefault(e["project_id"], []).append({"at": e["at"], "note": e.get("note", ""), "run_ids": e.get("run_ids", [])})
                elif t == "promoted_global":
                    it["scope"] = "global"
                    it["provenance"].append({"at": e["at"], "by": e["approved_by"], "what": "promoted to global", "projects": e["projects"]})
                elif t == "demoted_global":
                    it["scope"] = "project"
                    it["provenance"].append({"at": e["at"], "by": e["approved_by"], "what": "global promotion rolled back", "reason": e.get("reason", "")})
                elif t == "archived":
                    it["status"] = "archived"
                    it["provenance"].append({"at": e["at"], "by": e.get("by", "staleness"), "what": "archived", "reason": e.get("reason", "")})
                elif t == "unarchived":
                    it["status"] = "active"
                    it["last_confirmed"] = e["at"]
                    it["provenance"].append({"at": e["at"], "by": e["approved_by"], "what": "unarchived"})
                elif t == "revised":
                    it["text"] = e["text"]
                    if "confidence" in e:
                        it["confidence"] = e["confidence"]
                    it["provenance"].append({"at": e["at"], "by": e["approved_by"], "what": "revised"})
            elif t == "non_transferable":
                nontransfer[e["signature"]] = {"signature": e["signature"], "reason": e["reason"], "projects": e.get("projects", []), "at": e["at"], "by": e["by"]}
        return items, nontransfer

    # reading ---------------------------------------------------------------------------------------------------
    def items(self, *, include_archived: bool = True) -> list[dict]:
        items, _ = self._replay()
        return [i for i in items.values() if include_archived or i["status"] != "archived"]

    def get(self, item_id_or_signature: str) -> dict | None:
        items, _ = self._replay()
        if item_id_or_signature in items:
            return items[item_id_or_signature]
        return next((i for i in items.values() if i["signature"] == item_id_or_signature), None)

    def non_transferable(self) -> list[dict]:
        return list(self._replay()[1].values())

    # writing ---------------------------------------------------------------------------------------------------
    def learn(self, signature: str, *, text: str, project_id: str, run_ids: Iterable[str] = (), approved_by: str, kind: str = "note",
              tags: Iterable[str] = (), confidence: float = 0.5, now: _dt.datetime | None = None) -> dict:
        """Record that an abstract pattern held in ``project_id``. The first call creates the item (scoped to that project); later
        calls for the same signature are confirmations (see :meth:`confirm`). ``text`` must not carry game-specific names."""
        who = _approver(approved_by)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        existing = self.get(signature)
        if existing is not None:
            return self.confirm(signature, project_id, run_ids=run_ids, approved_by=who, now=now)
        item_id = "kn-" + hashlib.sha256(signature.encode()).hexdigest()[:10]
        item = {"id": item_id, "signature": signature, "kind": kind, "text": redact(text), "tags": sorted(set(tags)), "confidence": confidence, "home_project": project_id,
                "provenance": [{"at": _iso(now), "by": who, "project_id": project_id, "run_ids": sorted(set(run_ids)), "what": "learned"}]}
        self._append({"event": "created", "at": _iso(now), "item": item})
        self.log_change(f"learned '{signature}' in project {project_id}", by=who, ref=item_id, version=1, provenance={"run_ids": sorted(set(run_ids)), "project_id": project_id}, now=now)
        return self.get(item_id)  # type: ignore[return-value]

    def confirm(self, signature: str, project_id: str, *, run_ids: Iterable[str] = (), approved_by: str, now: _dt.datetime | None = None) -> dict:
        who = _approver(approved_by)
        it = self.get(signature)
        if it is None:
            raise PromotionError(f"unknown item '{signature}': use learn() first")
        self._append({"event": "confirmed", "at": _iso(now), "id": it["id"], "project_id": project_id, "run_ids": sorted(set(run_ids)), "by": who})
        out = self.get(it["id"])
        self.log_change(f"confirmed '{it['signature']}' in project {project_id}", by=who, ref=it["id"], version=out["version"], provenance={"run_ids": sorted(set(run_ids)), "project_id": project_id}, now=now)
        return out  # type: ignore[return-value]

    def contradict(self, signature: str, project_id: str, *, note: str, run_ids: Iterable[str] = (), approved_by: str, now: _dt.datetime | None = None) -> dict:
        """Record evidence AGAINST an item. It stays visible in :meth:`contradictions` and blocks global promotion until a person resolves it."""
        who = _approver(approved_by)
        it = self.get(signature)
        if it is None:
            raise PromotionError(f"unknown item '{signature}'")
        self._append({"event": "contradicted", "at": _iso(now), "id": it["id"], "project_id": project_id, "note": redact(note), "run_ids": sorted(set(run_ids)), "by": who})
        out = self.get(it["id"])
        self.log_change(f"contradiction recorded for '{it['signature']}' in project {project_id}: {redact(note)}", by=who, ref=it["id"], version=out["version"], now=now)
        return out  # type: ignore[return-value]

    def mark_non_transferable(self, signature: str, *, reason: str, projects: Iterable[str] = (), approved_by: str, now: _dt.datetime | None = None) -> dict:
        """It held in one game and failed in another: never offer it for global promotion again."""
        who = _approver(approved_by)
        if not reason.strip():
            raise PromotionError("a reason is required")
        self._append({"event": "non_transferable", "at": _iso(now), "signature": signature, "reason": redact(reason), "projects": sorted(set(projects)), "by": who})
        self.log_change(f"'{signature}' marked non-transferable: {redact(reason)}", by=who, now=now)
        return self._replay()[1][signature]

    # generalisation ----------------------------------------------------------------------------------------------
    def candidate_report(self, k: int | None = None) -> list[dict]:
        """Every project-scoped item with whether it may be offered for global promotion and, if not, why."""
        k = k or self.k
        items, nt = self._replay()
        out = []
        for it in items.values():
            if it["scope"] == "global":
                continue
            projects = sorted(it["confirmed_in"])
            reasons = []
            if it["status"] != "active":
                reasons.append(f"item is {it['status']}")
            if len(projects) < k:
                reasons.append(f"confirmed in {len(projects)} distinct project(s), needs {k}")
            if it["contradicted_in"]:
                reasons.append("contradicting evidence in " + ", ".join(sorted(it["contradicted_in"])))
            if it["signature"] in nt:
                reasons.append("on the known-non-transferable list: " + nt[it["signature"]]["reason"])
            out.append({"id": it["id"], "signature": it["signature"], "projects": projects, "eligible": not reasons, "blocked_by": reasons})
        return out

    def global_candidates(self, k: int | None = None) -> list[dict]:
        """Items that may be OFFERED to the user for global promotion (confirmed in >= k distinct projects, no contradiction)."""
        return [c for c in self.candidate_report(k) if c["eligible"]]

    def promote_global(self, signature: str, *, approved_by: str, confirm: bool = False, now: _dt.datetime | None = None) -> dict:
        who = _approver(approved_by)
        _need_confirm(confirm, "promoting to global")
        it = self.get(signature)
        if it is None:
            raise PromotionError(f"unknown item '{signature}'")
        cand = next((c for c in self.candidate_report() if c["id"] == it["id"]), None)
        if cand is None:
            raise PromotionError(f"'{it['signature']}' is already global")
        if not cand["eligible"]:
            raise PromotionError(f"'{it['signature']}' cannot be promoted to global: " + "; ".join(cand["blocked_by"]))
        self._append({"event": "promoted_global", "at": _iso(now), "id": it["id"], "approved_by": who, "projects": cand["projects"]})
        out = self.get(it["id"])
        self.log_change(f"promoted '{it['signature']}' to global (confirmed in {', '.join(cand['projects'])})", by=who, ref=it["id"], version=out["version"],
                        provenance={"projects": cand["projects"]}, now=now)
        return out  # type: ignore[return-value]

    def demote_global(self, signature: str, *, approved_by: str, reason: str, confirm: bool = False, now: _dt.datetime | None = None) -> dict:
        """Rollback of a global promotion (the item returns to project scope; history is kept)."""
        who = _approver(approved_by)
        _need_confirm(confirm, "rolling back a global promotion")
        it = self.get(signature)
        if it is None or it["scope"] != "global":
            raise PromotionError(f"'{signature}' is not a global item")
        self._append({"event": "demoted_global", "at": _iso(now), "id": it["id"], "approved_by": who, "reason": reason})
        out = self.get(it["id"])
        self.log_change(f"rolled back global promotion of '{it['signature']}': {reason}", by=who, ref=it["id"], version=out["version"], now=now)
        return out  # type: ignore[return-value]

    # staleness -----------------------------------------------------------------------------------------------------
    def is_stale(self, item: dict, now: _dt.datetime | None = None, max_age_days: int | None = None) -> bool:
        age = (_now(now) - _dt.datetime.fromisoformat(item["last_confirmed"])).days
        return age > (max_age_days if max_age_days is not None else self.max_age_days)

    def effective_confidence(self, item: dict, now: _dt.datetime | None = None, max_age_days: int | None = None) -> float:
        """Confidence decayed linearly to 0 at ``max_age_days`` since last confirmation."""
        limit = max_age_days if max_age_days is not None else self.max_age_days
        age = max(0, (_now(now) - _dt.datetime.fromisoformat(item["last_confirmed"])).days)
        return round(item["confidence"] * max(0.0, 1 - age / limit), 4)

    def retrievable(self, scope: Scope | None = None, *, now: _dt.datetime | None = None, max_age_days: int | None = None) -> list[dict]:
        """Items a tool may show: active, not stale, and visible in ``scope`` (home project, or global). Archived/stale ones are left out, not deleted."""
        out = []
        for it in self.items(include_archived=False):
            if self.is_stale(it, now, max_age_days):
                continue
            if scope is not None and not scope.is_global and it["scope"] != "global" and it["home_project"] != scope.project_id:
                continue
            if scope is not None and scope.is_global and it["scope"] != "global":
                continue
            out.append({**it, "effective_confidence": self.effective_confidence(it, now, max_age_days)})
        return out

    def archive_stale(self, *, now: _dt.datetime | None = None, max_age_days: int | None = None) -> list[str]:
        """Record ``archived`` for every active item past its age limit. Nothing is deleted; a new confirmation revives the item."""
        done = []
        for it in self.items(include_archived=False):
            if self.is_stale(it, now, max_age_days):
                self._append({"event": "archived", "at": _iso(now), "id": it["id"], "reason": f"not confirmed since {it['last_confirmed'][:10]}", "by": "staleness"})
                self.log_change(f"archived '{it['signature']}' (not confirmed since {it['last_confirmed'][:10]})", by="staleness", ref=it["id"], now=now)
                done.append(it["id"])
        return done

    def unarchive(self, signature: str, *, approved_by: str, now: _dt.datetime | None = None) -> dict:
        who = _approver(approved_by)
        it = self.get(signature)
        if it is None or it["status"] != "archived":
            raise PromotionError(f"'{signature}' is not archived")
        self._append({"event": "unarchived", "at": _iso(now), "id": it["id"], "approved_by": who})
        self.log_change(f"unarchived '{it['signature']}'", by=who, ref=it["id"], now=now)
        return self.get(it["id"])  # type: ignore[return-value]

    def contradictions(self) -> list[dict]:
        """Items with evidence against them, with the evidence. Surfaced for a person to resolve."""
        return [{"id": i["id"], "signature": i["signature"], "confirmed_in": sorted(i["confirmed_in"]), "contradicted_in": i["contradicted_in"]}
                for i in self.items() if i["contradicted_in"]]


def promote_proposal(proposals: ProposalStore, proposal_id: str, *, params: ParamStore, knowledge: KnowledgeStore, approved_by: str, confirm: bool = False,
                     now: _dt.datetime | None = None) -> dict:
    """Apply ONE approved, gate-passed proposal. Refuses unless: the gate passed, a person approved, ``confirm=True``.

    ``param`` proposals become a new parameter version in the proposal's scope. ``rule``/``synonym`` proposals are recorded as
    approved diffs (status ``approved``) for the person to apply; guide-core never edits a project's files. Global-scope proposals
    are refused here: they go through :meth:`KnowledgeStore.promote_global`.
    """
    who = _approver(approved_by)
    _need_confirm(confirm, "promoting a proposal")
    prop = proposals.get(proposal_id)
    if prop is None:
        raise PromotionError(f"unknown proposal '{proposal_id}'")
    if prop["status"] != "gate_passed":
        raise PromotionError(f"proposal {proposal_id} is '{prop['status']}': only a proposal that passed the eval gate can be promoted"
                             + (" (it was rejected automatically)" if prop["status"] == "gate_rejected" else ""))
    scope = Scope(prop["scope"]["project_id"], prop["scope"].get("place_id")) if not prop["scope"].get("global") else Scope.make_global()
    if scope.is_global:
        raise PromotionError("a global-scope change is made through KnowledgeStore.promote_global (confirmed in k projects), not here")
    change, ev = prop["change"], prop["evidence"]
    prov = {"proposal_id": proposal_id, "run_ids": ev["run_ids"], "project_id": scope.project_id, "place_id": scope.place_id}
    if prop["kind"] == "param":
        try:
            ver = params.update(change["param"], change["after"], scope, approved_by=who, reason=prop["rationale"], evidence=ev["run_ids"])
        except ParamError as exc:
            raise PromotionError(f"could not apply: {exc}") from exc
        line = f"parameter {change['param']} {change['before']!r} -> {change['after']!r} for {scope.label} (param version {ver['version']})"
        knowledge.log_change(line, by=who, ref=proposal_id, version=ver["version"], provenance=prov, now=now)
        proposals.set_status(proposal_id, "promoted", approved_by=who, param_version=ver["version"])
        return {"status": "promoted", "applied": line, "param_version": ver["version"], "provenance": prov}
    knowledge.log_change(f"approved {prop['kind']} diff for {scope.label}: {json.dumps(change, sort_keys=True, default=str)} (apply it yourself; not applied by guide-core)",
                         by=who, ref=proposal_id, provenance=prov, now=now)
    proposals.set_status(proposal_id, "approved", approved_by=who, note="diff recorded; a person applies it to the file")
    return {"status": "approved", "applied": None, "diff": change, "provenance": prov, "note": "guide-core never edits project files: apply this diff yourself"}


def reject_proposal(proposals: ProposalStore, proposal_id: str, *, rejected_by: str, reason: str) -> dict:
    """The user said no. The proposal is kept (never deleted) with the reason."""
    who = _approver(rejected_by)
    return proposals.set_status(proposal_id, "rejected", rejected_by=who, reason=redact(reason))


def monitor_change(log: RunLog, scope: Scope | None, change_at: str, *, n: int = 10, worse_override_rate: float = 0.1, worse_size_ratio: float = 1.25,
                   worse_time_ratio: float = 1.5) -> dict:
    """Step 6: compare the next ``n`` runs after ``change_at`` with the ``n`` before it (override rate, output size, time).
    ``worse`` only suggests a rollback; this function changes nothing."""
    runs = log.runs(scope)
    before = [r for r in runs if r["at"] < change_at][-n:]
    after = [r for r in runs if r["at"] >= change_at][:n]
    if len(before) < max(3, n // 2) or len(after) < max(3, n // 2):
        return {"status": "insufficient_data", "before_runs": len(before), "after_runs": len(after), "worse": False}

    def stats(rs: list[dict]) -> dict:
        acted = [r for r in rs if r.get("user_action")]
        sizes = [r["output_chars"] for r in rs if r.get("output_chars") is not None]
        secs = [r["seconds"] for r in rs if r.get("seconds") is not None]
        return {"override_rate": (sum(r["user_action"] in ("reject", "edit") for r in acted) / len(acted)) if acted else None,
                "mean_chars": sum(sizes) / len(sizes) if sizes else None, "mean_seconds": sum(secs) / len(secs) if secs else None}

    b, a = stats(before), stats(after)
    flags = []
    if b["override_rate"] is not None and a["override_rate"] is not None and a["override_rate"] - b["override_rate"] > worse_override_rate:
        flags.append(f"override rate {b['override_rate']:.2f} -> {a['override_rate']:.2f}")
    if b["mean_chars"] and a["mean_chars"] and a["mean_chars"] / b["mean_chars"] > worse_size_ratio:
        flags.append(f"output size {b['mean_chars']:.0f} -> {a['mean_chars']:.0f} chars")
    if b["mean_seconds"] and a["mean_seconds"] and a["mean_seconds"] / b["mean_seconds"] > worse_time_ratio:
        flags.append(f"time {b['mean_seconds']:.3f}s -> {a['mean_seconds']:.3f}s")
    return {"status": "ok", "before": b, "after": a, "worse": bool(flags), "flags": flags,
            "advice": "got worse: offer a rollback to the user (ParamStore.rollback); it is not done automatically" if flags else "no regression seen in these runs"}
