"""Parameters, bounded updates, rollback, skill export, the learn commands; the eval suite itself (size, shape, the split report) and guards against vacuous evals."""
import json
import re
from pathlib import Path

import pytest

from visualverify import evalsupport as ES
from visualverify import hooks as hooks_mod
from visualverify import learn as LR
from visualverify import learning_params as LP
from visualverify.guide_adapter import all_tools, cli_run, evals, mcpkit, params as P, scope as S

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"project_id": "demo-stylized-obby", "place_id": "stage-1"}
SC = S.Scope("demo-stylized-obby", "stage-1")


def call(project, tool, **kw):
    return mcpkit.call_local(all_tools(project, hooks_mod.HOOKS), tool, kw)


# --- parameters --------------------------------------------------------------------------------------------------------------------------
def test_every_threshold_is_a_named_parameter_with_its_shipped_default(project):
    meta = LP.raw_thresholds(project)
    specs = {s.name: s for s in LP.param_specs(project)}
    assert set(specs) == set(meta) and len(specs) >= 40
    store = LP.store(project)
    assert all(store.value(n) == meta[n]["default"] for n in meta)
    assert all(s.min is not None and s.max is not None and s.max_step for s in specs.values())
    assert all(store.value(n, SC) == meta[n]["default"] for n in meta)  # nothing learned: every value is the file's default


def test_the_safety_limits_are_locked_and_pbr_conventions_are_flagged(project):
    specs = {s.name: s for s in LP.param_specs(project)}
    locked = {n for n, s in specs.items() if s.locked}
    assert locked == {n for n in specs if n.startswith("limits.")} and "limits.max_pixels" in locked
    store = LP.store(project)
    with pytest.raises(P.LockedParameter):
        store.update("limits.max_pixels", 2_000_000, SC, approved_by="tester", reason="x")
    assert specs["pbr.albedo_lum_min"].verify_against_current_docs and specs["pbr.albedo_lum_max"].verify_against_current_docs


def test_updates_are_bounded_approved_and_roll_back_exactly(project):
    store = LP.store(project)
    with pytest.raises(P.StepTooLarge):
        store.update("silhouette.iou_min", 0.5, SC, approved_by="tester", reason="x")
    with pytest.raises(P.OutOfRange):
        store.update("silhouette.iou_min", 1.5, SC, approved_by="tester", reason="x")
    with pytest.raises(P.ParamError, match="approved_by"):
        store.update("silhouette.iou_min", 0.75, SC, approved_by="claude", reason="x")
    store.update("silhouette.iou_min", 0.75, SC, approved_by="tester", reason="first")
    store.update("silhouette.iou_min", 0.70, SC, approved_by="tester", reason="second")
    assert store.value("silhouette.iou_min", SC) == 0.70 and store.value("silhouette.iou_min", S.Scope("demo-pixel-brawler", "arena")) == 0.8
    store.rollback("silhouette.iou_min", SC, approved_by="tester", reason="undo")
    assert store.value("silhouette.iou_min", SC) == 0.75
    store.rollback("silhouette.iou_min", SC, approved_by="tester", reason="undo again")
    assert store.value("silhouette.iou_min", SC) == 0.8


def test_a_learned_value_changes_the_verdict_and_is_shown(project, images):
    ref = images("ref", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [20, 20, 60, 60]})
    cand = images("cand", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [25, 20, 65, 60]})
    before = call(project, "silhouette_iou", candidate=cand, reference=ref, **MAIN)
    assert [f["check"] for f in before["findings"]] == ["silhouette.iou"] and "learned_thresholds" not in before
    LP.store(project).update("silhouette.iou_min", 0.75, SC, approved_by="tester", reason="accepted 0.78")
    after = call(project, "silhouette_iou", candidate=cand, reference=ref, **MAIN)
    assert after["findings"] == [] and after["learned_thresholds"] == {"silhouette.iou_min": "project"}
    assert any(p.startswith("silhouette.iou 0.7778 >= 0.75 (project)") for p in after["passed"])
    other = call(project, "silhouette_iou", candidate=cand, reference=ref, project_id="demo-pixel-brawler", place_id="arena")
    assert [f["check"] for f in other["findings"]] == ["silhouette.iou"]  # another project keeps the default


def test_evals_ignore_learned_values(project, images):
    LP.store(project).update("silhouette.iou_min", 0.75, SC, approved_by="tester", reason="x")
    tasks = [t for t in evals.load_tasks(ROOT / "evals" / "tasks") if t["id"].startswith("sil-")]
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=ROOT)
    assert rep["passed"] == rep["total"]


# --- export-skill -----------------------------------------------------------------------------------------------------------------------
def test_export_skill_dry_run_then_write(project, tmp_path, capsys):
    LP.store(project).update("silhouette.iou_min", 0.75, SC, approved_by="tester", reason="x")
    assert cli_run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "demo-stylized-obby", "--place", "stage-1"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["problems"] == [] and dry["skill_lines"] < 80
    md = next(v for k, v in dry["files"].items() if k.endswith("SKILL.md"))
    assert "silhouette.iou_min=0.75" in md and "schema_unverified" in md and "measure_image" in md and "placeholder" in md.lower()
    out = tmp_path / "skills"
    assert cli_run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "demo-stylized-obby", "--out", str(out), "--write"]) == 0
    capsys.readouterr()
    assert (out / "visual-verify" / "SKILL.md").exists() and (out / "visual-verify" / "references" / "parameters.md").exists()
    assert "silhouette.iou_min" not in (out / "visual-verify" / "SKILL.md").read_text()  # project-level scope: the place-level value is not in force there
    assert cli_run(project, hooks_mod.HOOKS, ["export-skill", "--scope", "no-such-game"]) == 2
    assert "unknown project_id" in capsys.readouterr().err


# --- the learn commands ------------------------------------------------------------------------------------------------------------------
def _record(project, ref, cand, decision, n=1, **kw):
    ids = []
    for i in range(n):
        rid = call(project, "record_run", request=f"render {i}", **MAIN, measure={"tool": "silhouette_iou", "args": {"candidate": cand, "reference": ref}})["run_id"]
        call(project, "record_decision", run_id=rid, decision=decision, **MAIN, **kw)
        ids.append(rid)
    return ids


def test_learn_commands_end_to_end(project, images, capsys):
    ref = images("ref", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [20, 20, 60, 60]})
    cand = images("cand", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [25, 20, 65, 60]})
    runs = _record(project, ref, cand, "accept", 3)
    scope = ["--project-id", "demo-stylized-obby", "--place-id", "stage-1"]
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "propose", *scope]) == 0
    prop = json.loads(capsys.readouterr().out)["proposals"][0]
    assert (prop["param"], prop["before"], prop["after"]) == ("silhouette.iou_min", 0.8, 0.75) and len(prop["run_ids"]) == 3 and all(r.startswith("obs-") for r in prop["run_ids"]) and len(set(runs)) == 3
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "promote", prop["id"], "--approved-by", "tester", "--confirm"]) == 2  # not gated yet
    assert "passed the eval gate" in capsys.readouterr().err
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "gate", prop["id"], *scope]) == 0
    gate = json.loads(capsys.readouterr().out)
    assert gate["verdict"] == "pass" and gate["suites"]["real"]["total"] == 0 and any("no real examples" in n for n in gate["notes"])
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "promote", prop["id"], "--approved-by", "tester"]) == 2  # no --confirm
    capsys.readouterr()
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "promote", prop["id"], "--approved-by", "tester", "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "promoted"
    assert LP.store(project).value("silhouette.iou_min", SC) == 0.75
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "list", *scope]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["learned"]["silhouette.iou_min"]["value"] == 0.75 and listing["proposals"][0]["status"] == "promoted"
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "rollback", "silhouette.iou_min", *scope, "--approved-by", "tester", "--reason", "undo"]) == 0
    capsys.readouterr()
    assert LP.store(project).value("silhouette.iou_min", SC) == 0.8
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "monitor", *scope, "--since", "2000-01-01T00:00:00"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "insufficient_data"
    assert cli_run(project, hooks_mod.HOOKS, ["learn", "list", "--project-id", "no-such-game"]) == 2


def test_no_tool_can_apply_approve_or_promote_a_change(project):
    for t in all_tools(project, hooks_mod.HOOKS):
        assert not {"approved_by", "confirm_threshold", "apply_proposal"} & set(__import__("inspect").signature(t.fn).parameters), t.name
    assert not [t.name for t in all_tools(project, hooks_mod.HOOKS) if re.search(r"propose|promote_proposal|rollback|update_threshold|set_threshold", t.name)]


def test_the_observation_log_is_redacted_and_local(project, images):
    ref = images("ref", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [20, 20, 60, 60]})
    rid = call(project, "record_run", request="check /home/user/secret/game.rbxl token=abcd1234efgh", **MAIN, measure={"tool": "silhouette_iou", "args": {"candidate": ref, "reference": ref}})["run_id"]
    call(project, "record_decision", run_id=rid, decision="accept", **MAIN)
    text = (project.workspace / "learning" / "observations.jsonl").read_text()
    assert "/home/user" not in text and "abcd1234efgh" not in text and "<path>" in text


# --- the eval suite -------------------------------------------------------------------------------------------------------------------
def test_eval_suite_is_big_enough_and_not_trivially_shaped():
    split = evals.load_split(ROOT / "evals" / "tasks", ROOT / "evals" / "real")
    tasks = split["self_written"]
    assert len(tasks) >= 100 and split["real"] == []
    assert all(t["checks"] for t in tasks)
    ids = {t["id"] for t in tasks}
    for prefix in ("sil-", "pal-", "val-", "edge-", "tile-", "pbr-", "diff-", "cmp-", "scope-", "profile-", "hub-", "learn-", "budget-", "honest-", "det-", "files-"):
        assert sum(i.startswith(prefix) for i in ids) >= 2, prefix
    tags = {tg for t in tasks for tg in t.get("tags", [])}
    assert {"known-good", "known-bad", "refusal", "hand-computed", "isolation", "budget"} <= tags
    assert sum("refusal" in t.get("tags", []) for t in tasks) >= 20


def test_every_tool_has_an_output_budget_eval(project):
    ids = {t["id"] for t in evals.load_tasks(ROOT / "evals" / "tasks")}
    missing = [t.name for t in all_tools(project, hooks_mod.HOOKS) if f"budget-{t.name.replace('_', '-')}" not in ids]
    assert not missing, f"add an output-size budget eval for: {missing}"


def test_tasks_with_hand_computed_expectations_say_how(project):
    for f in (ROOT / "evals" / "tasks").glob("*.yaml"):
        text = f.read_text()
        for block in text.split("\n- id: ")[1:]:
            if "hand-computed" in block.split("\n")[1]:
                assert "#" in block, f"{f.name}: a hand-computed task must show the arithmetic in a comment"


def test_the_whole_suite_passes_and_matches_the_baseline(project):
    tasks = evals.load_tasks(ROOT / "evals" / "tasks", strict=True)
    fresh = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="t", root=ROOT)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((ROOT / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp


def test_eval_report_keeps_self_written_and_real_apart(project, capsys, tmp_path):
    assert cli_run(project, hooks_mod.HOOKS, ["eval-report", "--label", "t-split", "--compare", "baseline"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "agrees with itself" in out["self_written"] and out["real"].startswith("0 cases") and out["failed"] == []
    for f in (ROOT / "evals" / "reports").glob("t-split*"):
        f.unlink()


def test_compare_flags_a_regression():
    rep = {"label": "a", "passed": 1, "total": 2, "tasks": [{"id": "x", "passed": True, "checks": []}, {"id": "y", "passed": False, "checks": []}]}
    worse = {"label": "b", "passed": 0, "total": 2, "tasks": [{"id": "x", "passed": False, "checks": [{"passed": False, "message": "boom"}]}, {"id": "y", "passed": False, "checks": []}]}
    cmp = evals.compare_detailed(rep, worse)
    assert cmp["regressions"] == ["x"] and cmp["regression_details"]["x"] == ["boom"]


def _run_subset(project, prefixes):
    tasks = [t for t in evals.load_tasks(ROOT / "evals" / "tasks") if t["id"].startswith(tuple(prefixes))]
    rep = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="broken", root=ROOT)
    return {t["id"] for t in rep["tasks"] if not t["passed"]}, len(tasks)


def test_evals_fail_when_the_silhouette_judge_is_broken(project, monkeypatch):
    """Vacuity guard: a checker that always reports a perfect overlap must fail the known-bad silhouette evals."""
    from visualverify.domain import silhouette as SL

    real = SL.compare_masks
    monkeypatch.setattr(SL, "compare_masks", lambda a, b: {**real(a, b), "iou": 1.0, "centroid_offset": 0.0, "bbox_offset": 0.0})
    failed, n = _run_subset(project, ["sil-"])
    assert {"sil-shifted-10px-hand-iou-0.6", "sil-disjoint-boxes-zero-iou", "sil-ellipse-in-box-iou-is-pi-over-4"} <= failed and len(failed) < n


def test_evals_fail_when_every_check_passes(project, monkeypatch):
    from visualverify.domain import checks as C

    monkeypatch.setattr(C, "OPS", {k: (lambda v, l: True) for k in C.OPS})
    failed, _ = _run_subset(project, ["val-", "edge-", "pbr-", "tile-", "diff-"])
    assert {"val-low-contrast-halves-fail", "edge-flat-image-is-too-flat", "pbr-albedo-too-dark", "tile-gradient-seam-ratio-255-hand", "diff-large-change-fails-tolerance"} <= failed


def test_evals_fail_when_the_seam_and_edge_measurements_are_broken(project, monkeypatch):
    from visualverify.domain import pixels as PX
    from visualverify.domain import tiling as TL

    monkeypatch.setattr(TL, "_axis_ratio", lambda t, axis: {"ratio": 0.0, "wrap_step": 0.0, "inside_step": 1.0, "mean_step": 1.0})
    monkeypatch.setattr(PX, "edge_map", lambda lum, threshold: __import__("numpy").zeros(lum.shape, bool))
    failed, _ = _run_subset(project, ["tile-", "edge-"])
    assert {"tile-gradient-seam-ratio-255-hand", "tile-cropped-from-larger-image-is-seamed", "edge-vertical-step-density-0.02", "edge-white-noise-is-too-noisy"} <= failed


def test_evals_fail_when_the_scope_and_path_guards_are_removed(project, monkeypatch):
    from visualverify import tools as T
    from visualverify.domain import imageio as IO

    real = T.S.require_scope
    monkeypatch.setattr(T.S, "require_scope", lambda pid, place=None, **kw: real(pid or "demo-stylized-obby", place, **kw))  # "helpfully" guesses the project
    monkeypatch.setattr(IO.S, "resolve_inside", lambda p, roots: Path(p).resolve())  # no confinement
    failed, _ = _run_subset(project, ["scope-"])
    assert {"scope-every-tool-refuses-without-project", "scope-image-outside-the-allowed-folders-is-refused", "scope-parent-traversal-is-refused", "scope-other-projects-image-folder-is-refused"} <= failed


def test_evals_fail_when_dry_run_writes(project, monkeypatch):
    from visualverify import tools as T

    real = T.dryrun.run_plan
    monkeypatch.setattr(T.dryrun, "run_plan", lambda plan, applier=None, *, apply=False, **kw: real(plan, applier, apply=True, **kw))
    failed, _ = _run_subset(project, ["scope-write-tools-default-to-dry-run"])
    assert failed == {"scope-write-tools-default-to-dry-run"}


def test_evals_fail_when_learning_signals_are_broken(project, monkeypatch):
    from visualverify.domain import learning as LN

    monkeypatch.setattr(LN, "signals_for", lambda decision, rows, corrections, meta: ([], []))
    failed, _ = _run_subset(project, ["learn-three-accepts", "learn-rejecting", "learn-gate-then"])
    assert {"learn-three-accepts-of-a-failing-check-propose-relaxing-it", "learn-rejecting-a-passing-result-tightens-the-narrowest-check", "learn-gate-then-approved-promotion-then-rollback"} <= failed


def test_evals_fail_when_the_gate_is_blind(project, monkeypatch):
    """A gate that applies nothing in memory sees no regression: the revise case must then fail the eval that expects a rejection."""
    monkeypatch.setattr(ES, "solver_for", lambda project, scope: (lambda prop: (lambda task: ES.solve(task, project=project, scope=scope, overrides={}, use_store=True))))
    failed, _ = _run_subset(project, ["learn-gate-rejects"])
    assert failed == {"learn-gate-rejects-a-proposal-that-undoes-a-revise"}


def test_evals_fail_when_wording_gets_a_verdict(project, monkeypatch):
    from visualverify.domain import checks as C

    real = C.verdict
    monkeypatch.setattr(C, "verdict", lambda *a, **k: real(*a, **k) + " The image looks good.")
    failed, _ = _run_subset(project, ["honest-"])
    assert {"honest-measure-image-wording", "honest-compare-wording", "honest-pbr-wording"} <= failed


def test_evals_fail_when_output_grows_past_the_budget(project, monkeypatch):
    from visualverify.domain import engine as E

    real = E.render
    monkeypatch.setattr(E, "render", lambda *a, **k: {**real(*a, **k), "padding": "x" * 5000})
    failed, _ = _run_subset(project, ["budget-measure-image", "budget-check-tiling"])
    assert failed == {"budget-measure-image", "budget-check-tiling"}


def test_threshold_overrides_reach_the_measurement_unless_the_task_pins_the_defaults(project):
    """What the gate relies on: a proposal's value is applied in memory to every unpinned task, and pinned tasks keep the shipped defaults."""
    tasks = {t["id"]: t for t in evals.load_tasks(ROOT / "evals" / "tasks")}
    free = ES.solve(tasks["sil-shifted-2px-hand-iou-passes"], project=project, overrides={"silhouette.iou_min": 0.95})
    pinned = ES.solve(tasks["sil-shifted-10px-hand-iou-0.6"], project=project, overrides={"silhouette.iou_min": 0.95})
    row = lambda r: next(x for x in r["rows"] if x["id"] == "silhouette.iou")  # noqa: E731
    assert row(free)["limit"] == 0.95 and row(free)["passed"] is False  # 0.9048 now fails the tightened limit
    assert row(pinned)["limit"] == 0.8
    assert [t for t in tasks.values() if t["input"].get("pin_defaults")], "some tasks must pin the defaults"
