"""Structured feedback: runs, decisions, corrections, and explicit curation.

Nothing here changes a model. It writes plain JSONL files that retrieval can
load into a future prompt. AI outputs only enter the style library through
``promote_run`` with ``confirm=True`` - a deliberate curation step.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Any

from .manifest import LibraryStore, new_asset_id, read_jsonl, write_jsonl_atomic
from .project import Project
from .retrieval import bm25_scores, tokens

DECISIONS = ("accept", "reject", "revise")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _runs_file(project: Project):
    return project.feedback_dir / "runs.jsonl"


def _decisions_file(project: Project):
    return project.feedback_dir / "decisions.jsonl"


def _append(path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def record_run(
    project: Project,
    *,
    request: str,
    constraints: dict | None = None,
    retrieved: list[str] | None = None,
    tools: list[str] | None = None,
    recipes: list[str] | None = None,
    outputs: list[str] | None = None,
    preview: str | None = None,
    model: str | None = None,
    extra: dict | None = None,
) -> str:
    if not request.strip():
        raise ValueError("request must not be empty")
    run_id = f"run-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    _append(
        _runs_file(project),
        {
            "run_id": run_id,
            "at": _now(),
            "request": request,
            "constraints": constraints or {},
            "retrieved": retrieved or [],
            "tools": tools or [],
            "recipes": recipes or [],
            "outputs": outputs or [],
            "preview": preview,
            "model": model,
            "extra": extra or {},
        },
    )
    return run_id


def get_run(project: Project, run_id: str) -> dict | None:
    return next((r for r in read_jsonl(_runs_file(project)) if r["run_id"] == run_id), None)


def list_runs(project: Project, limit: int = 20) -> list[dict]:
    return read_jsonl(_runs_file(project))[-limit:]


def record_decision(
    project: Project,
    run_id: str,
    decision: str,
    *,
    reason: str = "",
    corrections: list[dict] | None = None,
    rating: int | None = None,
    promote_requested: bool = False,
    allowed_dimensions: list[str] | None = None,
) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if get_run(project, run_id) is None:
        raise ValueError(f"unknown run_id '{run_id}'")
    if decision != "accept" and not (reason or corrections):
        raise ValueError("a reject/revise decision needs a reason or at least one correction")
    if rating is not None and not 0 <= rating <= 5:
        raise ValueError("rating must be 0-5")
    corrections = corrections or []
    for c in corrections:
        if not c.get("dimension") or not c.get("note"):
            raise ValueError("each correction needs 'dimension' and 'note'")
        if allowed_dimensions and c["dimension"] not in allowed_dimensions:
            raise ValueError(f"unknown correction dimension '{c['dimension']}'. Allowed: {allowed_dimensions}")
    row = {
        "run_id": run_id,
        "at": _now(),
        "decision": decision,
        "reason": reason,
        "corrections": corrections,
        "rating": rating,
        "promote_requested": promote_requested,
    }
    _append(_decisions_file(project), row)
    return row


def decisions_for(project: Project, run_id: str) -> list[dict]:
    return [d for d in read_jsonl(_decisions_file(project)) if d["run_id"] == run_id]


def corrections_for(project: Project, query: str, k: int = 5) -> list[dict]:
    """Past user corrections most relevant to a new request (BM25 over request+reason+notes)."""
    runs = {r["run_id"]: r for r in read_jsonl(_runs_file(project))}
    rows = [d for d in read_jsonl(_decisions_file(project)) if d["decision"] != "accept" or d["corrections"]]
    docs = []
    for d in rows:
        run = runs.get(d["run_id"], {})
        text = " ".join([run.get("request", ""), d["reason"]] + [f"{c['dimension']} {c['note']}" for c in d["corrections"]])
        docs.append(tokens(text))
    scores = bm25_scores(query, docs)
    ranked = sorted(zip(scores, rows), key=lambda t: -t[0])
    out = []
    for score, d in ranked[:k]:
        if score <= 0:
            continue
        run = runs.get(d["run_id"], {})
        out.append({"run_id": d["run_id"], "request": run.get("request"), "decision": d["decision"],
                    "reason": d["reason"], "corrections": d["corrections"], "score": round(score, 3)})
    return out


def promote_run(
    project: Project,
    run_id: str,
    *,
    kind: str,
    title: str,
    description: str,
    tags: list[str],
    path: str,
    owner: str = "user",
    polarity: str = "positive",
    confirm: bool = False,
    style: dict | None = None,
    domain: dict | None = None,
    store: LibraryStore | None = None,
) -> dict:
    """Turn a reviewed run into a library entry. ``confirm=False`` creates only a *candidate*."""
    run = get_run(project, run_id)
    if run is None:
        raise ValueError(f"unknown run_id '{run_id}'")
    decisions = decisions_for(project, run_id)
    if confirm and not any(d["decision"] == "accept" for d in decisions) and polarity == "positive":
        raise ValueError("refusing to curate a positive example that has no 'accept' decision")
    notes = [f"{c['dimension']}: {c['note']}" for d in decisions for c in d["corrections"]]
    last_rating = next((d["rating"] for d in reversed(decisions) if d.get("rating") is not None), None)
    asset: dict[str, Any] = {
        "id": new_asset_id(title, run_id),
        "kind": kind,
        "title": title,
        "description": description,
        "path": path,
        "preview": run.get("preview"),
        "license": {"owner": owner},
        "status": "curated" if confirm else "candidate",
        "polarity": polarity,
        "tags": sorted(set(tags)),
        "rating": last_rating,
        "correction_notes": notes,
        "provenance": {"origin": "ai-generated", "run_id": run_id, "added_at": _now()},
    }
    if style:
        asset["style"] = style
    if domain:
        asset["domain"] = domain
    return (store or LibraryStore(project)).upsert(asset)
