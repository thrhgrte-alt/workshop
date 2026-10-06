"""Wires the place-map domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations


from . import learning_params, tools as tools_mod
from .domain import mock_extra, places
from .guide_adapter import DomainHooks, config, emit, evals as evals_mod, observe, propose, skillgen, telemetry

INSTRUCTIONS = """\
Roblox place map: a searchable index of ONE named place so you do not re-explore it every session. Workflow: find_past_corrections -> find_in_place (the active place only; one-line
verdict, snapshot age and a STALE flag first) -> get_path_info / who_uses / show_dependencies / list_remotes / summarize_area for detail -> diff_snapshots for what changed.
Data: plan_refresh returns read-only Luau for the hub's execute_luau (it needs list_roblox_studios output and REFUSES when the open Studio place is not the named one); run it in Studio,
then ingest_snapshot (dry run first). Landmarks are found by ROLE from structure, not names (vendor, upgradable_tool, resource_node, collectible, spawn, zone, currency_display,
progression_gate): a request that names a thing returns that exact path only, near matches come back as 'not selected', and you ASK when candidates are borderline; list_candidates then
label_landmark (only with the user's explicit confirm or reject) tunes that project's weights, undoable with undo_label. Cross-place questions use find_across_places, compare_places and
find_shared_code, never find_in_place. Every path is prefixed place::. Every tool needs project_id and place_id; they are never guessed. Parsers of hub output are schema_unverified and
all weights, bands and limits are placeholders: say so. This server never talks to Studio and never publishes. Save the user's corrections with record_run / record_decision."""

EXTENSIONS = {"snapshot": [".json"], "landmark": [".yaml", ".yml", ".json"], "script_summary": [".json"]}


def correction_dimensions(project) -> list[str]:
    return config.load_style(project).get("correction_dimensions", [])


def doctor_checks(project) -> dict:
    try:
        known = places.known(project)
        report = [{"place": f"{p['project_id']}/{p['place_id']}", "synthetic": p["synthetic"], "stable_id": p["stable_id"]} for p in known]
        reg = {"registry": str(places.registry_path(project)), "places": report}
    except Exception as exc:
        reg = {"registry": str(places.registry_path(project)), "error": f"{type(exc).__name__}: {exc}"}
    return {
        "projects": reg,
        "luau_mock": "available" if mock_extra.M.available() else "unavailable (pip install '.[simulate]'): the collector Luau is linted but not run",
        "roblox_studio": "this server never connects to Studio; plan_refresh emits Luau and the hub's execute_luau runs it",
        "parsers": "all hub-output parsers are schema_unverified (no real capture exists yet): see samples/README.md",
        "works_without_studio": ["ingest of saved JSON/text", "search", "roles and scoring", "dependency graph", "diff", "multi-place tools", "evals", "MCP server"],
        "needs_a_real_game": ["real hub output to confirm the parsers", "your places in projects.yaml", "labels for borderline landmarks", "real examples in evals/real/"],
    }


def register_cli(sub, project) -> None:
    skillgen.add_export_skill_command(sub, project, learning_params.skill_kwargs, resolve=learning_params.resolve_scope)
    p = sub.add_parser("places", help="list the projects and places in projects.yaml")
    p.set_defaults(handler=lambda a, pr: emit({"registry": str(places.registry_path(pr)), "places": places.known(pr)}))
    p = sub.add_parser("eval-report", help="run the self-written and real eval sets separately and write a Markdown report")
    p.add_argument("--label", default="report")
    p.set_defaults(handler=_eval_report)
    p = sub.add_parser("propose", help="group repeated label signals into parameter proposals (nothing is applied; see guide-core promote)")
    p.add_argument("--project-id", required=True)
    p.add_argument("--place-id")
    p.set_defaults(handler=_propose)
    p = sub.add_parser("telemetry", help="typical output size and time per tool, from the local telemetry log")
    p.set_defaults(handler=lambda a, pr: emit(telemetry.Telemetry(pr.workspace / "telemetry.jsonl").summary()))


def _eval_report(a, pr) -> int:
    from .evalkit import eval_checks, eval_solver

    split = evals_mod.load_split(pr.root / "evals" / "tasks", pr.root / "evals" / "real")
    rep = evals_mod.run_split(split, eval_solver(pr), eval_checks(pr), label=a.label, root=pr.root)
    out = evals_mod.save_markdown(rep, pr.root / "evals" / "reports")
    pr_res = eval_solver(pr)({"input": {"op": "pr"}})
    lines = ["", "## Role precision and recall", "",
             f"- **Self-written synthetic places** (written by the builder; shows the tool agrees with its author, not real-world precision): precision {pr_res['precision']:.3f} ({pr_res['tp']} tp, {pr_res['fp']} fp), "
             f"recall {pr_res['recall']:.3f} ({pr_res['fn']} missed); decoys selected as confident: {pr_res['decoys_confident'] or 'none'}",
             f"- **Real (`evals/real/`)**: {len(split['real'])} case(s); no real precision or recall exists until you add examples", ""]
    lines += [f"  - `{k}`: tp {v['tp']}, fp {v['fp']}, fn {v['fn']}" + (f" (missed {v['missed']})" if v["missed"] else "") for k, v in pr_res["per_role"].items() if v["tp"] or v["fp"] or v["fn"]]
    out.write_text(out.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    emit({"markdown": str(out), "self_written": f"{rep['self_written']['passed']}/{rep['self_written']['total']}", "real": f"{rep['real']['passed']}/{rep['real']['total']}",
          "note": "self-written evals only show the tool agrees with itself; real = examples you added to evals/real/ (0 until you do)"})
    return 0 if rep["self_written"]["passed"] == rep["self_written"]["total"] and rep["real"]["passed"] == rep["real"]["total"] else 1


def _propose(a, pr) -> int:
    sc = learning_params.resolve_scope(pr, a.project_id, a.place_id)
    log = observe.RunLog(pr.workspace / "learning" / "observations.jsonl")
    patterns = propose.find_patterns(log.runs(sc))
    res = propose.make_proposals(patterns, learning_params.store(pr, a.project_id), sc)
    emit({"patterns": len(patterns), **res, "note": "proposals are not applied: run them through guide-core gate and promote with a named approver"})
    return 0


def eval_solver_hook(project):
    from .evalkit import eval_solver

    return eval_solver(project)


def eval_checks_hook(project) -> dict:
    from .evalkit import eval_checks

    return eval_checks(project)


HOOKS = DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver_hook,
    eval_checks=eval_checks_hook,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
