"""Command line shared by all repositories: ``python -m <package> <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import agentfiles, evals, feedback as fb, retrieval
from .commontools import common_tools, default_embedder
from .doctor import capability_report
from .embeddings import build_index, load_embedder
from .manifest import LibraryStore
from .mcpkit import ToolSpec, call_local, serve
from .project import Project
from .style import load_style, style_brief


@dataclass
class DomainHooks:
    tools: Callable[[Project], list[ToolSpec]]
    instructions: str
    ingest_extensions: dict[str, list[str]] = field(default_factory=dict)  # kind -> file extensions
    eval_solver: Callable[[Project], Callable[[dict], Any]] | None = None
    eval_checks: Callable[[Project], dict] | None = None
    doctor_checks: Callable[[Project], dict] | None = None
    register_cli: Callable[[Any, Project], None] | None = None
    correction_dimensions: Callable[[Project], list[str]] | None = None


def emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def all_tools(project: Project, hooks: DomainHooks) -> list[ToolSpec]:
    dims = hooks.correction_dimensions(project) if hooks.correction_dimensions else None
    return common_tools(project, dims) + hooks.tools(project)


def build_parser(project: Project, hooks: DomainHooks) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"python -m {project.package}", description=f"{project.name} command line")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="report what works on this machine")

    v = sub.add_parser("validate-library", help="validate every manifest entry")
    v.add_argument("--check-files", action="store_true", help="also verify referenced files exist")

    ing = sub.add_parser("ingest", help="create CANDIDATE records for files in a folder (dry-run unless --apply)")
    ing.add_argument("directory")
    ing.add_argument("--kind", required=True, choices=project.asset_kinds)
    ing.add_argument("--ext", nargs="*", help="file extensions (default: per kind)")
    ing.add_argument("--owner", default="unknown", choices=["user", "user-approved", "third-party", "unknown"])
    ing.add_argument("--tag", action="append", default=[])
    ing.add_argument("--project-name")
    ing.add_argument("--apply", action="store_true", help="write candidates to the local library")

    s = sub.add_parser("search", help="search the reference library")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--kind")
    s.add_argument("--tag", action="append", default=[])
    s.add_argument("--trait", action="append", default=[])
    s.add_argument("--palette", nargs="*", default=[])
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--candidates", action="store_true", help="include un-curated candidates")
    s.add_argument("--embedder", help="none | hashing[-DIM] | st:<model> (default: env/index)")

    bi = sub.add_parser("build-index", help="build embedding vectors for the library")
    bi.add_argument("--embedder", default="hashing-512")

    sb = sub.add_parser("style-brief", help="print the compact style brief")
    sb.add_argument("--focus", nargs="*", default=[])

    f = sub.add_parser("feedback", help="record runs, decisions, and curate examples")
    fsub = f.add_subparsers(dest="fcmd", required=True)
    r = fsub.add_parser("record-run")
    r.add_argument("--request", required=True)
    r.add_argument("--output", action="append", default=[])
    r.add_argument("--preview")
    r.add_argument("--retrieved", action="append", default=[])
    d = fsub.add_parser("decide")
    d.add_argument("run_id")
    d.add_argument("--decision", required=True, choices=list(fb.DECISIONS))
    d.add_argument("--reason", default="")
    d.add_argument("--correction", action="append", default=[], help="dimension=note")
    d.add_argument("--rating", type=int)
    c = fsub.add_parser("corrections")
    c.add_argument("query")
    pr = fsub.add_parser("promote", help="add a run to the library; --confirm makes it curated")
    pr.add_argument("run_id")
    pr.add_argument("--kind", required=True, choices=project.asset_kinds)
    pr.add_argument("--title", required=True)
    pr.add_argument("--description", required=True)
    pr.add_argument("--tag", action="append", default=[])
    pr.add_argument("--path", required=True)
    pr.add_argument("--polarity", default="positive", choices=["positive", "negative"])
    pr.add_argument("--confirm", action="store_true")

    e = sub.add_parser("eval", help="evaluation suite")
    esub = e.add_subparsers(dest="ecmd", required=True)
    er = esub.add_parser("run")
    er.add_argument("--label", default="baseline")
    ec = esub.add_parser("compare")
    ec.add_argument("base")
    ec.add_argument("new")
    ek = esub.add_parser("check-results", help="check results an AI agent saved as <task_id>.json")
    ek.add_argument("results_dir")
    ek.add_argument("--label", required=True)

    sa = sub.add_parser("sync-agent-files", help="regenerate CLAUDE.md, GEMINI.md, copilot and skill mirrors")
    sa.add_argument("--check", action="store_true")
    sub.add_parser("check-skills", help="validate skills against the Agent Skills spec")

    sv = sub.add_parser("serve", help="run the MCP server")
    sv.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)

    cl = sub.add_parser("call", help="call a tool directly without an MCP client")
    cl.add_argument("tool", nargs="?")
    cl.add_argument("--json", default="{}", help="JSON object of arguments")

    if hooks.register_cli:
        hooks.register_cli(sub, project)
    return p


def _parse_corrections(items: list[str]) -> list[dict]:
    out = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--correction must look like dimension=note, got '{item}'")
        dim, note = item.split("=", 1)
        out.append({"dimension": dim.strip(), "note": note.strip()})
    return out


def run(project: Project, hooks: DomainHooks, argv: list[str] | None = None) -> int:
    args = build_parser(project, hooks).parse_args(argv)
    store = LibraryStore(project)
    cmd = args.command
    try:
        if cmd == "doctor":
            emit(capability_report(project, hooks.doctor_checks))
        elif cmd == "validate-library":
            res = store.validate_all(check_files=args.check_files)
            emit(res)
            return 0 if res["ok"] else 1
        elif cmd == "ingest":
            exts = args.ext or hooks.ingest_extensions.get(args.kind)
            if not exts:
                raise SystemExit("no default extensions for this kind; pass --ext")
            recs = store.ingest_dir(Path(args.directory), kind=args.kind, extensions=exts, owner=args.owner,
                                    project_name=args.project_name, tags=args.tag, dry_run=not args.apply)
            emit({"dry_run": not args.apply, "candidates": len(recs), "records": recs[:20],
                  "next": "Review, add descriptions/tags/license, then set status to 'curated'."})
        elif cmd == "search":
            emb = load_embedder(args.embedder) if args.embedder else default_embedder(project)
            emit(retrieval.search(store, args.query, filters={"kind": args.kind} if args.kind else None,
                                  tags=args.tag, traits=args.trait, palette=args.palette, k=args.k, embedder=emb,
                                  include_candidates=args.candidates))
        elif cmd == "build-index":
            emit(build_index(project, load_embedder(args.embedder), store))
        elif cmd == "style-brief":
            print(style_brief(load_style(project), args.focus))
        elif cmd == "feedback":
            if args.fcmd == "record-run":
                emit({"run_id": fb.record_run(project, request=args.request, outputs=args.output,
                                              preview=args.preview, retrieved=args.retrieved)})
            elif args.fcmd == "decide":
                dims = hooks.correction_dimensions(project) if hooks.correction_dimensions else None
                emit(fb.record_decision(project, args.run_id, args.decision, reason=args.reason,
                                        corrections=_parse_corrections(args.correction), rating=args.rating,
                                        allowed_dimensions=dims))
            elif args.fcmd == "corrections":
                emit(fb.corrections_for(project, args.query))
            elif args.fcmd == "promote":
                asset = fb.promote_run(project, args.run_id, kind=args.kind, title=args.title,
                                       description=args.description, tags=args.tag, path=args.path,
                                       polarity=args.polarity, confirm=args.confirm)
                emit({"asset_id": asset["id"], "status": asset["status"]})
        elif cmd == "eval":
            tasks_dir = project.root / "evals" / "tasks"
            reports = project.root / "evals" / "reports"
            checks = hooks.eval_checks(project) if hooks.eval_checks else {}
            if args.ecmd == "run":
                if not hooks.eval_solver:
                    raise SystemExit("this repository defines no deterministic eval solver")
                rep = evals.run_suite(evals.load_tasks(tasks_dir), hooks.eval_solver(project), checks,
                                      label=args.label, root=project.root)
                emit({"saved": str(evals.save_report(rep, reports)), "passed": rep["passed"], "total": rep["total"],
                      "failed": [t["id"] for t in rep["tasks"] if not t["passed"]]})
                return 0 if rep["passed"] == rep["total"] else 1
            if args.ecmd == "check-results":
                rep = evals.check_results_dir(evals.load_tasks(tasks_dir), checks, Path(args.results_dir),
                                              label=args.label, root=project.root)
                emit({"saved": str(evals.save_report(rep, reports)), "passed": rep["passed"], "total": rep["total"],
                      "failed": [t["id"] for t in rep["tasks"] if not t["passed"]]})
                return 0 if rep["passed"] == rep["total"] else 1
            if args.ecmd == "compare":
                load = lambda label: json.loads((reports / f"{label}.json").read_text(encoding="utf-8"))
                res = evals.compare_reports(load(args.base), load(args.new))
                emit(res)
                return 1 if res["regressions"] else 0
        elif cmd == "sync-agent-files":
            diffs = agentfiles.sync(project.root, check=args.check)
            emit({"check": args.check, "differences": diffs})
            return 1 if (args.check and diffs) else 0
        elif cmd == "check-skills":
            problems = agentfiles.validate_all_skills(project.root)
            emit({"ok": not problems, "problems": problems})
            return 1 if problems else 0
        elif cmd == "serve":
            serve(project, all_tools(project, hooks), hooks.instructions, args.transport, args.host, args.port)
        elif cmd == "call":
            tools = all_tools(project, hooks)
            if not args.tool:
                emit([{"name": t.name, "read_only": t.read_only, "description": t.description.split("\n")[0]}
                      for t in tools])
            else:
                emit(call_local(tools, args.tool, json.loads(args.json)))
        elif hasattr(args, "handler"):
            return int(args.handler(args, project) or 0)
        return 0
    except (ValueError, FileNotFoundError, PermissionError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
