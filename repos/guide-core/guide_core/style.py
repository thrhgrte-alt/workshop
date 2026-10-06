"""Style as explicit, checkable data - not a vague "make it look like the references".

``style/style.yaml`` holds three kinds of information:

* ``traits``: named visual/design dimensions an agent can act on,
* ``ranges``: numeric limits that automated checks can measure,
* ``constraints`` / ``exclusions``: rules and things to avoid.

``style_brief`` turns that into a compact block for a prompt (progressive
disclosure): the agent asks for the traits relevant to the task instead of
loading the whole file every time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from .project import Project


def load_style(project: Project) -> dict:
    data = yaml.safe_load(Path(project.style_file).read_text(encoding="utf-8")) or {}
    problems = validate_style(data)
    if problems:
        raise ValueError(f"{project.style_file}: " + "; ".join(problems))
    return data


def validate_style(style: dict) -> list[str]:
    problems = []
    for key in ("version", "name", "summary", "traits"):
        if key not in style:
            problems.append(f"missing '{key}'")
    if not isinstance(style.get("traits", {}), dict):
        problems.append("'traits' must be a mapping")
    ids = [c.get("id") for c in style.get("constraints", [])]
    if len(ids) != len(set(ids)):
        problems.append("duplicate constraint ids")
    for c in style.get("constraints", []):
        if c.get("severity") not in ("must", "should"):
            problems.append(f"constraint '{c.get('id')}' needs severity must|should")
    for name, rng in style.get("ranges", {}).items():
        if "min" in rng and "max" in rng and rng["min"] > rng["max"]:
            problems.append(f"range '{name}' has min > max")
    return problems


def _fmt(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_fmt(v)}" for k, v in value.items())
    return str(value)


def style_brief(style: dict, focus: Iterable[str] = (), max_chars: int = 1800) -> str:
    focus = [f.lower() for f in focus]
    lines = [f"STYLE: {style['name']}", style["summary"].strip(), ""]
    traits = style.get("traits", {})
    ordered = sorted(traits, key=lambda k: (0 if any(f in k.lower() for f in focus) else 1, k))
    lines.append("TRAITS")
    lines += [f"- {k}: {_fmt(traits[k])}" for k in ordered]
    musts = [c for c in style.get("constraints", []) if c["severity"] == "must"]
    shoulds = [c for c in style.get("constraints", []) if c["severity"] == "should"]
    if musts:
        lines += ["", "MUST"] + [f"- [{c['id']}] {c['rule']}" for c in musts]
    if shoulds:
        lines += ["", "SHOULD"] + [f"- [{c['id']}] {c['rule']}" for c in shoulds]
    if style.get("exclusions"):
        lines += ["", "AVOID"] + [f"- {e}" for e in style["exclusions"]]
    text = "\n".join(lines)
    if len(text) > max_chars:
        marker = "\n[...truncated; request fewer focus areas]"
        text = text[: max(0, max_chars - len(marker))].rstrip() + marker
        text = text[:max_chars]
    return text


def check_ranges(measured: dict[str, float], ranges: dict[str, dict]) -> list[dict]:
    """Compare measured numbers with style ranges. Missing measurements are reported, not ignored."""
    findings = []
    for name, rng in ranges.items():
        if name not in measured:
            findings.append({"metric": name, "severity": "info", "message": "not measured"})
            continue
        v = measured[name]
        if "min" in rng and v < rng["min"]:
            findings.append({"metric": name, "severity": rng.get("severity", "warning"), "value": v,
                             "message": f"{name}={v:.3g} is below {rng['min']} ({rng.get('note', '')})".strip()})
        if "max" in rng and v > rng["max"]:
            findings.append({"metric": name, "severity": rng.get("severity", "warning"), "value": v,
                             "message": f"{name}={v:.3g} is above {rng['max']} ({rng.get('note', '')})".strip()})
    return findings
