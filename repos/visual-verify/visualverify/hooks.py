"""Wires visual-verify into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy
import PIL

from . import evalsupport as ES
from . import learn as LR
from . import learning_params as LP
from . import tools as tools_mod
from .domain import projects as PJ
from .guide_adapter import DomainHooks, all_tools, call_local, config, emit, evals as ev, skillgen, scope as S

INSTRUCTIONS = """\
Visual measurement and verification. A model's reading of a screenshot is imprecise: MEASURE instead, and reason from the numbers.
Workflow: get_style_brief -> find_past_corrections (what this user already accepted or rejected) -> measure_image / compare_to_reference / silhouette_iou / palette_distance /
check_tiling / check_pbr_ranges / diff_images (Pillow + NumPy only, deterministic) -> report every number WITH the limit it was compared with and whether it passed ->
render_diff_image for a picture (dry run first) -> record_run (it re-measures, so the numbers are stored exactly) and record_decision with the user's own words.
RULES: never say an image looks good or bad; say what was measured and what was NOT (taste, anatomy, perspective, lighting realism, whether it is the right object).
Thresholds are PLACEHOLDERS until the user's profile or accept/reject history replaces them; say so. Every tool needs project_id (and place_id where it applies) from projects.yaml
and refuses without one. Reference images stay on this machine and are never uploaded; profiles store numbers only. Masks assume a plain background or alpha: warn when they do not.
Screenshots come from the hub's screen_capture: save the result to disk, then ingest_capture (dry run, then apply); that parser is schema_unverified. This server never connects to
Studio or Blender, never publishes, and never changes a threshold: thresholds change only through the `learn` commands (propose, gate, your explicit approval)."""

EXTENSIONS = {"target_profile": [".json"], "reference_image": [".png", ".jpg", ".jpeg", ".webp"], "measurement_case": [".png"]}


def correction_dimensions(project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project) -> dict:
    try:
        reg = PJ.load_registry(project)
        projects = {"registry": str(PJ.registry_path(project)), "projects": PJ.known(reg)}
    except Exception as exc:
        projects = {"registry": str(PJ.registry_path(project)), "error": f"{type(exc).__name__}: {exc}"}
    return {"projects": projects, "numpy": numpy.__version__, "pillow": PIL.__version__, "thresholds": len(LP.raw_thresholds(project)),
            "model_calls": "none: Pillow and NumPy only", "network": "none: this package imports no network code",
            "needs_a_real_game": ["real screenshots (hub screen_capture; parser is schema_unverified)", "your reference images", "your target look: thresholds are placeholders"]}


def eval_solver(project):
    return lambda task: ES.solve(task, project=project)


def eval_checks(project) -> dict:
    return ES.checks()


# --- CLI ---------------------------------------------------------------------------------------------------------------------------------
def _call(pr, tool: str, **kw) -> dict:
    return call_local(all_tools(pr, HOOKS), tool, {k: v for k, v in kw.items() if v is not None})


def _verdict_exit(res: dict) -> int:
    return 1 if res.get("findings") else 0


def _add_scope(p) -> None:
    p.add_argument("--project-id", required=True)
    p.add_argument("--place-id")
    p.add_argument("--detail", action="store_true")


def register_cli(sub, project) -> None:
    def simple(name, helptext, tool, args, extra=()):
        p = sub.add_parser(name, help=helptext)
        for a in args:
            p.add_argument(a)
        for flag, kw in extra:
            p.add_argument(flag, **kw)
        _add_scope(p)

        def handler(a, pr):
            kws = {"project_id": a.project_id, "place_id": a.place_id, "detail": a.detail}
            for k in [x for x in vars(a) if x not in ("command", "handler", "project_id", "place_id", "detail")]:
                kws[k] = getattr(a, k)
            res = _call(pr, tool, **kws)
            emit(res)
            return _verdict_exit(res)

        p.set_defaults(handler=handler)

    simple("measure", "measure one image; exit 1 if any check failed", "measure_image", ["path"], [("--profile", {}), ("--levels", {"type": int, "default": 3})])
    simple("compare", "compare a candidate with a reference image; exit 1 if any check failed", "compare_to_reference", ["candidate"], [("--reference", {"required": True})])
    simple("iou", "silhouette IoU of two images", "silhouette_iou", ["candidate", "reference"])
    simple("tiling", "seam and repetition of a tile", "check_tiling", ["path"])
    simple("diff", "numeric before/after difference", "diff_images", ["before", "after"], [("--resize", {"action": "store_true"})])
    p = sub.add_parser("pbr", help="check PBR map ranges; exit 1 if any check failed")
    for k in ("albedo", "roughness", "metalness", "normal"):
        p.add_argument(f"--{k}")
    _add_scope(p)
    p.set_defaults(handler=lambda a, pr: _emit_exit(_call(pr, "check_pbr_ranges", project_id=a.project_id, place_id=a.place_id, detail=a.detail, albedo=a.albedo, roughness=a.roughness,
                                                           metalness=a.metalness, normal=a.normal)))
    p = sub.add_parser("projects", help="list the projects and places in projects.yaml")
    p.set_defaults(handler=lambda a, pr: emit({"registry": str(PJ.registry_path(pr)), "projects": PJ.known(PJ.load_registry(pr))}) or 0)
    skillgen.add_export_skill_command(sub, project, LP.skill_kwargs, resolve=LP.resolve_scope)
    _register_eval_report(sub)
    _register_learn(sub)


def _emit_exit(res: dict) -> int:
    emit(res)
    return _verdict_exit(res)


def _register_eval_report(sub) -> None:
    p = sub.add_parser("eval-report", help="run the self-written AND real eval sets and report them separately (saves <label>.json = self-written, <label>.real.json, <label>.md)")
    p.add_argument("--label", default="report")
    p.add_argument("--compare", help="label of a saved self-written report to compare with")

    def handler(a, pr) -> int:
        split = ev.load_split(pr.root / "evals" / "tasks", pr.root / "evals" / "real")
        rep = ev.run_split(split, eval_solver(pr), eval_checks(pr), label=a.label, root=pr.root)
        reports = pr.root / "evals" / "reports"
        ev.save_report(rep["self_written"] | {"label": a.label}, reports)
        ev.save_report(rep["real"] | {"label": f"{a.label}.real"}, reports)
        cmp = None
        if a.compare:
            cmp = ev.compare_detailed(json.loads((reports / f"{a.compare}.json").read_text(encoding="utf-8")), rep["self_written"] | {"label": a.label})
        ev.save_markdown(rep, reports, cmp)
        emit({"label": a.label, "self_written": f"{rep['self_written']['passed']}/{rep['self_written']['total']} (written by the builder: shows the tool agrees with itself)",
              "real": (f"{rep['real']['passed']}/{rep['real']['total']}" if rep["real_count"] else "0 cases: no real-world result exists yet; see evals/real/README.md"),
              "failed": [t["id"] for t in rep["self_written"]["tasks"] + rep["real"]["tasks"] if not t["passed"]], "compare": cmp})
        bad = rep["self_written"]["passed"] != rep["self_written"]["total"] or rep["real"]["passed"] != rep["real"]["total"] or bool(cmp and cmp["regressed"])
        return 1 if bad else 0

    p.set_defaults(handler=handler)


def _scope_args(p) -> None:
    p.add_argument("--project-id", required=True)
    p.add_argument("--place-id")


def _register_learn(sub) -> None:
    lp = sub.add_parser("learn", help="the threshold improvement loop: list, propose, gate, promote, reject, rollback, monitor (nothing is applied without your approval)")
    ls = lp.add_subparsers(dest="lcmd", required=True)

    def scope_of(a, pr):
        return PJ.resolve(pr, a.project_id, a.place_id)

    p = ls.add_parser("list", help="learned values and proposals for a scope")
    _scope_args(p)
    p.set_defaults(handler=lambda a, pr: emit(LR.listing(pr, scope_of(a, pr))) or 0)
    p = ls.add_parser("propose", help="group the logged accept/reject signals into proposals (stores them; applies nothing)")
    _scope_args(p)
    p.add_argument("--min-runs", type=int, default=3)
    p.add_argument("--min-rate", type=float, default=0.5)
    p.set_defaults(handler=lambda a, pr: emit(LR.propose(pr, scope_of(a, pr), a.min_runs, a.min_rate)) or 0)
    p = ls.add_parser("gate", help="run a proposal against all evals and past accepted cases")
    p.add_argument("proposal")
    _scope_args(p)

    def gate_h(a, pr) -> int:
        res = LR.gate(pr, scope_of(a, pr), a.proposal)
        emit(res)
        return 0 if res["verdict"] == "pass" else 1

    p.set_defaults(handler=gate_h)
    p = ls.add_parser("promote", help="apply ONE gate-passed proposal as a new parameter version (needs --approved-by NAME --confirm)")
    p.add_argument("proposal")
    p.add_argument("--approved-by", required=True)
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(handler=lambda a, pr: emit(LR.promote(pr, a.proposal, a.approved_by, a.confirm)) or 0)
    p = ls.add_parser("reject", help="record that you said no to a proposal (kept, never deleted)")
    p.add_argument("proposal")
    p.add_argument("--by", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(handler=lambda a, pr: emit(LR.reject(pr, a.proposal, a.by, a.reason)) or 0)
    p = ls.add_parser("rollback", help="restore the previous version of a threshold for a scope")
    p.add_argument("param")
    _scope_args(p)
    p.add_argument("--approved-by", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--to-version", type=int)
    p.set_defaults(handler=lambda a, pr: emit(LR.rollback(pr, a.param, scope_of(a, pr), a.approved_by, a.reason, a.to_version)) or 0)
    p = ls.add_parser("monitor", help="compare the runs after a change with the runs before it")
    _scope_args(p)
    p.add_argument("--since", required=True, help="ISO time of the promotion")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(handler=lambda a, pr: emit(LR.monitor(pr, scope_of(a, pr), a.since, a.n)) or 0)


HOOKS = DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver,
    eval_checks=eval_checks,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
