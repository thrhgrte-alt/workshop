"""Generation records: everything needed to reproduce, compare and learn from one generation run.

Layout under ``workspace/generations/<gen_id>/``: ``run.json`` plus the output images. ``run.json`` stores the brief, the adapter and declared
model, the exact prompts, settings and seeds, the reference ids, every output with its sha256 and measurements, and the feedback attached
later. Records are written only when a tool is called with ``dry_run=false``. Feedback is attached only when explicitly given.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import uuid
from pathlib import Path

SCHEMA = "conceptai-run/1"
ID_RE = re.compile(r"^gen-\d{8}-\d{6}-[0-9a-f]{6}$")
DECISIONS = ("accept", "reject", "revise")


def runs_dir(project) -> Path:
    return project.workspace / "generations"


def new_id() -> str:
    return f"gen-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def run_dir(project, gen_id: str) -> Path:
    if not ID_RE.match(gen_id):
        raise ValueError(f"'{gen_id}' is not a generation id (expected gen-YYYYMMDD-HHMMSS-xxxxxx)")
    return runs_dir(project) / gen_id


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load(project, gen_id: str) -> dict:
    p = run_dir(project, gen_id) / "run.json"
    if not p.exists():
        raise ValueError(f"no generation run '{gen_id}'")
    return json.loads(p.read_text(encoding="utf-8"))


def save(project, record: dict) -> Path:
    p = run_dir(project, record["gen_id"]) / "run.json"
    _write(p, record)
    return p


def make_record(gen_id: str, *, brief: dict, adapter: dict, directions: list[dict], requests: list[dict], outputs: list[dict], reference_ids: list[str],
                core_run_id: str | None) -> dict:
    return {"schema": SCHEMA, "gen_id": gen_id, "created": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "status": "concept_only",
            "brief": brief, "adapter": adapter, "directions": directions, "requests": requests, "outputs": outputs, "reference_ids": reference_ids,
            "feedback": [], "core_run_id": core_run_id,
            "note": "Concept images only. Nothing here is a mesh, a Substance graph or a Roblox scene."}


def add_feedback(record: dict, *, output_id: str, decision: str, scores: dict | None, notes: str, corrections: list[dict]) -> dict:
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if output_id not in {o["output_id"] for o in record["outputs"]}:
        raise ValueError(f"unknown output '{output_id}' (outputs: {[o['output_id'] for o in record['outputs']]})")
    row = {"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "output_id": output_id, "decision": decision, "scores": scores or {}, "notes": notes,
           "corrections": corrections, "subjective": True}
    record["feedback"].append(row)
    return row


def list_runs(project, limit: int = 20) -> list[dict]:
    base = runs_dir(project)
    if not base.exists():
        return []
    rows = []
    for d in sorted(base.iterdir(), reverse=True):
        f = d / "run.json"
        if f.exists() and ID_RE.match(d.name):
            r = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"gen_id": r["gen_id"], "created": r["created"], "subject": r["brief"].get("subject"), "kind": r["brief"].get("kind"), "adapter": r["adapter"]["name"],
                         "outputs": len(r["outputs"]), "feedback": len(r["feedback"])})
        if len(rows) >= limit:
            break
    return rows
