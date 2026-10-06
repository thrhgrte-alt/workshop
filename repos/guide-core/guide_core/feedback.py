"""Structured feedback: runs, decisions, corrections, and explicit curation.

Nothing here changes a model. It writes plain JSONL files (the source of truth) and keeps a small SQLite index beside
them for lookups; the index is derived data and is rebuilt from the JSONL whenever it is missing, stale or damaged.
AI outputs only enter the style library through ``promote_run`` with ``confirm=True`` - a deliberate curation step.

Layout, under ``project.feedback_dir``::

    runs.jsonl        one JSON object per run          (append-only)
    decisions.jsonl   one JSON object per decision     (append-only)
    index.sqlite      derived index, ``meta.schema_version`` = ``SCHEMA`` (see CHANGELOG.md for migrations)

Scope. Every function that writes accepts ``scope=`` (:class:`guide_core.scope.Scope`); the row then carries it.
``find_past_corrections(..., scope=s)`` returns corrections recorded for that project/place plus ``global`` ones, never another
project's. Without ``scope`` the legacy behaviour applies (everything in this workspace). ``strict_scope=True`` refuses a
missing scope (what tools that read or write project data should pass).
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .manifest import LibraryStore, new_asset_id
from .project import Project
from .retrieval import bm25_scores, tokens
from .scope import Scope, ScopeError, scope_from_dict

SCHEMA = 1
DECISIONS = ("accept", "reject", "revise")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _runs_file(project: Project) -> Path:
    return project.feedback_dir / "runs.jsonl"


def _decisions_file(project: Project) -> Path:
    return project.feedback_dir / "decisions.jsonl"


def _index_file(project: Project) -> Path:
    return project.feedback_dir / "index.sqlite"


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _need_scope(scope: Scope | None, strict: bool, what: str) -> None:
    if strict and scope is None:
        raise ScopeError(f"{what} needs a resolved scope (project_id, optional place_id): refusing to touch project data without one")


# --- SQLite index ------------------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs(seq INTEGER PRIMARY KEY, run_id TEXT UNIQUE, at TEXT, project_id TEXT, place_id TEXT, is_global INTEGER, row TEXT);
CREATE TABLE IF NOT EXISTS decisions(seq INTEGER PRIMARY KEY, run_id TEXT, at TEXT, decision TEXT, project_id TEXT, place_id TEXT,
                                     is_global INTEGER, has_corrections INTEGER, row TEXT);
CREATE INDEX IF NOT EXISTS decisions_run ON decisions(run_id);
CREATE INDEX IF NOT EXISTS decisions_scope ON decisions(project_id, place_id);
"""


def _scope_cols(row: dict) -> tuple[str | None, str | None, int]:
    s = row.get("scope") or {}
    return s.get("project_id"), s.get("place_id"), 1 if s.get("global") else 0


def _ingest(con: sqlite3.Connection, table: str, path: Path, from_byte: int) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        fh.seek(from_byte)
        data = fh.read()
    pos = from_byte
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):  # a half-written last line is picked up next time
            break
        pos += len(raw)
        text = raw.decode("utf-8").strip()
        if not text:
            continue
        row = json.loads(text)
        pid, plid, glob = _scope_cols(row)
        if table == "runs":
            con.execute("INSERT OR REPLACE INTO runs(run_id, at, project_id, place_id, is_global, row) VALUES (?,?,?,?,?,?)",
                        (row["run_id"], row.get("at"), pid, plid, glob, text))
        else:
            con.execute("INSERT INTO decisions(run_id, at, decision, project_id, place_id, is_global, has_corrections, row) VALUES (?,?,?,?,?,?,?,?)",
                        (row["run_id"], row.get("at"), row.get("decision"), pid, plid, glob, 1 if row.get("corrections") else 0, text))
    return pos


def _open_index(project: Project) -> sqlite3.Connection:
    """Open the index and bring it in step with the JSONL files (incrementally, or by a full rebuild)."""
    f = _index_file(project)
    if not _runs_file(project).exists() and not _decisions_file(project).exists():
        # Nothing recorded here: answer from an empty in-memory index. A read must not create files (or directories) in a workspace that holds no feedback.
        con = sqlite3.connect(":memory:")
        con.executescript(_DDL)
        con.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA),))
        return con
    f.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (0, 1):
        try:
            con = sqlite3.connect(f)
            con.executescript(_DDL)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            stale = meta.get("schema_version") != str(SCHEMA)
            sizes = {t: (p.stat().st_size if p.exists() else 0) for t, p in (("runs", _runs_file(project)), ("decisions", _decisions_file(project)))}
            done = {t: int(meta.get(f"{t}_bytes", 0)) for t in sizes}
            if stale or any(done[t] > sizes[t] for t in sizes):  # different schema, or a file was rewritten shorter: rebuild
                con.executescript("DELETE FROM runs; DELETE FROM decisions; DELETE FROM meta;")
                done = {t: 0 for t in sizes}
            for t, path in (("runs", _runs_file(project)), ("decisions", _decisions_file(project))):
                if done[t] != sizes[t]:
                    done[t] = _ingest(con, t, path, done[t])
                con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (f"{t}_bytes", str(done[t])))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA),))
            con.commit()
            return con
        except (sqlite3.DatabaseError, json.JSONDecodeError):
            try:
                con.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            if attempt == 1:
                raise
            f.unlink(missing_ok=True)  # damaged index: it is only a cache, rebuild from the JSONL
    raise RuntimeError("unreachable")


def rebuild_index(project: Project) -> dict:
    """Throw the index away and rebuild it from the JSONL files. Returns row counts."""
    _index_file(project).unlink(missing_ok=True)
    con = _open_index(project)
    try:
        return {"runs": con.execute("SELECT COUNT(*) FROM runs").fetchone()[0], "decisions": con.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
                "schema_version": SCHEMA}
    finally:
        con.close()


def index_info(project: Project) -> dict:
    con = _open_index(project)
    try:
        return {"schema_version": int(dict(con.execute("SELECT key, value FROM meta").fetchall())["schema_version"]), "path": str(_index_file(project))}
    finally:
        con.close()


# --- runs ----------------------------------------------------------------------------------------------
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
    scope: Scope | None = None,
    strict_scope: bool = False,
) -> str:
    if not request.strip():
        raise ValueError("request must not be empty")
    _need_scope(scope, strict_scope, "record_run")
    run_id = f"run-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    row = {
        "schema": SCHEMA,
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
    }
    if scope is not None:
        row["scope"] = scope.to_dict()
    _append(_runs_file(project), row)
    return run_id


def get_run(project: Project, run_id: str) -> dict | None:
    con = _open_index(project)
    try:
        hit = con.execute("SELECT row FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(hit[0]) if hit else None
    finally:
        con.close()


def list_runs(project: Project, limit: int = 20) -> list[dict]:
    con = _open_index(project)
    try:
        rows = con.execute("SELECT row FROM runs ORDER BY seq DESC LIMIT ?", (max(0, limit),)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]
    finally:
        con.close()


# --- decisions -----------------------------------------------------------------------------------------
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
    scope: Scope | None = None,
    mark_global: bool = False,
    tags: list[str] | None = None,
    regression_case: dict | None = None,
    strict_scope: bool = False,
) -> dict:
    """Store the user's verdict. ``scope`` defaults to the run's scope; ``mark_global=True`` makes the correction apply everywhere
    (a deliberate, user-requested step). ``regression_case={"input": ..., "checks": [...]}`` makes an accepted correction executable
    as a regression case for the improvement gate (see :mod:`guide_core.gate`)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    run = get_run(project, run_id)
    if run is None:
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
    if regression_case is not None and not (isinstance(regression_case, dict) and regression_case.get("checks")):
        raise ValueError("regression_case must be a mapping with a non-empty 'checks' list (and optionally 'input')")
    eff = scope or scope_from_dict(run.get("scope"))
    _need_scope(eff, strict_scope, "record_decision")
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "at": _now(),
        "decision": decision,
        "reason": reason,
        "corrections": corrections,
        "rating": rating,
        "promote_requested": promote_requested,
    }
    if tags:
        row["tags"] = sorted({t.lower() for t in tags})
    if regression_case is not None:
        row["regression_case"] = regression_case
    if mark_global:
        row["scope"] = Scope.make_global().to_dict()
        if eff is not None and not eff.is_global:
            row["origin_scope"] = eff.to_dict()
    elif eff is not None:
        row["scope"] = eff.to_dict()
    _append(_decisions_file(project), row)
    return row


def decisions_for(project: Project, run_id: str) -> list[dict]:
    con = _open_index(project)
    try:
        return [json.loads(r[0]) for r in con.execute("SELECT row FROM decisions WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()]
    finally:
        con.close()


def _visible(row_scope: dict | None, scope: Scope | None, include_global: bool) -> bool:
    if scope is None:
        return True  # legacy: no scope asked for -> the whole workspace
    rs = scope_from_dict(row_scope)
    if rs is None:
        return False  # an unscoped legacy row is never shown to a scoped query: its owner is unknown
    if rs.is_global:
        return include_global
    return rs.covers(scope)


def find_past_corrections(project: Project, query: str, k: int = 5, *, scope: Scope | None = None, tags: list[str] | None = None,
                          include_global: bool = True, strict_scope: bool = False) -> list[dict]:
    """Past user corrections most relevant to a new request (BM25 over request + reason + notes).

    With ``scope`` only that project's (and place's) corrections and ``global`` ones are considered. With ``tags`` the text score
    is blended with tag overlap (0.7 text, 0.3 tags); without, ranking is plain BM25. Zero-score rows are never returned."""
    _need_scope(scope, strict_scope, "find_past_corrections")
    con = _open_index(project)
    try:
        run_rows = {r[0]: json.loads(r[1]) for r in con.execute("SELECT run_id, row FROM runs").fetchall()}
        dec_rows = [json.loads(r[0]) for r in con.execute(
            "SELECT row FROM decisions WHERE decision != 'accept' OR has_corrections = 1 ORDER BY seq").fetchall()]
    finally:
        con.close()
    rows = [d for d in dec_rows if _visible(d.get("scope"), scope, include_global)]
    docs = []
    for d in rows:
        run = run_rows.get(d["run_id"], {})
        docs.append(tokens(" ".join([run.get("request", ""), d["reason"]] + [f"{c['dimension']} {c['note']}" for c in d["corrections"]])))
    scores = bm25_scores(query, docs)
    if tags:
        want = {t.lower() for t in tags}
        top = max(scores) if scores and max(scores) > 0 else 1.0
        scores = [0.7 * (s / top) + 0.3 * (len(want & set(d.get("tags", []))) / len(want)) if (s > 0 or want & set(d.get("tags", []))) else 0.0
                  for s, d in zip(scores, rows)]
    ranked = sorted(zip(scores, rows), key=lambda t: -t[0])
    out = []
    for score, d in ranked[:k]:
        if score <= 0:
            continue
        run = run_rows.get(d["run_id"], {})
        item = {"run_id": d["run_id"], "request": run.get("request"), "decision": d["decision"],
                "reason": d["reason"], "corrections": d["corrections"], "score": round(score, 3)}
        if d.get("scope"):
            item["scope"] = d["scope"]
        out.append(item)
    return out


corrections_for = find_past_corrections  # name used by the vendored kit


def accepted_regression_cases(project: Project, scope: Scope | None = None) -> list[dict]:
    """Decisions that carry an executable ``regression_case`` and were accepted/revised by the user, newest last.
    Used by the improvement gate; each item is ``{"id", "run_id", "scope", "input", "checks"}``."""
    con = _open_index(project)
    try:
        rows = [json.loads(r[0]) for r in con.execute("SELECT row FROM decisions WHERE decision IN ('accept','revise') ORDER BY seq").fetchall()]
    finally:
        con.close()
    out = []
    for i, d in enumerate(rows):
        case = d.get("regression_case")
        if not case or not _visible(d.get("scope"), scope, True):
            continue
        out.append({"id": f"correction:{d['run_id']}:{i}", "run_id": d["run_id"], "scope": d.get("scope"), "input": case.get("input", {}), "checks": case["checks"]})
    return out


def recent_corrections(project: Project, scope: Scope | None = None, k: int = 5, include_global: bool = True) -> list[dict]:
    """The newest ``k`` decisions that carry corrections and are visible in ``scope`` (``Scope.make_global()`` = only global ones)."""
    con = _open_index(project)
    try:
        rows = [json.loads(r[0]) for r in con.execute("SELECT row FROM decisions WHERE has_corrections = 1 ORDER BY seq DESC").fetchall()]
        runs = {r[0]: json.loads(r[1]) for r in con.execute("SELECT run_id, row FROM runs").fetchall()}
    finally:
        con.close()
    out = []
    for d in rows:
        if scope is not None and scope.is_global:
            if not (d.get("scope") or {}).get("global"):
                continue
        elif not _visible(d.get("scope"), scope, include_global):
            continue
        out.append({"run_id": d["run_id"], "request": runs.get(d["run_id"], {}).get("request"), "decision": d["decision"], "reason": d["reason"],
                    "corrections": d["corrections"], "scope": d.get("scope"), "at": d["at"]})
        if len(out) >= k:
            break
    return out


def accepted_decisions(project: Project, scope: Scope | None = None) -> list[dict]:
    """Every accept/revise decision visible in ``scope`` (with or without an executable regression case), oldest first."""
    con = _open_index(project)
    try:
        rows = [json.loads(r[0]) for r in con.execute("SELECT row FROM decisions WHERE decision IN ('accept','revise') AND has_corrections = 1 ORDER BY seq").fetchall()]
    finally:
        con.close()
    return [d for d in rows if _visible(d.get("scope"), scope, True)]


# --- promotion to the library ------------------------------------------------------------------------------
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
    scope: Scope | None = None,
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
    if scope is not None and not scope.is_global:
        asset["project"] = scope.project_id
    if style:
        asset["style"] = style
    if domain:
        asset["domain"] = domain
    return (store or LibraryStore(project)).upsert(asset)
