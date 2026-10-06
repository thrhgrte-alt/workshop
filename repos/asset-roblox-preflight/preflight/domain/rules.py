"""Load and validate rules/*.yaml and rules/profiles/*.yaml; resolve limits (call override > saved override > profile > rule default)."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SEVERITIES = ("error", "warn", "info")
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
PROFILE_NAMES = ("prop", "tool", "character_accessory", "terrain_piece")
CATEGORIES = ("mesh", "transform", "names", "uv", "materials", "rig", "collision", "upload")


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(template: str, **values: Any) -> str:
    return template.format_map(_Safe({k: _fmt(v) for k, v in values.items()}))


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


def validate_rule(rule: dict, category: str) -> list[str]:
    problems = []
    rid = rule.get("id", "<no id>")
    for key in ("id", "severity", "title", "explanation", "fix", "safe"):
        if key not in rule:
            problems.append(f"{rid}: missing '{key}'")
    if rule.get("severity") not in SEVERITIES:
        problems.append(f"{rid}: severity must be one of {SEVERITIES}")
    if not isinstance(rule.get("safe"), bool):
        problems.append(f"{rid}: 'safe' must be true or false")
    if not re.match(r"^[A-Z][A-Z0-9_]+$", str(rule.get("id", ""))):
        problems.append(f"{rid}: id must be UPPER_SNAKE_CASE")
    for key, lim in (rule.get("limits") or {}).items():
        if not isinstance(lim, dict) or "default" not in lim or "verify_against_current_docs" not in lim:
            problems.append(f"{rid}.{key}: a limit needs 'default' and 'verify_against_current_docs'")
        elif lim["verify_against_current_docs"] and not lim.get("note", "DEFAULT").upper().count("DEFAULT"):
            problems.append(f"{rid}.{key}: a limit that must be verified should say in 'note' that it is a DEFAULT")
    for p in rule.get("applies_to") or []:
        if p not in PROFILE_NAMES:
            problems.append(f"{rid}: applies_to names unknown profile '{p}'")
    return problems


def load_rules(rules_dir: Path) -> dict[str, dict]:
    rules: dict[str, dict] = {}
    problems: list[str] = []
    for f in sorted(Path(rules_dir).glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cat = data.get("category")
        if cat not in CATEGORIES:
            problems.append(f"{f.name}: category must be one of {CATEGORIES}")
            continue
        for r in data.get("rules", []):
            problems += validate_rule(r, cat)
            r = dict(r)
            r["category"], r["file"] = cat, f.name
            r.setdefault("enabled", True)
            r.setdefault("limits", {})
            if r.get("id") in rules:
                problems.append(f"{r['id']}: duplicate rule id")
            rules[r["id"]] = r
    if problems:
        raise ValueError("rules are invalid: " + "; ".join(problems))
    if not rules:
        raise ValueError(f"no rules found in {rules_dir}")
    return rules


def _validate_profile_parts(p: dict, rules: dict[str, dict], label: str) -> None:
    for rid, limits in (p.get("limits") or {}).items():
        if rid not in rules:
            raise ValueError(f"{label}: limits for unknown rule '{rid}'")
        for key in limits:
            if key not in rules[rid]["limits"]:
                raise ValueError(f"{label}: rule {rid} has no limit '{key}' (has {sorted(rules[rid]['limits'])})")
    for rid, sev in (p.get("severity") or {}).items():
        if rid not in rules or sev not in SEVERITIES:
            raise ValueError(f"{label}: bad severity override {rid}: {sev}")
    for rid in p.get("disabled_rules") or []:
        if rid not in rules:
            raise ValueError(f"{label}: disabled_rules names unknown rule '{rid}'")


def apply_project_layer(profiles: dict[str, dict], style: dict, doc: dict, rules: dict[str, dict], label: str) -> tuple[dict[str, dict], dict]:
    """Layer a project's profiles.yaml over the global profiles and style (returns new dicts)."""
    import copy

    profiles, style = copy.deepcopy(profiles), copy.deepcopy(style)
    for name, layer in (doc.get("profiles") or {}).items():
        if name not in profiles:
            raise ValueError(f"{label}: unknown profile '{name}' (profiles: {list(PROFILE_NAMES)})")
        _validate_profile_parts(layer, rules, f"{label} profile {name}")
        base = profiles[name]
        for rid, lims in (layer.get("limits") or {}).items():
            base.setdefault("limits", {}).setdefault(rid, {}).update(lims)
        base["settings"].update(layer.get("settings") or {})
        base["disabled_rules"] = sorted(set(base.get("disabled_rules") or []) | set(layer.get("disabled_rules") or []))
        base.setdefault("severity", {}).update(layer.get("severity") or {})
    naming = doc.get("naming") or {}
    if naming:
        n = style.setdefault("naming", {})
        n.setdefault("mesh_patterns", {}).update(naming.get("mesh_patterns") or {})
        for k in ("collision_pattern", "bone_pattern"):
            if k in naming:
                n[k] = naming[k]
    return profiles, style


def load_profiles(profile_dir: Path, rules: dict[str, dict]) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for f in sorted(Path(profile_dir).glob("*.yaml")):
        p = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        name = p.get("name")
        if name not in PROFILE_NAMES:
            raise ValueError(f"{f.name}: profile name must be one of {PROFILE_NAMES}")
        if not p.get("limits_are_defaults"):
            raise ValueError(f"{f.name}: a profile must say 'limits_are_defaults: true' until a person has replaced the numbers with verified ones")
        _validate_profile_parts(p, rules, f.name)
        p.setdefault("settings", {})
        p["settings"].setdefault("studs_per_unit", 1.0)
        p["file"] = f.name
        profiles[name] = p
    missing = set(PROFILE_NAMES) - set(profiles)
    if missing:
        raise ValueError(f"missing profile file(s): {sorted(missing)}")
    return profiles


@dataclass
class Config:
    rules: dict[str, dict]
    profiles: dict[str, dict]
    style: dict
    project_id: str | None = None


def matches_glob(name: str | None, pattern: str | None) -> bool:
    return not pattern or fnmatch.fnmatch((name or "").lower(), pattern.lower())


@dataclass
class Overrides:
    """Per-call limit overrides plus saved user overrides (already filtered to this profile)."""

    call: dict[str, dict] = field(default_factory=dict)   # {rule_id: {limit_key: value}}
    saved: list[dict] = field(default_factory=list)       # rows from feedback/overrides.jsonl (profile-scoped)
    asset_name: str | None = None

    def lookup(self, rule_id: str, key: str):
        """Return (found, value, source) for a numeric/list limit override."""
        if key in (self.call.get(rule_id) or {}):
            return True, self.call[rule_id][key], "call"
        for row in reversed(self.saved):
            if row["rule"] == rule_id and row.get("limit_key") == key and not row.get("waive") and matches_glob(self.asset_name, row.get("asset_glob")):
                return True, row["value"], row
        return False, None, None

    def waiver(self, rule_id: str) -> dict | None:
        for row in reversed(self.saved):
            if row["rule"] == rule_id and row.get("waive") and matches_glob(self.asset_name, row.get("asset_glob")):
                return row
        return None
