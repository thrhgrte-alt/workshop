"""MCP tools for the Luau reviewer.

Every tool that reads or writes project data takes `project_id` (and `place_id` where it applies) and REFUSES without a registered project; nothing is guessed.
Read-only tools never touch code. Tools that write (`suggest_patch`, `suppress_finding`, `mark_false_positive`, and the shared feedback tools) default to `dry_run=true`.
NOTHING here edits a reviewed Luau file, connects to Studio or publishes. Output is short by default (one-line summary first, ranked rows); `explain_finding` gives details.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from .domain import backends as B
from .domain import detectors as D
from .domain import learning, patch as P, projects as PJ, ruleset as RS
from .domain.review import analyse_source, collect_files, get_rules, read_text, review_target
from .domain.structure import Analysis
from .guide_adapter import Project, ToolSpec, dryrun, feedback as fb, scope as scope_

# Tools most sessions never need. A client can leave them disabled (LUAUREV_DISABLE_RARE=1 does it for the bundled server).
RARE_TOOLS = ("list_rules", "find_remote_handlers", "find_datastore_calls", "compare_reviews", "search_library", "promote_run")
ROW_KEYS = ("file", "place", "line", "rule_id", "severity", "confidence", "source", "problem", "fix", "fp")


def _short(s: str, n: int = 150) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def compact(rep: dict, detail: str) -> dict:
    """Brief by default: summary line first, ranked rows, what was/was not checked. detail='full' returns the whole report (also what the CLI saves)."""
    if detail == "full":
        return rep
    if detail != "brief":
        raise ValueError("detail must be 'brief' or 'full'")
    row = lambda f: {k: (_short(f[k]) if k in ("problem", "fix") else f[k]) for k in ROW_KEYS if f.get(k) is not None}
    ov = rep["overrides"]
    return {"summary": rep["summary_line"], "project": rep["project"], "findings": [row(f) for f in rep["findings"]], "questions": [row(f) for f in rep["questions"]],
            "backends": {n: b["status"] + ("" if b["status"] in ("ran", "not_requested") else f": {_short(b['message'], 90)}") for n, b in rep["backends"].items()},
            "checked": {"own_rules": rep["checked"]["own_rules"]["count"], "categories": rep["checked"]["own_rules"]["categories"], "strict": rep["strict"], "files": rep["checked"]["files"]},
            "not_checked": [_short(x, 110) for x in rep["not_checked"]],
            "overrides": {"layers": [f"{l['layer']}:{l['path']}" for l in ov["layers"]],
                          "applied": [f"[{u['layer']}] {u['rule']} {u.get('file') or '*'} x{u['matched']}: {_short(u['reason'], 80)}" for u in ov["applied_suppressions"]],
                          "unmatched": [f"[{u['layer']}] {u['rule']} {u.get('file') or '*'}" for u in ov["unmatched_suppressions"]], "disabled": ov["disabled_rules"]},
            "learned": len(rep["learned"]), "suppressed": rep["summary"]["suppressed"], "more": "explain_finding(rule_id, file, line) for details; detail='full' for the whole report"}


def make_tools(project: Project) -> list[ToolSpec]:
    def rules_() -> dict:
        return get_rules(project)[0]

    def settings_() -> dict:
        return get_rules(project)[1]

    def store_for(scope: PJ.Scope, global_: bool = False) -> learning.FalsePositiveStore:
        return learning.FalsePositiveStore(PJ.fp_store_path(project, scope, global_))

    def all_rows(scope: PJ.Scope) -> list[dict]:
        return [{**r, "global": False} for r in store_for(scope).load()] + [{**r, "global": True} for r in store_for(scope, True).load()]

    def read(path: str) -> tuple[Path, str]:
        p = Path(path).expanduser()
        if not p.exists():
            raise ValueError(f"file not found: {path}")
        if not p.is_file():
            raise ValueError(f"{path} is a folder; use review_folder (or pass a file)")
        return p, read_text(p, int(settings_().get("limits", {}).get("max_file_bytes", 1_048_576)))

    # ---------------------------------------------------------------- read-only
    def review_file(path: str, project_id: str | None = None, place_id: str | None = None, strict: bool = False, backends: list[str] | None = None,
                    ruleset: str | None = None, context: str | None = None, detail: str = "brief") -> dict[str, Any]:
        """Review one Luau file with the own rules plus installed luau-analyze/selene/stylua. Needs project_id. Low-confidence findings are `questions`, not defects. backends: omit=all installed, []=own rules only."""
        if not Path(path).expanduser().is_file():
            raise ValueError(f"'{path}' is not a file. Use review_folder for a folder")
        return compact(review_target(project, path, project_id=project_id, place_id=place_id, strict=strict, backends=backends, ruleset_path=ruleset, context=context), detail)

    def review_folder(path: str, project_id: str | None = None, place_id: str | None = None, strict: bool = False, backends: list[str] | None = None,
                      ruleset: str | None = None, context: str | None = None, detail: str = "brief") -> dict[str, Any]:
        """Review every .lua/.luau under a folder, labelling each finding with its place; also finds module require cycles. Needs project_id; without place_id files are matched to places by folder."""
        if not Path(path).expanduser().is_dir():
            raise ValueError(f"'{path}' is not a folder. Use review_file for a single file")
        return compact(review_target(project, path, project_id=project_id, place_id=place_id, strict=strict, backends=backends, ruleset_path=ruleset, context=context), detail)

    def explain_finding(rule_id: str, project_id: str | None = None, file: str | None = None, line: int | None = None, strict: bool = False,
                        context: str | None = None) -> dict[str, Any]:
        """Details for one rule: why, bad and good example, fix, confidence, saved false positives of the project. With file+line: that finding and the code around it. Needs project_id."""
        scope = PJ.resolve_scope(project, project_id)
        rules = rules_()
        if ":" in rule_id and rule_id.split(":")[0] in B.BACKENDS:
            tool = rule_id.split(":")[0]
            return {"rule_id": rule_id, "source": tool, "own_rule": False, "explanation": f"Produced by the external tool `{tool}`; this repository only wraps its output. See that tool's documentation for this diagnostic."}
        if rule_id not in rules:
            near = difflib.get_close_matches(rule_id, list(rules), n=3, cutoff=0.4)
            raise ValueError(f"unknown rule '{rule_id}'." + (f" Did you mean {near}?" if near else "") + " Use list_rules for all ids")
        out: dict[str, Any] = {**rules[rule_id].to_dict(), "own_rule": True, "source": "own", "project": scope.project_id,
                               "kind_when_reported": "question" if rules[rule_id].confidence == "low" else "defect",
                               "known_false_positives": [{"pattern_id": r["id"], "reason": r["reason"], "scope": "global" if r["global"] else scope.project_id} for r in all_rows(scope) if r["rule_id"] == rule_id][:10]}
        if file is not None:
            p, src = read(file)
            found, a = analyse_source(src, str(p), rules, strict=strict, context=context, enabled={rule_id})
            near = [f for f in found if f["rule_id"] == rule_id and (line is None or f["line"] == line)]
            if line is not None and not near:
                raise ValueError(f"no {rule_id} finding on line {line} of {file}. Findings of this rule are on lines {[f['line'] for f in found if f['rule_id'] == rule_id] or 'none'}")
            out["findings_in_file"] = [{"line": f["line"], "problem": f["problem"], "fix": f["fix"], "confidence": f["confidence"]} for f in (near or found)]
            if near or found:
                ln = (near or found)[0]["line"]
                out["code_context"] = [{"line": i, "text": a.lines[i - 1]} for i in range(max(1, ln - 3), min(len(a.lines), ln + 3) + 1)]
        return out

    def list_rules(category: str | None = None, severity: str | None = None, confidence: str | None = None, strict_only: bool | None = None, details: bool = False,
                   project_id: str | None = None) -> dict[str, Any]:
        """List the own rules (id, category, severity, confidence, one-line problem). Filter by category/severity/confidence/strict_only; details=true adds explanations. Stateless: project_id optional."""
        if project_id:
            PJ.resolve_scope(project, project_id)
        cats = sorted({r.category for r in rules_().values()})
        if category is not None and category not in cats:
            raise ValueError(f"unknown category '{category}'. Categories: {cats}")
        rows = [r for r in rules_().values() if (category is None or r.category == category) and (severity is None or r.severity == severity)
                and (confidence is None or r.confidence == confidence) and (strict_only is None or r.strict_only == strict_only)]
        brief = lambda r: {"id": r.id, "severity": r.severity, "confidence": r.confidence, "strict_only": r.strict_only, "problem": _short(r.problem, 110)}
        return {"count": len(rows), "categories": cats, "rules": [r.to_dict(True) if details else brief(r) for r in rows],
                "note": "confidence low = reported as a question; strict_only = only with strict=true"}

    def find_remote_handlers(path: str, project_id: str | None = None, context: str | None = None) -> dict[str, Any]:
        """List server remote handlers (OnServerEvent/OnServerInvoke) in a file or folder: params, which look validated, which look like price/damage, cooldown signal. A finder, not a verdict. Stateless."""
        if project_id:
            PJ.resolve_scope(project, project_id)
        root, files, is_folder = collect_files(path, settings_())
        out = []
        for f in files:
            a = Analysis.of(read_text(f, int(settings_()["limits"]["max_file_bytes"])), str(f), context)
            for h in D.remote_handlers(a):
                fn = h["func"]
                lo, hi = D.body_range(a, fn)
                names = {t.text for t in a.toks[lo:hi] if t.kind == "name"}
                params = [q.name for q in fn.params[1:]]
                out.append({"file": str(f.relative_to(root)) if is_folder else str(f), "line": h["line"], "kind": h["kind"], "remote": h["remote"], "params": params,
                            "validated": {q: D.param_validated(a, fn, q) for q in params if q != "..."}, "sensitive_params": [q for q in params if D.is_sensitive(q)],
                            "uses_varargs": "..." in params,
                            "cooldown_or_permission_signal": any(D.COOLDOWN_RE.search(n) for n in names) or any(lo < c.start < hi and c.callee in D.TIME_CALLS for c in a.calls)})
        return {"count": len(out), "handlers": out, "files_scanned": len(files)}

    def find_datastore_calls(path: str, project_id: str | None = None, context: str | None = None) -> dict[str, Any]:
        """List DataStore calls in a file or folder: receiver, write or read, in pcall/retry helper, in a loop, key expression; plus BindToClose/ProcessReceipt per file. A finder, not a verdict. Stateless."""
        if project_id:
            PJ.resolve_scope(project, project_id)
        root, files, is_folder = collect_files(path, settings_())
        calls, per_file = [], []
        for f in files:
            a = Analysis.of(read_text(f, int(settings_()["limits"]["max_file_bytes"])), str(f), context)
            name = str(f.relative_to(root)) if is_folder else str(f)
            for c in D.ds_calls(a):
                pc = D._protecting_call(a, c.start)
                calls.append({"file": name, "line": c.line, "receiver": c.receiver, "method": c.method, "write": c.method in D.DS_WRITE, "in_pcall": a.in_protected(c.start),
                              "protected_by": pc.callee if pc else None, "in_loop": bool(D.has_loop_ancestor(a, c.start)), "key": a.text(*c.args[0]) if c.args else None})
            per_file.append({"file": name, "datastore_calls": sum(1 for c in calls if c["file"] == name), "bind_to_close": a.has_name("BindToClose"),
                             "process_receipt": any(True for _ in D._receipt_funcs(a))})
        return {"count": len(calls), "calls": calls, "files": per_file, "unprotected": sum(1 for c in calls if not c["in_pcall"])}

    def compare_reviews(base: Any, new: Any, project_id: str | None = None, place_id: str | None = None, strict: bool = False, backends: list[str] | None = None) -> dict[str, Any]:
        """What is NEW, FIXED or changed between two reviews. base/new: a report dict, a saved report .json, or a Luau file/folder (reviewed now, own rules by default; then project_id is needed). Matched by file, rule and line text, not line number."""
        def get(x: Any, label: str) -> dict:
            if isinstance(x, dict):
                return x
            if isinstance(x, str):
                p = Path(x).expanduser()
                if p.suffix == ".json" and p.is_file():
                    return json.loads(p.read_text(encoding="utf-8"))
                if p.exists():
                    return review_target(project, str(p), project_id=project_id, place_id=place_id, strict=strict, backends=[] if backends is None else backends)
            raise ValueError(f"'{label}' must be a review report dict, a path to a saved report .json, or a path to a Luau file/folder")

        b, n = get(base, "base"), get(new, "new")

        def keyed(rep: dict) -> dict[str, dict]:
            out = {}
            for kind, key in (("defect", "findings"), ("question", "questions")):
                for f in rep.get(key, []):
                    out[f.get("fp") or f["fingerprint"]] = {**f, "kind": kind}
            return out

        kb, kn = keyed(b), keyed(n)
        brief = lambda f: {"file": f["file"], "line": f["line"], "rule_id": f["rule_id"], "severity": f["severity"], "confidence": f["confidence"], "kind": f["kind"]}
        new_f = [brief(kn[k]) for k in sorted(set(kn) - set(kb))]
        fixed = [brief(kb[k]) for k in sorted(set(kb) - set(kn))]
        changed = [{**brief(kn[k]), "was": f"{kb[k]['severity']}/{kb[k]['confidence']}"} for k in sorted(set(kb) & set(kn))
                   if (kb[k]["severity"], kb[k]["confidence"]) != (kn[k]["severity"], kn[k]["confidence"])]
        same = len(set(kb) & set(kn)) - len(changed)
        regression = any(f["severity"] == "error" and f["kind"] == "defect" for f in new_f)
        return {"summary": f"{len(new_f)} new, {len(fixed)} fixed, {len(changed)} changed, {same} unchanged" + (" - NEW ERRORS" if regression else ""),
                "new": new_f, "fixed": fixed, "changed": changed, "unchanged": same, "regression": regression}

    # ---------------------------------------------------------------- write (dry run by default)
    def suggest_patch(path: str, rule_id: str, project_id: str | None = None, line: int | None = None, strict: bool = False, context: str | None = None,
                      dry_run: bool = True) -> dict[str, Any]:
        """Suggest a fix for one rule as a unified diff (text only; your file is never modified). hits_after shows what the patch leaves. Supported: API001-005, API008, PERF002, STR001, DAT001, COR001. dry_run=false saves the diff under workspace/output/patches/<project> (needs project_id)."""
        scope = PJ.resolve_scope(project, project_id) if (project_id or not dry_run) else None
        p, src = read(path)
        res = P.suggest(src, p.name, rule_id, rules_(), line=line, strict=strict, context=context)
        res.update({"dry_run": dry_run, "modifies_your_file": False})
        dest = project.output_dir / "patches" / (scope.project_id if scope else "_unscoped") / f"{scope_.safe_name(p.name)}.{rule_id}.diff"
        if not dry_run and res["supported"]:
            dest = scope_.resolve_inside(dest, PJ.write_roots(project))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(res["diff"], encoding="utf-8")
            res["saved_to"] = str(dest)
        else:
            res["would_save_to"] = str(dest)
        return res

    def suppress_finding(rule_id: str, reason: str, project_id: str | None = None, place_id: str | None = None, file: str | None = None, line: int | None = None,
                         apply_globally: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """Add a documented suppression (rule, optional file+line, reason >= 8 chars) to the project's ruleset YAML; with place_id to that place's ruleset; apply_globally=true to the global ruleset (every project). Suppressed findings stay listed in reports. Needs project_id."""
        scope = PJ.resolve_scope(project, project_id, place_id)
        rules = rules_()
        if rule_id not in rules and rule_id.split(":")[0] not in B.BACKENDS:
            raise ValueError(f"unknown rule '{rule_id}'. Use list_rules for the ids (backend ids look like selene:unused_variable)")
        if len((reason or "").strip()) < RS.MIN_REASON:
            raise ValueError(f"a suppression needs a reason of at least {RS.MIN_REASON} characters: say why this finding is acceptable")
        entry: dict[str, Any] = {"rule": rule_id, "reason": reason.strip(), "recorded_for": "global" if apply_globally else scope.label()}
        if file:
            entry["file"] = file
        if line is not None:
            if not file:
                raise ValueError("pass `file` together with `line` so the suppression knows which line you mean")
            p, src = read(file)
            lines = src.split("\n")
            if not 1 <= line <= len(lines):
                raise ValueError(f"line {line} is outside {file} ({len(lines)} lines)")
            entry.update({"fingerprint": RS.line_fingerprint(lines[line - 1]), "line": line, "code": lines[line - 1].strip()[:120]})
        if apply_globally:
            layer, target = "global", PJ.global_ruleset_path(project)
        elif scope.place_id:
            layer, target = "place", PJ.place_ruleset_path(project, scope, scope.place_id)
        else:
            layer, target = "project", PJ.project_ruleset_path(project, scope)
        target = scope_.resolve_inside(target, PJ.write_roots(project))
        current = RS.load(target)
        new = RS.add_suppression(current, entry)
        steps = dryrun.Plan(f"Add a suppression of {rule_id} to the {layer} ruleset ({target.name})")
        steps.add("file", str(target), "append one suppression entry (existing YAML comments are not preserved)")
        res = {**steps.to_dict(dry_run), "layer": layer, "entry": entry, "ruleset_path": str(target), "existing_suppressions": len(current["suppressions"]),
               "applies_to": "every project" if apply_globally else (f"place {scope.label()} only" if scope.place_id else f"project {scope.project_id} only (not other projects)")}
        if dry_run:
            res["resulting_yaml"] = RS.dump(new)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(RS.dump(new), encoding="utf-8")
            res["written"] = True
        return res

    def mark_false_positive(rule_id: str, reason: str, project_id: str | None = None, place_id: str | None = None, code: str | None = None, file: str | None = None,
                            line: int | None = None, window: int = 1, apply_globally: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """Record a false positive: saves the normalised code pattern and reason for this project (place_id: that place only; apply_globally: everywhere), so the rule gets lower confidence on similar code in later reviews. Give code, or file+line(+window). Only with the user's verdict. Needs project_id."""
        scope = PJ.resolve_scope(project, project_id, place_id)
        if rule_id not in rules_():
            raise ValueError(f"unknown rule '{rule_id}'. Use list_rules for the ids")
        if len((reason or "").strip()) < RS.MIN_REASON:
            raise ValueError(f"explain why this is a false positive (at least {RS.MIN_REASON} characters): the reason is saved and shown on later reviews")
        if (code is None) == (file is None):
            raise ValueError("pass exactly one of: code (the pattern) or file (+ line)")
        if file is not None:
            if line is None:
                raise ValueError("pass `line` with `file`")
            p, src = read(file)
            lines = src.split("\n")
            if not 1 <= line <= len(lines):
                raise ValueError(f"line {line} is outside {file} ({len(lines)} lines)")
            code = "\n".join(lines[line - 1: line - 1 + max(1, min(window, 20))])
        store = store_for(scope, apply_globally)
        row = store.make_row(rule_id, code or "", reason, file, line, scope.project_id, scope.place_id, apply_globally)
        exists = any(r["id"] == row["id"] for r in store.load())
        thr = settings_()["learning"]["similarity_threshold"]
        res = {"dry_run": dry_run, "pattern_id": row["id"], "rule_id": rule_id, "recorded_for": "global" if apply_globally else scope.label(), "already_saved": exists,
               "normalised_pattern": " ".join(row["tokens"]), "window_lines": row["window_lines"],
               "effect": f"{rule_id} on code >= {thr} similar: one step lower confidence, {'in every project' if apply_globally else 'in ' + scope.label()}", "store": str(store.path)}
        if not dry_run:
            res["written"] = store.add(row)
            run_id = fb.record_run(project, request=f"[{scope.label()}] review false positive {rule_id}: {reason.strip()[:120]}",
                                   constraints={"rule_id": rule_id, "project_id": scope.project_id, "place_id": scope.place_id, "global": apply_globally},
                                   tools=["mark_false_positive"], outputs=[str(store.path)])
            fb.record_decision(project, run_id, "revise", reason=reason.strip(), corrections=[{"dimension": "false_positive", "direction": "less",
                               "note": f"[{scope.label()}] {rule_id}: {reason.strip()} | pattern: {' '.join(row['tokens'])[:200]}"}], allowed_dimensions=None)
            res["feedback_run_id"] = run_id
        return res

    ro = dict(read_only=True)
    return [
        ToolSpec("review_file", review_file, review_file.__doc__, **ro),
        ToolSpec("review_folder", review_folder, review_folder.__doc__, **ro),
        ToolSpec("explain_finding", explain_finding, explain_finding.__doc__, **ro),
        ToolSpec("list_rules", list_rules, list_rules.__doc__, **ro),
        ToolSpec("find_remote_handlers", find_remote_handlers, find_remote_handlers.__doc__, **ro),
        ToolSpec("find_datastore_calls", find_datastore_calls, find_datastore_calls.__doc__, **ro),
        ToolSpec("compare_reviews", compare_reviews, compare_reviews.__doc__, **ro),
        ToolSpec("suggest_patch", suggest_patch, suggest_patch.__doc__, read_only=False, idempotent=True),
        ToolSpec("suppress_finding", suppress_finding, suppress_finding.__doc__, read_only=False, idempotent=True),
        ToolSpec("mark_false_positive", mark_false_positive, mark_false_positive.__doc__, read_only=False, idempotent=False),
    ]
