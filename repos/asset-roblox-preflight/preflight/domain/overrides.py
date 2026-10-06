"""User overrides ("this prop is allowed to be 30k tris") saved as decisions scoped to an asset type, and promotion suggestions.

Stored as JSON lines in <workspace>/feedback/overrides.jsonl. Nothing here edits rules/ or profiles: a promotion is only ever a suggested patch.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from typing import Any

from ..guide_adapter import Project, read_jsonl
from .rules import PROFILE_NAMES, Config, matches_glob

MIN_REPEATS = 3


def overrides_file(project: Project) -> Path:
    return project.feedback_dir / "overrides.jsonl"


def load_saved(project: Project, profile: str | None = None) -> list[dict]:
    rows = read_jsonl(overrides_file(project))
    return [r for r in rows if profile is None or r.get("asset_type") == profile]


def validate_override(cfg: Config, *, rule: str, asset_type: str, reason: str, value: Any, limit_key: str | None, waive: bool) -> None:
    if rule not in cfg.rules:
        raise ValueError(f"unknown rule '{rule}'. Known rules: {sorted(cfg.rules)[:8]} ... (see list_profiles with include_rules=true)")
    if asset_type not in PROFILE_NAMES:
        raise ValueError(f"asset_type must be one of {list(PROFILE_NAMES)}, got '{asset_type}'")
    if not reason or not reason.strip():
        raise ValueError("an override needs a reason: pass the user's own words (why this asset may break the rule)")
    r = cfg.rules[rule]
    if r.get("applies_to") and asset_type not in r["applies_to"]:
        raise ValueError(f"rule {rule} does not apply to the {asset_type} profile (applies to {r['applies_to']})")
    if waive:
        if value is not None or limit_key:
            raise ValueError("a waiver (waive=true) takes no value or limit_key; to change a number pass limit_key and value instead")
        return
    if not r["limits"]:
        raise ValueError(f"rule {rule} has no numeric limit to change; use waive=true to waive the rule for this asset type")
    if limit_key is None:
        if len(r["limits"]) == 1:
            limit_key = next(iter(r["limits"]))
        else:
            raise ValueError(f"rule {rule} has several limits {sorted(r['limits'])}; say which with limit_key")
    if limit_key not in r["limits"]:
        raise ValueError(f"rule {rule} has no limit '{limit_key}'. Limits: {sorted(r['limits'])}")
    if value is None:
        raise ValueError("pass the new value (for example value=30000), or waive=true")
    default = r["limits"][limit_key]["default"]
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, (int, float)):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif isinstance(default, list):
        ok = isinstance(value, list)
    else:
        ok = isinstance(value, str)
    if not ok:
        raise ValueError(f"limit {rule}.{limit_key} expects a {type(default).__name__}, got {type(value).__name__}")


def normalise(cfg: Config, rule: str, limit_key: str | None, waive: bool) -> str | None:
    if waive or limit_key:
        return limit_key
    lims = cfg.rules[rule]["limits"]
    return next(iter(lims)) if len(lims) == 1 else limit_key


def make_row(*, rule: str, asset_type: str, reason: str, value: Any, limit_key: str | None, waive: bool, asset_glob: str | None, run_id: str | None, asset: str | None,
             project_id: str, is_global: bool = False) -> dict:
    return {"override_id": f"ovr-{_dt.datetime.now(_dt.timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}", "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "rule": rule, "asset_type": asset_type, "limit_key": limit_key, "value": value, "waive": waive, "asset_glob": asset_glob, "reason": reason.strip(), "run_id": run_id, "asset": asset, "project_id": project_id, "global": is_global}


def append(project: Project, row: dict) -> None:
    f = overrides_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def suggest(rows: list[dict], cfg: Config, min_count: int = MIN_REPEATS, asset_type: str | None = None) -> list[dict]:
    """Group repeated overrides and propose a patch to the project's profile (or, for global overrides, the global profile). Never writes."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if asset_type and r.get("asset_type") != asset_type:
            continue
        key = (r.get("project_id"), bool(r.get("global")), r["asset_type"], r["rule"], r.get("limit_key"), json.dumps(r.get("value")), bool(r.get("waive")))
        groups.setdefault(key, []).append(r)
    out = []
    for (pid, is_global, atype, rule, lkey, vjson, waive), grp in groups.items():
        if len(grp) < min_count:
            continue
        assets = sorted({r.get("asset") or r.get("asset_glob") or r.get("run_id") or r["override_id"] for r in grp})
        value = json.loads(vjson)
        prof = cfg.profiles.get(atype, {})  # already layered with the project's profile, so "already there" is judged per project
        target = f"rules/profiles/{atype}.yaml" if is_global else f"projects/{pid}/profiles.yaml"
        if waive:
            current = rule in (prof.get("disabled_rules") or [])
            body = f"disabled_rules: [{rule}]   # add to the existing list"
            patch = f"# {target}\n" + (body if is_global else f"profiles:\n  {atype}:\n    {body}")
        else:
            current = (prof.get("limits") or {}).get(rule, {}).get(lkey) == value
            if is_global:
                patch = f"# {target}\nlimits:\n  {rule}: {{{lkey}: {json.dumps(value)}}}"
            else:
                patch = f"# {target}\nprofiles:\n  {atype}:\n    limits:\n      {rule}: {{{lkey}: {json.dumps(value)}}}"
        where = "every project" if is_global else f"project {pid}"
        out.append({"project_id": pid, "global": is_global, "asset_type": atype, "rule": rule, "limit_key": lkey, "value": value, "waive": waive, "times_overridden": len(grp),
                    "distinct_assets": len(assets), "assets": assets[:10], "reasons": [r["reason"] for r in grp][:3], "already_in_profile": current,
                    "target_file": target, "suggested_patch": None if current else patch,
                    "offer": None if current else f"Recorded {len(grp)} times for {atype} assets in {where}. Promote it into {target}? (Edit it yourself or ask the user; this tool never edits profiles.)"})
    out.sort(key=lambda x: -x["times_overridden"])
    return out
