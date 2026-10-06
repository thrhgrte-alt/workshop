"""Patch suggestions as unified diff TEXT. Nothing here writes to the reviewed file.

Each fixer turns one detector hit into text edits ``(start, end, replacement)`` on the source. The result is re-analysed in memory: the report says
how many hits of the rule remain, so a bad patch is visible instead of trusted. Rules without a safe mechanical fix are reported as unsupported
with the reason (the human-readable fix is in the rule)."""

from __future__ import annotations

import difflib
import re

from .detectors import DETECTORS
from .review import analyse_source
from .rules import Rule
from .structure import Analysis, Call

Edit = tuple[int, int, str]


def _indent(src: str, pos: int) -> str:
    start = src.rfind("\n", 0, pos) + 1
    m = re.match(r"[ \t]*", src[start:])
    return m.group(0) if m else ""


def _calls_on_line(a: Analysis, line: int, pred) -> list[Call]:
    return [c for c in a.calls if c.line == line and pred(c)]


def _rename_global(new: str, name: str):
    def fix(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
        edits = []
        for c in _calls_on_line(a, line, lambda c: c.callee == name):
            t = a.toks[c.start]
            edits.append((t.pos, t.end, new))
        return edits, []
    return fix


def _rename_method(old: str, new: str):
    def fix(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
        edits = []
        for c in _calls_on_line(a, line, lambda c: c.is_method and c.method == old):
            t = a.toks[c.open - 1]
            edits.append((t.pos, t.end, new))
        return edits, []
    return fix


def fix_instance_new_parent(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    edits: list[Edit] = []
    notes: list[str] = []
    for c in _calls_on_line(a, line, lambda c: c.callee == "Instance.new" and len(c.args) == 2):
        s = c.start
        if not (s >= 2 and a.toks[s - 1].is_op("=") and a.toks[s - 2].kind == "name"):
            notes.append(f"line {line}: the result of Instance.new is not assigned to a variable, so there is nothing to set Parent on; assign it first")
            continue
        if a.toks[c.close].line != a.toks[s].line:
            notes.append(f"line {line}: the call spans several lines; edit it by hand")
            continue
        var = a.toks[s - 2].text
        (alo, ahi), (blo, bhi) = c.args[0], c.args[1]
        parent_text = src[a.toks[blo].pos:a.toks[bhi - 1].end]
        edits.append((a.toks[ahi - 1].end, a.toks[c.close].pos, ""))
        eol = src.find("\n", a.toks[c.close].end)
        eol = len(src) if eol < 0 else eol
        edits.append((eol, eol, f"\n{_indent(src, a.toks[s].pos)}{var}.Parent = {parent_text}"))
        notes.append("Parent is now assigned right after creation; move it below the property assignments to get the full benefit")
    return edits, notes


def fix_load_animation(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    edits = []
    for c in _calls_on_line(a, line, lambda c: c.is_method and c.method == "LoadAnimation"):
        colon = a.toks[c.open - 2]
        edits.append((colon.pos, colon.pos, ':WaitForChild("Animator")'))
    return edits, ["WaitForChild yields until the Animator exists; use FindFirstChildOfClass(\"Animator\") if it may never appear"]


def fix_missing_strict(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    if re.match(r"--!(nonstrict|nocheck)", a.all_tokens[0].text if a.all_tokens else ""):
        t = a.all_tokens[0]
        return [(t.pos, t.end, "--!strict")], ["--!strict will report type errors that nonstrict hid: expect to fix some"]
    return [(0, 0, "--!strict\n")], ["--!strict will report type errors that nonstrict hid: expect to fix some"]


def fix_datastore_pcall(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    from .detectors import ds_calls
    edits: list[Edit] = []
    notes: list[str] = []
    for c in ds_calls(a):
        if c.line != line or a.in_protected(c.start):
            continue
        if a.toks[c.close].line != a.toks[c.start].line:
            notes.append(f"line {line}: the call spans several lines; wrap it by hand")
            continue
        call_text = src[a.toks[c.start].pos:a.toks[c.close].end]
        s = c.start
        if s >= 3 and a.toks[s - 1].is_op("=") and a.toks[s - 2].kind == "name" and a.toks[s - 3].is_kw("local") and (s < 4 or True):
            var = a.toks[s - 2].text
            lo = a.toks[s - 3].pos
            edits.append((lo, a.toks[c.close].end, f"local ok, {var} = pcall(function() return {call_text} end)"))
            notes.append("introduces a local `ok`: rename it if it clashes, check it, and add a retry (rule DAT002)")
        elif s == 0 or a.toks[s - 1].line != a.toks[s].line:
            edits.append((a.toks[s].pos, a.toks[c.close].end, f"local ok, err = pcall(function() {call_text} end)"))
            notes.append("introduces locals `ok` and `err`: rename them if they clash, handle the failure, and add a retry (rule DAT002)")
        else:
            notes.append(f"line {line}: the call is part of a larger expression; wrap it by hand")
    return edits, notes


def fix_ignored_pcall(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    edits = []
    for c in _calls_on_line(a, line, lambda c: c.callee in ("pcall", "xpcall")):
        edits.append((a.toks[c.start].pos, a.toks[c.start].pos, "local ok, err = "))
    return edits, ["`err` is only the error message when ok is false: log or handle it (warn(err))"] if edits else []


FIXERS = {
    "API001": _rename_global("task.wait", "wait"),
    "PERF002": _rename_global("task.wait", "wait"),
    "API002": _rename_global("task.spawn", "spawn"),
    "API003": _rename_global("task.delay", "delay"),
    "API004": fix_instance_new_parent,
    "API005": fix_load_animation,
    "API008": None,  # filled below (two methods)
    "STR001": fix_missing_strict,
    "DAT001": fix_datastore_pcall,
    "COR001": fix_ignored_pcall,
}


def fix_connect_case(a: Analysis, src: str, line: int) -> tuple[list[Edit], list[str]]:
    edits = []
    for c in _calls_on_line(a, line, lambda c: c.is_method and c.method in ("connect", "disconnect")):
        t = a.toks[c.open - 1]
        edits.append((t.pos, t.end, t.text.capitalize()))
    return edits, []


FIXERS["API008"] = fix_connect_case

UNSUPPORTED = {
    "SEC001": "validation depends on what the handler should accept: a type or range check has to be written by someone who knows the contract",
    "SEC002": "the price or damage must come from a server-side table; the right table is a design decision",
    "SEC003": "a cooldown or permission check needs a decision about limits and who may act",
    "SEC004": "loadstring has no mechanical replacement; the code that needs it has to be redesigned around known functions",
    "SEC005": "the module has to be vendored into your place by a person",
    "API009": "the right replacement (os.clock, os.time, DateTime or GetServerTimeNow) depends on what the value is used for",
}


def apply_edits(src: str, edits: list[Edit]) -> str:
    out = src
    for start, end, text in sorted(set(edits), key=lambda e: (e[0], e[1]), reverse=True):
        out = out[:start] + text + out[end:]
    return out


def unified_diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f"a/{name}", tofile=f"b/{name}"))


def suggest(src: str, name: str, rule_id: str, rules: dict[str, Rule], *, line: int | None = None, strict: bool = False, context: str | None = None) -> dict:
    if rule_id not in rules:
        raise ValueError(f"unknown rule '{rule_id}'. Use list_rules for the ids")
    findings, a = analyse_source(src, name, rules, strict=strict, context=context, enabled={rule_id})
    hits = [f for f in findings if f["rule_id"] == rule_id and (line is None or f["line"] == line)]
    base = {"rule_id": rule_id, "file": name, "diff": "", "edits": 0, "notes": [], "hits_before": len([f for f in findings if f["rule_id"] == rule_id]), "hits_after": None}
    if not hits:
        raise ValueError(f"no {rule_id} finding{' on line ' + str(line) if line else ''} in {name}: nothing to patch (run review_file to see the findings)")
    fixer = FIXERS.get(rule_id)
    if fixer is None:
        return {**base, "supported": False, "reason": UNSUPPORTED.get(rule_id, "no safe mechanical fix is implemented for this rule") + f". Suggested fix: {rules[rule_id].fix}"}
    edits: list[Edit] = []
    notes: list[str] = []
    for ln in sorted({f["line"] for f in hits}):
        e, n = fixer(a, src, ln)
        edits += e
        notes += n
    if not edits:
        return {**base, "supported": False, "notes": sorted(set(notes)), "reason": "the flagged code has a shape this fixer does not handle; edit it by hand. Suggested fix: " + rules[rule_id].fix}
    new = apply_edits(src, edits)
    after, _ = analyse_source(new, name, rules, strict=strict, context=context, enabled={rule_id})
    return {**base, "supported": True, "diff": unified_diff(src, new, name), "edits": len(set(edits)), "notes": sorted(set(notes)),
            "hits_after": len([f for f in after if f["rule_id"] == rule_id])}
