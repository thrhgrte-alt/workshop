"""The review pipeline: own rule detectors + backend wrappers -> ONE report that says which tool produced each finding."""

from __future__ import annotations

import functools
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..guide_adapter import Project
from . import backends as B
from .. import learning_params
from . import learning, projects as PJ, ruleset as RS
from .detectors import DETECTORS
from .rules import SEV_RANK, Rule, fmt, load_rules, load_settings, rules_dir
from .structure import Analysis

NOT_CHECKED_ALWAYS = [
    "runtime behaviour: nothing was executed and no game was loaded",
    "data flow across files and functions (only module require cycles are checked across files, and only in a folder review)",
    "anything inside strings (including interpolated-string expressions)",
]


@functools.lru_cache(maxsize=8)
def _cached(root: str, stamp: tuple) -> tuple[dict[str, Rule], dict]:
    return load_rules(Path(root)), load_settings(Path(root))


def get_rules(project: Project) -> tuple[dict[str, Rule], dict]:
    d = rules_dir(project.root)
    stamp = tuple(sorted((f.name, f.stat().st_mtime_ns) for f in d.glob("*.yaml")))
    return _cached(str(project.root), stamp)


def read_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{path} is {size} bytes; refusing to review files over {max_bytes} bytes (rules/_settings.yaml limits.max_file_bytes)")
    return path.read_bytes().decode("utf-8", errors="replace")


def collect_files(target: str | Path, settings: dict) -> tuple[Path, list[Path], bool]:
    """Returns (root, files, is_folder). Folders are walked deterministically; symlinks are not followed; hidden and vendored folders are skipped."""
    p = Path(target).expanduser()
    lim = settings.get("limits", {})
    if not p.exists():
        raise ValueError(f"path not found: {target}. Pass a .lua/.luau file or a folder of them")
    if p.is_file():
        return p.parent, [p], False
    exts = tuple(lim.get("extensions", [".lua", ".luau"]))
    skip = set(lim.get("skip_dirs", []))
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            if fn.endswith(exts) and not fp.is_symlink():
                files.append(fp)
    if not files:
        raise ValueError(f"no {', '.join(exts)} files under {target}")
    cap = int(lim.get("max_files", 300))
    if len(files) > cap:
        raise ValueError(f"{len(files)} files under {target}; the limit is {cap} per review (rules/_settings.yaml limits.max_files). Review a subfolder")
    return p, files, True


def _snippet(lines: list[str], line: int) -> str:
    return lines[line - 1].strip()[:160] if 1 <= line <= len(lines) else ""


def _finding(rule: Rule, hit, a: Analysis, name: str) -> dict:
    data = dict(hit.data)
    text = hit.line and _snippet(a.lines, hit.line) or ""
    return {"file": name, "line": hit.line, "col": hit.col, "rule_id": rule.id, "source": "own", "category": rule.category, "severity": hit.severity or rule.severity,
            "confidence": hit.confidence or rule.confidence, "problem": hit.problem or fmt(rule.problem, data), "fix": hit.fix or fmt(rule.fix, data), "snippet": text,
            "line_fingerprint": RS.line_fingerprint(a.line_text(hit.line))}


def analyse_source(src: str, name: str, rules: dict[str, Rule], *, strict: bool = False, context: str | None = None,
                   enabled: set[str] | None = None) -> tuple[list[dict], Analysis]:
    a = Analysis.of(src, path=name, context=context)
    out: list[dict] = []
    for rid, rule in rules.items():
        if rule.scope != "file" or rid not in DETECTORS:
            continue
        if rule.strict_only and not strict:
            continue
        if enabled is not None and rid not in enabled:
            continue
        try:
            hits = DETECTORS[rid](a, rule.params)
        except Exception as exc:  # a detector bug must never take the whole review down: say so loudly instead
            out.append({"file": name, "line": 1, "col": 0, "rule_id": f"internal:{rid}", "source": "own", "category": "internal", "severity": "info", "confidence": "high",
                        "problem": f"detector {rid} crashed ({type(exc).__name__}: {exc}); this rule did not run on this file", "fix": "report this as a bug", "snippet": "",
                        "line_fingerprint": ""})
            continue
        seen = set()
        for h in hits:
            key = (h.line, h.problem or "", tuple(sorted(h.data.items())))
            if key in seen:
                continue
            seen.add(key)
            out.append(_finding(rule, h, a, name))
    # supersedes: a more specific finding replaces a more general one on the same line
    sup = {(f["file"], f["line"], s) for f in out for s in rules[f["rule_id"]].supersedes if f["rule_id"] in rules}
    out = [f for f in out if (f["file"], f["line"], f["rule_id"]) not in sup]
    return out, a


# ---------------------------------------------------------------------------------------------------------------------------------
# module cycles (folder reviews)
# ---------------------------------------------------------------------------------------------------------------------------------
def module_name(path: Path) -> str:
    stem = re.sub(r"\.(luau?|txt)$", "", path.name)
    stem = re.sub(r"\.(server|client)$", "", stem)
    return path.parent.name if stem == "init" else stem


def required_names(a: Analysis) -> list[tuple[str, int]]:
    out = []
    for c in a.calls:
        if c.callee != "require" or not c.args:
            continue
        lo, hi = c.args[0]
        name = None
        for k in range(hi - 1, lo - 1, -1):
            t = a.toks[k]
            if t.kind == "string" and k > lo and a.toks[k - 1].is_op("(") and k - 2 >= lo and a.toks[k - 2].text in ("WaitForChild", "FindFirstChild"):
                name = t.text.strip("\"'")
                break
        if name is None:
            for k in range(hi - 1, lo - 1, -1):
                t = a.toks[k]
                if t.kind == "name" and t.text not in ("Parent", "script"):
                    name = t.text
                    break
        if name:
            out.append((name, c.line))
    return out


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    sccs: list[list[str]] = []
    counter = [0]

    def visit(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in graph.get(v, ()):
                sccs.append(sorted(comp))

    for v in sorted(graph):
        if v not in index:
            visit(v)
    return sorted(sccs)


def cycle_path(graph: dict[str, set[str]], comp: list[str]) -> list[str]:
    start = comp[0]
    members = set(comp)
    path = [start]

    def dfs(v: str, seen: set[str]) -> list[str] | None:
        for w in sorted(graph.get(v, ())):
            if w == start and (len(path) > 1 or v == start):
                return path + [start]
            if w in members and w not in seen:
                path.append(w)
                r = dfs(w, seen | {w})
                if r:
                    return r
                path.pop()
        return None

    return dfs(start, {start}) or comp + [start]


def cycle_findings(analyses: dict[str, tuple[Path, Analysis]], rule: Rule) -> list[dict]:
    by_name: dict[str, list[str]] = {}
    for name, (p, _) in analyses.items():
        by_name.setdefault(module_name(p), []).append(name)
    graph: dict[str, set[str]] = {}
    first_line: dict[tuple[str, str], int] = {}
    for name, (p, a) in analyses.items():
        me = module_name(p)
        for target, line in required_names(a):
            if len(by_name.get(target, [])) == 1 and len(by_name.get(me, [])) == 1:
                graph.setdefault(me, set()).add(target)
                first_line.setdefault((me, target), line)
    out = []
    for comp in find_cycles(graph):
        path = cycle_path(graph, comp)
        head = comp[0]
        nxt = path[1] if len(path) > 1 else head
        file_name = by_name[head][0]
        a = analyses[file_name][1]
        line = first_line.get((head, nxt), 1)
        f = _finding(rule, type("H", (), {"line": line, "data": {"cycle": " -> ".join(path)}, "confidence": None, "severity": None, "problem": None, "fix": None, "col": 0})(),
                     a, file_name)
        out.append(f)
    return out


# ---------------------------------------------------------------------------------------------------------------------------------
# the review
# ---------------------------------------------------------------------------------------------------------------------------------
def parse_backends(backends: list[str] | str | None) -> list[str]:
    if backends is None:
        return list(B.BACKENDS)
    if isinstance(backends, str):
        backends = [b.strip() for b in backends.split(",") if b.strip()]
    backends = [b for b in backends if b and b != "none"]
    unknown = [b for b in backends if b not in B.BACKENDS]
    if unknown:
        raise ValueError(f"unknown backend(s) {unknown}. Choose from {list(B.BACKENDS)}, or pass [] / 'none' to run only the own rules")
    return backends


def review_target(project: Project, target: str, *, project_id: str | None = None, place_id: str | None = None, strict: bool = False,
                  backends: list[str] | str | None = None, ruleset_path: str | None = None, context: str | None = None, timeout: float | None = None) -> dict:
    scope = PJ.resolve_scope(project, project_id, place_id)  # refuses (and says what is missing) before anything is read
    rules, settings = get_rules(project)
    rules, settings = learning_params.apply_params(rules, settings, learning_params.store(project), learning_params.to_core_scope(scope))  # identity when nothing is learned
    lim = settings.get("limits", {})
    root, files, is_folder = collect_files(target, settings)
    if context not in (None, "server", "client", "shared"):
        raise ValueError("context must be 'server', 'client' or 'shared' (or omitted to infer it from the file name/folder)")
    wanted = parse_backends(backends)

    names = {f: (str(f.relative_to(root)).replace(os.sep, "/") if is_folder else str(f)) for f in files}
    place_of = {names[f]: PJ.place_for_file(scope, names[f] if is_folder else f.name) for f in files}
    all_findings: list[dict] = []
    analyses: dict[str, tuple[Path, Analysis]] = {}
    file_rows = []
    lines_by_file: dict[str, list[str]] = {}
    for f in files:
        src = read_text(f, int(lim.get("max_file_bytes", 1_048_576)))
        found, a = analyse_source(src, names[f], rules, strict=strict, context=context)
        all_findings += found
        analyses[names[f]] = (f, a)
        lines_by_file[names[f]] = a.lines
        file_rows.append({"file": names[f], "place": place_of[names[f]], "lines": len(a.lines), "context": a.file_kind() or "unknown", "balanced": a.balanced})
    if is_folder and "STR003" in rules:
        all_findings += cycle_findings(analyses, rules["STR003"])

    # backends: one run per tool over all files
    backend_results: dict[str, dict] = {}
    t = float(timeout if timeout is not None else project.env("BACKEND_TIMEOUT") or lim.get("backend_timeout_seconds", 20))
    abs_files = [str(f.resolve()) for f in files]
    rev = {str(f.resolve()): names[f] for f in files}
    for name in B.BACKENDS:
        if name not in wanted:
            backend_results[name] = B.BackendResult(name, "not_requested", message="not requested for this review").to_dict()
            continue
        res, grouped = B.run_backend(name, abs_files, timeout=t, cwd=str(root), batch=int(lim.get("backend_batch_size", 40)))
        backend_results[name] = res.to_dict()
        for fpath, items in grouped.items():
            disp = rev.get(fpath) or rev.get(os.path.abspath(fpath)) or fpath
            for it in items:
                ln = lines_by_file.get(disp, [])
                all_findings.append({"file": disp, "line": it.line, "col": it.col, "rule_id": it.rule_id, "source": name, "category": f"backend:{name}", "severity": it.severity,
                                     "confidence": "high", "problem": it.problem, "fix": it.fix or "see the tool's documentation for this diagnostic",
                                     "snippet": _snippet(ln, it.line), "line_fingerprint": RS.line_fingerprint(ln[it.line - 1] if 1 <= it.line <= len(ln) else "")})
    for f in all_findings:
        f["place"] = place_of.get(f["file"])

    # layered ruleset (global -> project -> place [-> explicit file]); applied per place so a place override never leaks into another place
    layer_cache: dict[str | None, dict] = {}

    def merged(place: str | None) -> dict:
        if place not in layer_cache:
            layers = []
            for name, path in PJ.layer_paths(project, scope, place):
                layers.append((name, str(path.relative_to(project.root)) if path.is_relative_to(project.root) else str(path), RS.load(path)))
            if ruleset_path:
                ep = Path(ruleset_path).expanduser()
                if not ep.exists():
                    raise ValueError(f"ruleset file not found: {ruleset_path}")
                layers.append(("explicit", str(ep), RS.load(ep)))
            m = RS.merge(layers)
            problems = RS.validate(m, set(rules))
            if problems:
                raise ValueError("ruleset problems: " + "; ".join(problems))
            layer_cache[place] = m
        return layer_cache[place]

    kept, suppressed, dropped, usage = [], [], [], []
    for place in sorted({f["place"] for f in all_findings}, key=lambda x: (x is None, x or "")):
        k, s_, d, u = RS.apply([f for f in all_findings if f["place"] == place], merged(place))
        kept += k
        suppressed += s_
        dropped += d
    places_seen = sorted({p for p in place_of.values() if p}, key=str) or [None]
    layer_report, seen_layers = [], set()
    for place in (sorted({p for p in place_of.values()}, key=lambda x: (x is None, x or "")) or [None]):
        m = merged(place)
        for lay in m["layers"]:
            key = (lay["layer"], lay["path"])
            if key in seen_layers:
                continue
            seen_layers.add(key)
            layer_report.append({**lay, "place": place if lay["layer"] == "place" else None})
        for i, u in enumerate(RS.apply([dict(f) for f in all_findings if f["place"] == place], m)[3]):
            u["place"] = place
            usage.append(u)
    # collapse usage rows per (layer, rule, file, line, reason): sum matches over places
    folded: dict[tuple, dict] = {}
    for u in usage:
        k = (u["layer"], u["rule"], u["file"], u["line"], u["reason"])
        if k in folded:
            folded[k]["matched"] += u["matched"]
        else:
            folded[k] = {kk: vv for kk, vv in u.items() if kk != "place"}
    usage = list(folded.values())
    disabled_all = sorted({r for p in place_of.values() for r in merged(p)["disabled_rules"]} | set(merged(None)["disabled_rules"]))
    overrides = {"layers": layer_report, "applied_suppressions": [u for u in usage if u["matched"]], "unmatched_suppressions": [u for u in usage if not u["matched"]],
                 "disabled_rules": disabled_all, "severity_overrides": merged(None)["severity_overrides"]}

    # learning from saved false positives: this project's rows plus rows marked global
    fp_rows = []
    for gl, st in ((False, learning.FalsePositiveStore(PJ.fp_store_path(project, scope))), (True, learning.FalsePositiveStore(PJ.fp_store_path(project, scope, True)))):
        fp_rows += [{**r, "global": gl or r.get("global", False)} for r in st.load()]
    ls = settings.get("learning", {})
    learned = learning.apply_learning(kept, lines_by_file, fp_rows, float(ls.get("similarity_threshold", 0.6)), int(ls.get("max_confidence_steps", 2)), int(ls.get("ngram", 3)))
    # strict mode escalation
    if strict:
        esc = settings.get("strict_mode", {}).get("escalate", {"warning": "error", "info": "warning"})
        for f in kept:
            if f["source"] == "own" and f["severity"] in esc:
                f["severity_original"] = f.get("severity_original", f["severity"])
                f["severity"] = esc[f["severity"]]
    # fingerprints and kinds
    counts: dict[tuple, int] = {}
    for f in sorted(kept, key=lambda x: (x["file"], x["line"], x["rule_id"])):
        key = (f["file"], f["rule_id"], f["line_fingerprint"])
        counts[key] = counts.get(key, 0) + 1
        f["fingerprint"] = f"{Path(f['file']).name}|{f['rule_id']}|{f['line_fingerprint']}|{counts[key]}"
        f["fp"] = hashlib.sha1(f["fingerprint"].encode()).hexdigest()[:10]
        f["kind"] = "question" if f["confidence"] == "low" else "defect"
    order = lambda f: (f["file"], f["line"], SEV_RANK.get(f["severity"], 3), f["rule_id"])
    defects = sorted((f for f in kept if f["kind"] == "defect"), key=lambda f: (SEV_RANK.get(f["severity"], 3), f["file"], f["line"], f["rule_id"]))
    questions = sorted((f for f in kept if f["kind"] == "question"), key=order)

    rep = assemble(project, rules, settings, strict, wanted, backend_results, defects, questions, suppressed, dropped, learned, fp_rows, file_rows, overrides, is_folder, str(target))
    rep["project"] = {**scope.to_dict(), "places_in_review": sorted({p for p in place_of.values() if p})}
    rep["statement"] = f"[project {scope.label()}] " + rep["statement"]
    rep["summary_line"] = summary_line(rep)
    rep["table"] = render_table(rep)
    return rep


def summary_line(rep: dict) -> str:
    s = rep["summary"]
    sev = s["by_severity"]
    be = rep["checked"]
    tail = f"backends ran: {', '.join(be['backends_ran']) or 'none'}" + (f"; MISSING: {', '.join(be['backends_missing'])}" if be["backends_missing"] else "") \
        + (f"; FAILED: {', '.join(be['backends_failed'])}" if be["backends_failed"] else "")
    return (f"[{rep['project']['project_id']}{'/' + rep['project']['place_id'] if rep['project']['place_id'] else ''}] {rep['verdict'].upper()}: "
            f"{sev['error']} error, {sev['warning']} warning, {sev['info']} info, {s['questions']} question(s), {s['suppressed']} suppressed in {s['files']} file(s); {tail}")


def assemble(project, rules, settings, strict, wanted, backend_results, defects, questions, suppressed, dropped, learned, fp_rows, file_rows, overrides, is_folder, target) -> dict:
    cap = int(settings.get("limits", {}).get("max_findings_returned", 400))
    sev_counts = {s: sum(1 for f in defects if f["severity"] == s) for s in ("error", "warning", "info")}
    by_source: dict[str, int] = {}
    for f in defects + questions:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1
    enabled = [r for r in rules.values() if (strict or not r.strict_only) and (is_folder or r.scope == "file")]
    ran = [n for n, r in backend_results.items() if r["status"] == "ran"]
    missing = [n for n, r in backend_results.items() if r["status"] == "missing" and n in wanted]
    failed = [n for n, r in backend_results.items() if r["status"] in ("error", "timeout", "unparsed")]
    skipped = [n for n, r in backend_results.items() if r["status"] == "not_requested"]
    cats = sorted({r.category for r in enabled})
    not_checked = list(NOT_CHECKED_ALWAYS)
    if not is_folder:
        not_checked.append("module require cycles (needs a folder review)")
    for n, what in (("luau-analyze", "type and syntax errors"), ("selene", "selene lints"), ("stylua", "formatting")):
        st = backend_results[n]["status"]
        if st != "ran":
            why = {"missing": "not installed", "not_requested": "not requested", "error": "failed to run", "timeout": "timed out", "unparsed": "output not understood"}[st]
            not_checked.append(f"{what} ({n} {why})")
    if not strict:
        not_checked.append(f"strict-only rules ({', '.join(sorted(r.id for r in rules.values() if r.strict_only))}): run with strict=true")
    if overrides["disabled_rules"]:
        not_checked.append(f"rules disabled by the layered ruleset: {', '.join(overrides['disabled_rules'])}")
    checked = {"own_rules": {"count": len(enabled), "categories": cats, "strict": strict, "rule_ids": sorted(r.id for r in enabled)},
               "backends_ran": ran, "backends_missing": missing, "backends_failed": failed, "backends_not_requested": skipped,
               "files": len(file_rows), "lines": sum(r["lines"] for r in file_rows)}
    if sev_counts["error"]:
        verdict = "errors"
    elif sev_counts["warning"]:
        verdict = "warnings"
    elif defects:
        verdict = "info only"
    else:
        verdict = "clean"
    stmt = (f"{'CLEAN: no defects' if verdict == 'clean' else verdict.upper()}"
            f"{'' if not questions else f' ({len(questions)} open question(s))'}. Checked {checked['files']} file(s) with {len(enabled)} own rules "
            f"({', '.join(cats)}){' in strict mode' if strict else ''}; backends that ran: {', '.join(ran) or 'none'}"
            f"{'; NOT installed: ' + ', '.join(missing) if missing else ''}{'; FAILED: ' + ', '.join(failed) if failed else ''}. "
            f"Not checked: {'; '.join(not_checked)}.")
    shown_d, shown_q = defects[:cap], questions[: max(0, cap - len(defects[:cap]))]
    report = {"target": target, "strict": strict, "clean": verdict == "clean" and not questions, "verdict": verdict,
              "summary": {"files": len(file_rows), "defects": len(defects), "by_severity": sev_counts, "questions": len(questions), "suppressed": len(suppressed),
                          "by_source": by_source, "learned_adjustments": len(learned)},
              "statement": stmt, "checked": checked, "not_checked": not_checked, "backends": backend_results,
              "overrides": overrides, "findings": shown_d, "questions": shown_q, "suppressed": suppressed[:cap], "files": file_rows, "learned": learned,
              "past_corrections": past_corrections(defects + questions, fp_rows), "truncated": max(0, len(defects) + len(questions) - cap)}
    return report


def past_corrections(findings: list[dict], rows: list[dict]) -> list[dict]:
    present = {f["rule_id"] for f in findings}
    out = []
    for r in rows:
        if r["rule_id"] in present:
            out.append({"rule_id": r["rule_id"], "pattern_id": r["id"], "reason": r.get("reason", ""), "saved_from": r.get("file"), "tokens": len(r.get("tokens", []))})
    return out[:20]


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_table(report: dict) -> str:
    multi = any(f.get("place") for f in report["findings"] + report["questions"])

    def rows(items: list[dict]) -> list[str]:
        return [f"| {_esc(f['file'])} |{(' ' + str(f.get('place') or '-') + ' |') if multi else ''} {f['line']} | {f['rule_id']} | {f['severity']} | {f['confidence']} | {_esc(f['source'])} | "
                f"{_esc(f['problem'])} | {_esc(f['fix'])} |" for f in items]

    head = "| file |" + (" place |" if multi else "") + " line | rule | severity | confidence | tool | problem | fix |\n|---|" + ("---|" if multi else "") + "---|---|---|---|---|---|---|"
    parts = [report.get("summary_line", "")]
    if report["findings"]:
        parts.append("## Findings\n" + head + "\n" + "\n".join(rows(report["findings"])))
    else:
        parts.append("## Findings\nNo defects reported.")
    if report["questions"]:
        parts.append("## Questions (low confidence: not defects, please confirm)\n" + head + "\n" + "\n".join(rows(report["questions"])))
    ov = report["overrides"]
    lines = [f"- layer {l['layer']}{' (' + str(l['place']) + ')' if l.get('place') else ''}: {l['path']}, {l['suppressions']} suppression(s)" for l in ov["layers"]]
    lines += [f"- APPLIED suppression [{u['layer']}] {u['rule']} {u.get('file') or '*'}: {_esc(u['reason'])} (matched {u['matched']})" for u in ov["applied_suppressions"]]
    lines += [f"- not matched: [{u['layer']}] {u['rule']} {u.get('file') or '*'}" for u in ov["unmatched_suppressions"]]
    parts.append("## Overrides in effect\n" + "\n".join(lines))
    b = ["## Backends"] + [f"- {n}: {r['status']}" + (f" ({r['message']})" if r["message"] else "") + (f", {r['findings']} finding(s)" if r["status"] == "ran" else "")
                           for n, r in report["backends"].items()]
    parts.append("\n".join(b))
    s = report["summary"]
    parts.append(f"## Summary\n{report['statement']}")
    return "\n\n".join(parts)
