"""Learning from false-positive decisions. No model is trained; the mechanism is a deterministic similarity over normalised code.

``normalise`` turns code into a token list that ignores comments, strings, numbers and local names (so renamed variables still match) but keeps
keywords, operators, member names after ``.``/``:`` and capitalised/global API names. ``similarity`` is the Jaccard similarity of the token
n-gram sets (default 3-grams). A saved false-positive pattern lowers the confidence of the same rule on code whose similarity to the pattern is
at least ``learning.similarity_threshold`` (rules/_settings.yaml); low-confidence findings are then shown as questions, not defects.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

from .lexer import code_tokens, tokenize
from .rules import lower

GLOBALS = frozenset("game workspace script task math string table os coroutine debug utf8 bit32 buffer typeof type tonumber tostring pcall xpcall error assert select "
                    "ipairs pairs next print warn require setmetatable getmetatable rawget rawset unpack wait spawn delay tick loadstring Enum Instance Vector3 Vector2 "
                    "CFrame Color3 UDim2 UDim BrickColor Random TweenInfo Ray Region3 NumberRange NumberSequence ColorSequence Rect".split())


def normalise(src: str) -> list[str]:
    toks = code_tokens(tokenize(src))
    out: list[str] = []
    for i, t in enumerate(toks):
        if t.kind == "string":
            out.append("STR")
        elif t.kind == "number":
            out.append("NUM")
        elif t.kind == "name":
            member = i > 0 and toks[i - 1].is_op(".", ":")
            if member or t.text in GLOBALS or t.text[:1].isupper():
                out.append(t.text)
            else:
                out.append("ID")
        else:
            out.append(t.text)
    return out


def shingles(tokens: list[str], n: int = 3) -> set[tuple[str, ...]]:
    if not tokens:
        return set()
    n = max(1, min(n, len(tokens)))
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def similarity(a_tokens: list[str], b_tokens: list[str], n: int = 3) -> float:
    n = min(n, len(a_tokens), len(b_tokens)) if a_tokens and b_tokens else n
    a, b = shingles(a_tokens, n), shingles(b_tokens, n)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pattern_id(rule_id: str, tokens: list[str]) -> str:
    return "fp-" + hashlib.sha1((rule_id + "|" + " ".join(tokens)).encode()).hexdigest()[:10]


class FalsePositiveStore:
    """``<workspace>/feedback/false_positives.jsonl``: one row per saved false-positive pattern."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    def make_row(self, rule_id: str, code: str, reason: str, file: str | None = None, line: int | None = None, project_id: str | None = None,
                 place_id: str | None = None, global_: bool = False) -> dict:
        tokens = normalise(code)
        if len(tokens) < 3:
            raise ValueError("the code pattern is too short to match anything reliably (needs at least 3 tokens); include the whole statement")
        window = max(1, len([ln for ln in code.splitlines() if ln.strip()]))
        return {"id": pattern_id(rule_id, tokens), "rule_id": rule_id, "tokens": tokens, "window_lines": window, "reason": reason.strip(), "file": file, "line": line,
                "project_id": project_id, "place_id": place_id, "global": global_,
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}

    def add(self, row: dict) -> bool:
        """Append unless the same pattern is already stored. Returns True when a row was written."""
        if any(r.get("id") == row["id"] for r in self.load()):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        return True


def window_tokens(lines: list[str], line: int, window: int) -> list[str]:
    chunk = "\n".join(lines[max(0, line - 1): max(0, line - 1) + window])
    return normalise(chunk)


def apply_learning(findings: list[dict], lines_by_file: dict[str, list[str]], rows: list[dict], threshold: float, max_steps: int, ngram: int = 3) -> list[dict]:
    """Lower confidence of own-rule findings that resemble saved false positives. Returns a list of what was applied (also stored on each finding)."""
    applied: list[dict] = []
    for f in findings:
        if f.get("source") != "own":
            continue
        lines = lines_by_file.get(f["file"], [])
        matches = []
        for r in rows:
            if r["rule_id"] != f["rule_id"]:
                continue
            if not r.get("global") and r.get("place_id") not in (None, f.get("place")):
                continue  # saved for another place of the project
            sim = similarity(window_tokens(lines, f["line"], r.get("window_lines", 1)), r["tokens"], ngram)
            if sim >= threshold:
                matches.append({"pattern_id": r["id"], "similarity": round(sim, 3), "reason": r.get("reason", ""), "from": "global" if r.get("global") else "project"})
        if matches:
            steps = min(len(matches), max_steps)
            f["confidence_original"] = f["confidence"]
            f["confidence"] = lower(f["confidence"], steps)
            f["learned"] = sorted(matches, key=lambda m: (-m["similarity"], m["pattern_id"]))
            applied.append({"file": f["file"], "line": f["line"], "rule_id": f["rule_id"], "from": f["confidence_original"], "to": f["confidence"], "matches": f["learned"]})
    return applied
