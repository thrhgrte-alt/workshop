"""Rule definitions (rules/*.yaml) and the finding model.

Everything the reviewer judges is data in ``rules/*.yaml``: id, severity, confidence, explanation, example and fix. The detectors in
``detectors.py`` only decide WHERE a rule applies; severity, confidence, wording and numeric limits come from the YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SEVERITIES = ("error", "warning", "info")
CONFIDENCES = ("low", "medium", "high")  # ordered weakest to strongest
SEV_RANK = {"error": 0, "warning": 1, "info": 2}
REQUIRED_FIELDS = ("id", "title", "severity", "confidence", "problem", "explanation", "example", "fix")


@dataclass
class Rule:
    id: str
    category: str
    title: str
    severity: str
    confidence: str
    problem: str
    explanation: str
    example: str
    fix: str
    good: str = ""
    strict_only: bool = False
    supersedes: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    scope: str = "file"

    def to_dict(self, full: bool = True) -> dict:
        d = {"id": self.id, "category": self.category, "title": self.title, "severity": self.severity, "confidence": self.confidence,
             "strict_only": self.strict_only, "scope": self.scope, "problem": self.problem, "fix": self.fix}
        if full:
            d.update({"explanation": self.explanation.strip(), "example": self.example.rstrip(), "good": self.good.rstrip(),
                      "params": self.params, "supersedes": self.supersedes})
        return d


def rules_dir(root: Path) -> Path:
    return Path(root) / "rules"


def validate_rule_dict(r: dict, category: str, source: str) -> list[str]:
    problems = []
    for f in REQUIRED_FIELDS:
        if not r.get(f):
            problems.append(f"{source}: rule '{r.get('id', '?')}' is missing '{f}'")
    if r.get("severity") not in SEVERITIES:
        problems.append(f"{source}: rule '{r.get('id')}' severity must be one of {SEVERITIES}")
    if r.get("confidence") not in CONFIDENCES:
        problems.append(f"{source}: rule '{r.get('id')}' confidence must be one of {CONFIDENCES}")
    return problems


def load_settings(root: Path) -> dict:
    f = rules_dir(root) / "_settings.yaml"
    return yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}


def load_rules(root: Path) -> dict[str, Rule]:
    out: dict[str, Rule] = {}
    problems: list[str] = []
    for f in sorted(rules_dir(root).glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        category = data.get("category", f.stem)
        for r in data.get("rules", []):
            problems += validate_rule_dict(r, category, f.name)
            if r.get("id") in out:
                problems.append(f"{f.name}: duplicate rule id {r['id']}")
                continue
            if problems:
                continue
            out[r["id"]] = Rule(id=r["id"], category=category, title=r["title"], severity=r["severity"], confidence=r["confidence"],
                                problem=r["problem"], explanation=r["explanation"], example=r["example"], fix=r["fix"], good=r.get("good", ""),
                                strict_only=bool(r.get("strict_only", False)), supersedes=list(r.get("supersedes", [])), params=dict(r.get("params", {})),
                                scope=r.get("scope", "file"))
    if problems:
        raise ValueError("invalid rule files: " + "; ".join(problems[:6]))
    if not out:
        raise ValueError(f"no rules found in {rules_dir(root)}")
    return out


def lower(conf: str, steps: int = 1) -> str:
    return CONFIDENCES[max(0, CONFIDENCES.index(conf) - steps)]


class SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fmt(template: str, data: dict) -> str:
    try:
        return template.format_map(SafeFormat(data))
    except (ValueError, IndexError):
        return template
