"""The project ruleset: severity overrides, disabled rules and documented suppressions (each with a reason).

A suppression never hides a finding silently: suppressed findings are listed in the report with their reason. Matching is by rule id, optional
file glob and either a fingerprint of the offending line (survives line shifts) or an exact line number.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path

import yaml

RULESET_VERSION = 1
MIN_REASON = 8
HEADER = ("# luau-reviewer project ruleset. Suppressions need a reason; suppressed findings are still listed in every report.\n"
          "# Edited by the suppress_finding tool; you can also edit it by hand.\n")


def line_fingerprint(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", text.strip()).encode()).hexdigest()[:10]


def empty() -> dict:
    return {"version": RULESET_VERSION, "disabled_rules": [], "severity_overrides": {}, "suppressions": []}


def load(path: Path | str | None) -> dict:
    if not path:
        return empty()
    p = Path(path)
    if not p.exists():
        return empty()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{p}: ruleset is not valid YAML ({exc})") from exc
    problems = validate(data)
    if problems:
        raise ValueError(f"{p}: " + "; ".join(problems))
    rs = empty()
    rs.update({k: data[k] for k in data if k in rs})
    return rs


def validate(data: dict, known_rules: set[str] | None = None) -> list[str]:
    problems = []
    if not isinstance(data, dict):
        return ["the ruleset must be a mapping"]
    for s in data.get("suppressions", []) or []:
        if not isinstance(s, dict) or not s.get("rule"):
            problems.append("every suppression needs a 'rule'")
            continue
        if len(str(s.get("reason", "")).strip()) < MIN_REASON:
            problems.append(f"suppression of {s['rule']} needs a reason of at least {MIN_REASON} characters")
        if known_rules is not None and s["rule"] not in known_rules and ":" not in s["rule"]:
            problems.append(f"unknown rule id '{s['rule']}'")
    sev = data.get("severity_overrides", {}) or {}
    for rid, v in sev.items():
        if v not in ("error", "warning", "info", "off"):
            problems.append(f"severity override for {rid} must be error, warning, info or off")
    return problems


def matches(s: dict, finding: dict) -> bool:
    if s["rule"] != finding["rule_id"]:
        return False
    pat = s.get("file")
    if pat and not (fnmatch.fnmatch(finding["file"], pat) or fnmatch.fnmatch(Path(finding["file"]).name, pat) or finding["file"].endswith(pat)):
        return False
    if s.get("fingerprint"):
        return s["fingerprint"] == finding.get("line_fingerprint")
    if s.get("line"):
        return int(s["line"]) == finding["line"]
    return True


def merge(layers: list[tuple[str, str | None, dict]]) -> dict:
    """Combine rulesets, later layers winning for severity overrides. Every suppression remembers the layer it came from."""
    out = empty()
    out["layers"] = []
    out["disabled_layers"] = {}
    for name, path, rs in layers:
        out["layers"].append({"layer": name, "path": path, "suppressions": len(rs.get("suppressions", [])), "disabled_rules": list(rs.get("disabled_rules", [])),
                              "severity_overrides": dict(rs.get("severity_overrides", {}))})
        for r in rs.get("disabled_rules") or []:
            if r not in out["disabled_rules"]:
                out["disabled_rules"].append(r)
                out["disabled_layers"][r] = name
        out["severity_overrides"].update(rs.get("severity_overrides") or {})
        out["suppressions"] += [{**s, "layer": name} for s in rs.get("suppressions") or []]
    return out


def apply(findings: list[dict], rs: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Returns (kept, suppressed, dropped_by_disable, usage). Severity overrides are applied to the kept findings.
    `usage` has one row per suppression: how many findings it matched (0 = it matched nothing: visible, not silently ignored)."""
    disabled = set(rs.get("disabled_rules") or [])
    overrides = rs.get("severity_overrides") or {}
    sups = rs.get("suppressions") or []
    counts = [0] * len(sups)
    kept, suppressed, dropped = [], [], []
    for f in findings:
        if f["rule_id"] in disabled or overrides.get(f["rule_id"]) == "off":
            dropped.append(f)
            continue
        idx = next((i for i, s in enumerate(sups) if matches(s, f)), None)
        if idx is not None:
            counts[idx] += 1
            suppressed.append({**f, "suppressed_reason": sups[idx]["reason"], "suppressed_by": sups[idx].get("layer", "ruleset")})
            continue
        if f["rule_id"] in overrides:
            f["severity_original"] = f["severity"]
            f["severity"] = overrides[f["rule_id"]]
        kept.append(f)
    usage = [{"rule": s["rule"], "file": s.get("file"), "line": s.get("line"), "layer": s.get("layer", "ruleset"), "reason": s["reason"], "matched": counts[i]} for i, s in enumerate(sups)]
    return kept, suppressed, dropped, usage


def add_suppression(rs: dict, entry: dict) -> dict:
    new = {**rs, "suppressions": [*rs.get("suppressions", []), entry]}
    return new


def dump(rs: dict) -> str:
    return HEADER + yaml.safe_dump(rs, sort_keys=False, allow_unicode=True, width=120)
