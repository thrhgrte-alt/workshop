"""The toy tool's eval suite: it passes when healthy, and it FAILS when the toy is broken (a suite that cannot fail proves nothing)."""

import importlib.util
import sys
from pathlib import Path

import pytest

from guide_core import evals, mcpkit, telemetry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))
import toytool  # noqa: E402

spec = importlib.util.spec_from_file_location("run_evals", ROOT / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_evals)


@pytest.fixture(scope="module")
def tasks():
    return evals.load_tasks(ROOT / "evals" / "tasks", strict=True)


def test_at_least_thirty_tasks_covering_the_four_required_areas(tasks):
    assert len(tasks) >= 30
    tags = {t for task in tasks for t in task.get("tags", [])}
    assert {"dryrun", "feedback", "regression", "lint"} <= tags
    for area in ("dryrun", "feedback", "regression", "lint"):
        assert sum(area in t.get("tags", []) for t in tasks) >= 5, area
    assert any("known-bad" in t.get("tags", []) for t in tasks) and any("known-good" in t.get("tags", []) for t in tasks)


def test_no_task_is_vacuous(tasks):
    for t in tasks:
        assert t["checks"] and all("type" in c for c in t["checks"]), t["id"]
        assert len({t["id"] for t in tasks}) == len(tasks)


def test_all_tasks_pass_on_the_healthy_toy(tasks):
    rep = evals.run_suite(tasks, toytool.solve, toytool.CHECKS, label="t", root=ROOT)
    failed = {t["id"]: [c["message"] for c in t["checks"] if not c["passed"]] for t in rep["tasks"] if not t["passed"]}
    assert failed == {} and rep["passed"] == rep["total"] == len(tasks)


def test_unknown_check_type_fails_instead_of_passing(tasks):
    rep = evals.run_suite([{"id": "x", "input": {"op": "check", "project_id": "demo-a", "place_id": "main", "code": "x"}, "checks": [{"type": "no_such_check"}]}],
                          toytool.solve, toytool.CHECKS, label="t", root=ROOT)
    assert rep["passed"] == 0


# Hand-written: which tasks MUST fail when each behaviour is broken (a subset check: more may fail, these may not keep passing).
MUST_FAIL = {
    "lint_off": {"lint-blocks-httpget", "lint-blocks-requestasync", "lint-blocks-websocket", "lint-blocks-loadstring", "lint-blocks-require", "lint-blocks-destroy",
                 "lint-blocks-publish", "lint-blocks-datastore", "lint-blocks-other-httpservice-use", "lint-output-capped-at-ten"},
    "dryrun_writes": {"dryrun-default-writes-nothing"},
    "scope_off": {"scope-refuses-missing-project", "scope-refuses-unknown-project", "scope-refuses-place-of-another-project", "dryrun-needs-a-place"},
    "feedback_leaks": {"fb-never-leaks-another-projects-correction", "fb-place-level-correction-stays-in-its-place"},
    "gate_blind": {"gate-rejects-a-proposal-that-breaks-a-passing-case", "gate-protects-a-past-accepted-correction", "compare-detects-a-regression"},
    "redact_off": {"observe-redacts-paths-and-keys"},
}


@pytest.mark.parametrize("mode", toytool.BROKEN_MODES)
def test_breaking_the_toy_makes_the_expected_tasks_fail(tasks, mode):
    rep = evals.run_suite(tasks, lambda t: toytool.solve(t, mode), toytool.CHECKS, label=mode, root=ROOT)
    failed = {t["id"] for t in rep["tasks"] if not t["passed"]}
    assert MUST_FAIL[mode] <= failed, f"{mode}: still passing: {sorted(MUST_FAIL[mode] - failed)}"
    healthy_pass = {t["id"] for t in evals.run_suite(tasks, toytool.solve, toytool.CHECKS, label="h", root=ROOT)["tasks"] if t["passed"]}
    assert failed <= healthy_pass  # every failure is caused by the mutation, not by a flaky task


def test_every_broken_mode_is_covered_by_a_must_fail_set():
    assert set(MUST_FAIL) == set(toytool.BROKEN_MODES)


def test_compare_flags_the_regression_between_a_healthy_and_a_broken_run(tasks):
    good = evals.run_suite(tasks, toytool.solve, toytool.CHECKS, label="good", root=ROOT)
    bad = evals.run_suite(tasks, lambda t: toytool.solve(t, "lint_off"), toytool.CHECKS, label="bad", root=ROOT)
    cmp = evals.compare_detailed(good, bad)
    assert cmp["regressed"] and "lint-blocks-loadstring" in cmp["regressions"] and cmp["base_score"] == f"{len(tasks)}/{len(tasks)}"
    assert evals.compare_detailed(bad, good)["regressed"] is False


def test_self_written_and_real_are_reported_separately_and_real_is_empty():
    rep = run_evals.run("t")
    assert rep["self_written"]["total"] >= 30 and rep["real"]["total"] == 0 and rep["real_count"] == 0
    md = evals.render_markdown(rep)
    assert "Self-written" in md and "0 cases - no real-world result exists yet" in md


def test_a_dropped_in_real_example_is_run_and_reported_on_its_own(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "mine.yaml").write_text("- id: real-httpget\n  tags: [real]\n  input: {op: check, project_id: demo-a, place_id: main, code: 'game:HttpGet(\"x\")'}\n"
                                    "  checks:\n    - {type: equals, path: blocked, value: true}\n", encoding="utf-8")
    split = evals.load_split(ROOT / "evals" / "tasks", real)
    rep = evals.run_split(split, toytool.solve, toytool.CHECKS, label="t", root=ROOT)
    assert rep["real"]["total"] == 1 and rep["real"]["passed"] == 1 and rep["self_written"]["total"] == len(split["self_written"])


def test_toy_tools_are_labelled_grouped_and_small(tmp_path):
    toy = toytool.Toy(tmp_path)
    specs = toy.tools()
    cat = {c["name"]: c for c in mcpkit.tool_catalog(specs)}
    assert cat["check_script"]["label"] == "read-only" and cat["rename_part"]["label"] == "write" and cat["explain_rule"]["group"] == "rare"
    srv = mcpkit.build_server(toy.project, specs, "toy")  # the toy's tools are valid MCP tools
    import asyncio

    assert sorted(t.name for t in asyncio.run(srv.list_tools())) == ["check_script", "explain_rule", "rename_part"]
    toy.seed_parts(toy.scope("demo-a", "main"), ["Door"])
    res = mcpkit.call_local(specs, "rename_part", {"project_id": "demo-a", "place_id": "main", "old": "Door", "new": "Gate"})
    assert res["dry_run"] is True and toy.parts(toy.scope("demo-a", "main")) == ["Door"]  # the write tool defaults to a dry run
    assert toy.telemetry.summary()["rename_part"]["calls"] == 1


def test_toy_output_size_is_tracked_per_tool(tmp_path):
    toy = toytool.Toy(tmp_path)
    specs = {s.name: s for s in toy.tools()}
    big = "\n".join(["Publish"] * 3 + ["local a = 1"] * 60)
    specs["check_script"].fn(code=big, project_id="demo-a", place_id="main")
    s = toy.telemetry.summary()["check_script"]
    assert 0 < s["chars_median"] < 600 and telemetry.check_budgets(toy.telemetry.summary(), {"check_script": 600}) == []
