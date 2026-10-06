"""MCP tools end to end, projects/places, safety defaults, evals (and that they are not vacuous), token budgets and repository hygiene."""
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from luaurev import hooks as hooks_mod
from luaurev import server as server_mod
from guide_core import agentfiles, evals
from guide_core.cli import all_tools, run
from guide_core.mcpkit import build_server, call_local
from guide_core.style import load_style, validate_style
from luaurev.domain import detectors
from luaurev.domain.review import get_rules
from luaurev.tools import RARE_TOOLS

ROOT = Path(__file__).resolve().parents[1]
SBX = "example-sandbox"


def tools(project):
    return all_tools(project, hooks_mod.HOOKS)


def call(project, tool, **kw):
    return call_local(tools(project), tool, kw)


NEG = "examples/negative/"


def ex(name):
    return str(ROOT / name)


# ---------------------------------------------------------------- tools and scoping
def test_review_file_brief_output_leads_with_a_summary(project):
    out = call(project, "review_file", path=ex(NEG + "sec002_client_price.server.luau"), project_id=SBX, backends=[])
    assert list(out)[0] == "summary" and out["summary"].startswith("[example-sandbox] ERRORS: 1 error")
    assert out["findings"][0]["rule_id"] == "SEC002" and out["findings"][0]["line"] == 16 and out["findings"][0]["source"] == "own"
    assert out["backends"]["selene"] == "not_requested" and out["project"]["project_id"] == SBX
    assert "table" not in out and "fingerprint" not in out["findings"][0]
    full = call(project, "review_file", path=ex(NEG + "sec002_client_price.server.luau"), project_id=SBX, backends=[], detail="full")
    assert "table" in full and "| SEC002 |" in full["table"]
    with pytest.raises(ValueError, match="detail"):
        call(project, "review_file", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX, detail="huge")


def test_questions_are_not_defects(project):
    out = call(project, "review_file", path=ex(NEG + "sec003_no_cooldown.server.luau"), project_id=SBX, backends=[])
    assert out["findings"] == [] and [q["rule_id"] for q in out["questions"]] == ["SEC003"]
    assert out["summary"].startswith("[example-sandbox] CLEAN") and "1 question" in out["summary"]


def test_clean_review_states_what_was_and_was_not_checked(project, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = call(project, "review_file", path=ex("examples/positive/trap_typed_module.luau"), project_id=SBX)
    assert out["findings"] == [] and out["checked"]["own_rules"] >= 30 and "security" in out["checked"]["categories"]
    assert set(out["backends"]) == {"luau-analyze", "selene", "stylua"} and all(v.startswith("missing") for v in out["backends"].values())
    assert any("luau-analyze not installed" in x for x in out["not_checked"]) and "MISSING: luau-analyze, selene, stylua" in out["summary"]


def test_missing_or_unknown_project_is_refused_by_every_project_tool(project):
    f = ex(NEG + "api001_wait.server.luau")
    for tool, args in (("review_file", {"path": f}), ("review_folder", {"path": ex("examples/places")}), ("explain_finding", {"rule_id": "API001"}),
                       ("suppress_finding", {"rule_id": "API001", "reason": "a long enough reason"}), ("mark_false_positive", {"rule_id": "API001", "reason": "a long enough reason", "code": "wait(1) x y"})):
        with pytest.raises(ValueError, match="project_id is required"):
            call(project, tool, **args)
        with pytest.raises(ValueError, match="unknown project_id 'nope'"):
            call(project, tool, project_id="nope", **args)
    with pytest.raises(ValueError, match="does not belong"):
        call(project, "review_file", path=f, project_id="example-tycoon", place_id="lobby")
    assert not (project.workspace / "feedback").exists()  # nothing was written on the way to the refusal


def test_stateless_tools_work_without_a_project_but_validate_one_if_given(project):
    assert call(project, "list_rules", category="security")["count"] == 6
    with pytest.raises(ValueError, match="unknown project_id"):
        call(project, "list_rules", project_id="nope")
    assert call(project, "suggest_patch", path=ex(NEG + "api001_wait.server.luau"), rule_id="API001")["supported"]
    with pytest.raises(ValueError, match="project_id is required"):
        call(project, "suggest_patch", path=ex(NEG + "api001_wait.server.luau"), rule_id="API001", dry_run=False)


def test_suppression_in_project_a_is_not_applied_in_project_b(project):
    f = ex(NEG + "api001_wait.server.luau")
    a = call(project, "review_file", path=f, project_id="example-obby", backends=[])
    b = call(project, "review_file", path=f, project_id="example-tycoon", backends=[])
    assert a["findings"] == [] and a["suppressed"] == 1 and any("legacy scheduler" in x for x in a["overrides"]["applied"])
    assert [x["rule_id"] for x in b["findings"]] == ["API001"] and b["suppressed"] == 0 and b["overrides"]["applied"] == []
    assert any("unmatched" in k for k in b["overrides"]) and "[global] API009" in b["overrides"]["unmatched"][0]


def test_review_folder_labels_each_finding_with_its_place(project):
    out = call(project, "review_folder", path=ex("examples/places"), project_id="example-obby", backends=[])
    assert {x["place"] for x in out["findings"] + out["questions"]} == {"stage-1"} and out["suppressed"] == 1
    out2 = call(project, "review_folder", path=ex("examples/places"), project_id="example-tycoon", backends=[])
    assert all("place" not in x for x in out2["findings"])  # no registry place matches these folders: labelled nothing, not guessed
    full = call(project, "review_folder", path=ex("examples/places"), project_id="example-obby", backends=[], detail="full")
    assert {f["place"] for f in full["files"] and [{"place": r["place"]} for r in full["files"]]} == {"lobby", "stage-1"}


def test_writes_go_to_the_right_layer_and_dry_run_is_the_default(tmp_project):
    p = tmp_project
    f = str(p.root / NEG / "api001_wait.server.luau")
    obby_rs = p.root / "projects/example-tycoon/ruleset.yaml"
    before = obby_rs.read_text()
    dry = call(p, "suppress_finding", rule_id="API001", reason="legacy script kept as is for now", project_id="example-tycoon", file=f, line=3)
    assert dry["dry_run"] and obby_rs.read_text() == before and dry["layer"] == "project" and "resulting_yaml" in dry and dry["applies_to"].endswith("(not other projects)")
    done = call(p, "suppress_finding", rule_id="API001", reason="legacy script kept as is for now", project_id="example-tycoon", file=f, line=3, dry_run=False)
    assert done["written"] and yaml.safe_load(obby_rs.read_text())["suppressions"][0]["fingerprint"]
    assert call(p, "review_file", path=f, project_id="example-tycoon", backends=[])["findings"] == []
    assert [x["rule_id"] for x in call(p, "review_file", path=f, project_id=SBX, backends=[])["findings"]] == ["API001"]
    # place layer, then global layer
    call(p, "suppress_finding", rule_id="API002", reason="place specific reason ok", project_id="example-obby", place_id="stage-1", dry_run=False)
    assert yaml.safe_load((p.root / "projects/example-obby/places/stage-1.ruleset.yaml").read_text())["suppressions"][0]["rule"] == "API002"
    call(p, "suppress_finding", rule_id="API003", reason="accepted everywhere for now", project_id=SBX, apply_globally=True, dry_run=False)
    assert any(s["rule"] == "API003" for s in yaml.safe_load((p.root / "projects/_global/ruleset.yaml").read_text())["suppressions"])
    d3 = ex(NEG + "api003_delay.server.luau")
    for pid in (SBX, "example-tycoon", "example-obby"):
        assert call(p, "review_file", path=str(p.root / NEG / "api003_delay.server.luau"), project_id=pid, backends=[])["findings"] == []


def test_suppress_refuses_bad_input(tmp_project):
    with pytest.raises(ValueError, match="reason of at least"):
        call(tmp_project, "suppress_finding", rule_id="API001", reason="meh", project_id=SBX)
    with pytest.raises(ValueError, match="unknown rule"):
        call(tmp_project, "suppress_finding", rule_id="NOPE1", reason="a long enough reason", project_id=SBX)
    with pytest.raises(ValueError, match="pass `file`"):
        call(tmp_project, "suppress_finding", rule_id="API001", reason="a long enough reason", project_id=SBX, line=3)


def test_false_positive_loop_per_project_and_retrieval(tmp_project):
    p = tmp_project
    f = str(p.root / NEG / "sec001_unvalidated_remote.server.luau")
    base = call(p, "review_file", path=f, project_id="example-obby", backends=[])
    assert base["findings"][0]["confidence"] == "medium" and base["learned"] == 0
    dry = call(p, "mark_false_positive", rule_id="SEC001", reason="validated by the Remotes wrapper", project_id="example-obby", file=f, line=9)
    assert dry["dry_run"] and not (p.workspace / "feedback").exists()
    call(p, "mark_false_positive", rule_id="SEC001", reason="validated by the Remotes wrapper", project_id="example-obby", file=f, line=9, dry_run=False)
    after = call(p, "review_file", path=f, project_id="example-obby", backends=[])
    assert after["findings"] == [] and after["questions"][0]["rule_id"] == "SEC001" and after["questions"][0]["confidence"] == "low" and after["learned"] == 1
    other = call(p, "review_file", path=f, project_id="example-tycoon", backends=[])
    assert other["findings"][0]["confidence"] == "medium"  # recorded for obby only
    call(p, "mark_false_positive", rule_id="SEC001", reason="validated by the Remotes wrapper", project_id="example-obby", file=f, line=9, apply_globally=True, dry_run=False)
    assert call(p, "review_file", path=f, project_id="example-tycoon", backends=[])["questions"][0]["confidence"] == "low"
    again = call(p, "mark_false_positive", rule_id="SEC001", reason="validated by the Remotes wrapper", project_id="example-obby", file=f, line=9)
    assert again["already_saved"]
    corr = call(p, "find_past_corrections", request="SEC001 remote handler validation")["corrections"]
    assert corr and "Remotes wrapper" in corr[0]["reason"]
    expl = call(p, "explain_finding", rule_id="SEC001", project_id="example-obby")
    assert expl["known_false_positives"] and expl["example"] and expl["good"]


def test_suggest_patch_never_edits_the_file(project, tmp_path):
    src = tmp_path / "a.server.luau"
    src.write_text("--!strict\nwait(1)\n")
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    dry = call(project, "suggest_patch", path=str(src), rule_id="API001")
    assert dry["dry_run"] and dry["diff"].startswith("--- a/a.server.luau") and dry["modifies_your_file"] is False and "saved_to" not in dry
    real = call(project, "suggest_patch", path=str(src), rule_id="API001", project_id=SBX, dry_run=False)
    assert Path(real["saved_to"]).read_text() == real["diff"] and "patches/example-sandbox" in real["saved_to"]
    assert hashlib.sha256(src.read_bytes()).hexdigest() == digest
    assert call(project, "suggest_patch", path=ex(NEG + "sec004_loadstring.server.luau"), rule_id="SEC004")["supported"] is False
    with pytest.raises(ValueError, match="nothing to patch"):
        call(project, "suggest_patch", path=ex("examples/positive/trap_task_library.server.luau"), rule_id="API001")


def test_reviewing_never_modifies_files(project):
    files = sorted((ROOT / "examples").rglob("*.luau"))
    before = {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    call(project, "review_folder", path=ex("examples/negative"), project_id=SBX, backends=[], strict=True)
    assert {f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files} == before


def test_explain_finding_and_list_rules(project):
    e = call(project, "explain_finding", rule_id="SEC002", project_id=SBX, file=ex(NEG + "sec002_client_price.server.luau"), line=16)
    assert e["own_rule"] and e["findings_in_file"][0]["line"] == 16 and any(c["line"] == 16 for c in e["code_context"])
    assert call(project, "explain_finding", rule_id="selene:unused_variable", project_id=SBX)["own_rule"] is False
    with pytest.raises(ValueError, match="Did you mean"):
        call(project, "explain_finding", rule_id="SEC02", project_id=SBX)
    with pytest.raises(ValueError, match="no SEC002 finding on line 3"):
        call(project, "explain_finding", rule_id="SEC002", project_id=SBX, file=ex(NEG + "sec002_client_price.server.luau"), line=3)
    lr = call(project, "list_rules", confidence="low")
    assert {r["id"] for r in lr["rules"]} == {"API006", "API007", "DAT006", "DAT008", "LEAK004", "PERF005", "SEC003"}
    assert call(project, "list_rules", details=True)["rules"][0]["explanation"]
    with pytest.raises(ValueError, match="unknown category"):
        call(project, "list_rules", category="nope")


def test_compare_reviews_tool(project):
    out = call(project, "compare_reviews", base=ex(NEG + "api001_wait.server.luau"), new=ex("examples/positive/trap_task_library.server.luau"), project_id=SBX)
    assert out["fixed"] and out["fixed"][0]["rule_id"] == "API001" is not None and out["new"] == [] or out["summary"]
    r1 = call(project, "review_file", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX, backends=[])
    r2 = call(project, "review_file", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX, backends=[])
    same = call(project, "compare_reviews", base=r1, new=r2)
    assert same["unchanged"] == 1 and not same["new"] and not same["regression"]
    with pytest.raises(ValueError, match="must be a review report"):
        call(project, "compare_reviews", base="/no/such", new=r1)


def test_review_refuses_bad_paths_and_contexts(project):
    with pytest.raises(ValueError, match="not a file"):
        call(project, "review_file", path=ex("examples"), project_id=SBX)
    with pytest.raises(ValueError, match="not a folder"):
        call(project, "review_folder", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX)
    with pytest.raises(ValueError, match="context must be"):
        call(project, "review_file", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX, context="cloud")
    with pytest.raises(ValueError, match="unknown backend"):
        call(project, "review_file", path=ex(NEG + "api001_wait.server.luau"), project_id=SBX, backends=["eslint"])


def test_review_with_stub_backends_folds_everything_into_one_report(project, stubs, monkeypatch):
    f = ex(NEG + "api001_wait.server.luau")
    stubs("luau-analyze", stderr=f"{f}(2,1): TypeError: boom\n", exit_code=1)
    stubs("selene", stdout=f"{f}:3:1: warning[unused_variable]: x\n", exit_code=1)
    stubs("stylua", stdout=f"Diff in {f} at line 3:\n-a\n+b\n", exit_code=1)
    monkeypatch.setenv("PATH", f"{stubs.dir}:/bin:/usr/bin")
    out = call(project, "review_file", path=f, project_id=SBX)
    assert {x["source"] for x in out["findings"]} == {"own", "luau-analyze", "selene", "stylua"}
    assert out["backends"] == {"luau-analyze": "ran", "selene": "ran", "stylua": "ran"}
    assert out["findings"][0]["rule_id"] == "luau-analyze:TypeError"  # errors rank first


def test_rare_group_can_be_left_out(project, monkeypatch):
    assert {t.name for t in server_mod.selected_tools(project)} >= set(RARE_TOOLS)
    monkeypatch.setenv("LUAUREV_DISABLE_RARE", "1")
    names = {t.name for t in server_mod.selected_tools(project)}
    assert not (names & set(RARE_TOOLS)) and {"review_file", "review_folder", "explain_finding", "suggest_patch", "get_style_brief", "record_run"} <= names
    assert set(RARE_TOOLS) <= {t.name for t in tools(project)}


# ---------------------------------------------------------------- MCP
async def _with(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_session(project):
    server = build_server(project, tools(project), hooks_mod.HOOKS.instructions)

    async def go(client):
        listed = {t.name: t for t in (await client.list_tools()).tools}
        rev = await client.call_tool("review_file", {"path": ex(NEG + "dat001_unprotected_getasync.server.luau"), "project_id": SBX, "backends": []})
        refused = await client.call_tool("review_file", {"path": ex(NEG + "dat001_unprotected_getasync.server.luau"), "backends": []})
        unknown = await client.call_tool("review_file", {"path": ex(NEG + "dat001_unprotected_getasync.server.luau"), "project_id": "ghost"})
        rid = (await client.call_tool("record_run", {"request": "review the shop scripts"})).structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "reason": "too noisy", "corrections": [{"dimension": "noisy_rule", "note": "PERF005"}]})
        bad_dim = await client.call_tool("record_decision", {"run_id": rid, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}]})
        sp = await client.call_tool("suggest_patch", {"path": ex(NEG + "api001_wait.server.luau"), "rule_id": "API001"})
        return listed, rev, refused, unknown, dec, bad_dim, sp

    listed, rev, refused, unknown, dec, bad_dim, sp = asyncio.run(_with(server, go))
    assert listed["review_file"].annotations.readOnlyHint is True and listed["suggest_patch"].annotations.readOnlyHint is False
    assert listed["suppress_finding"].annotations.readOnlyHint is False and listed["list_rules"].annotations.readOnlyHint is True
    assert rev.structuredContent["findings"][0]["rule_id"] == "DAT001" and not rev.isError
    assert refused.isError and "project_id is required" in refused.content[0].text and unknown.isError and "unknown project_id" in unknown.content[0].text
    assert not dec.isError and bad_dim.isError and sp.structuredContent["supported"]


def test_real_stdio_server_starts_and_lists_tools(project):
    code = ("import asyncio,sys\nfrom mcp import ClientSession, StdioServerParameters\nfrom mcp.client.stdio import stdio_client\n"
            "async def main():\n p=StdioServerParameters(command=sys.executable,args=['-m','luaurev.server'],env={'LUAUREV_ROOT':%r,'PYTHONPATH':%r,'LUAUREV_DISABLE_RARE':'1','PATH':'/usr/bin:/bin'},cwd='/')\n"
            " async with stdio_client(p) as (r,w):\n  async with ClientSession(r,w) as s:\n   await s.initialize()\n   print(sorted(t.name for t in (await s.list_tools()).tools))\nasyncio.run(main())\n") % (str(ROOT), str(ROOT))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "review_file" in out.stdout and "compare_reviews" not in out.stdout


# ---------------------------------------------------------------- token discipline
def test_tool_descriptions_are_concise_and_documented(project):
    for t in tools(project):
        assert len(t.description) <= 420, (t.name, len(t.description))
    readme = (ROOT / "README.md").read_text()
    missing = [t.name for t in tools(project) if f"`{t.name}`" not in readme]
    assert not missing, f"README.md must document: {missing}"
    assert "`rare`" in readme and all(f"`{n}`" in readme for n in RARE_TOOLS)


def test_output_budgets_catch_growth(project, monkeypatch):
    import luaurev.tools as T

    tasks = [t for t in evals.load_tasks(ROOT / "evals" / "tasks") if t["id"].startswith("size-")]
    assert len(tasks) >= 11 and {t["input"]["tool"] for t in tasks} >= {t.name for t in T.make_tools(project)}
    ok = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="s", root=ROOT)
    assert ok["passed"] == ok["total"]
    monkeypatch.setattr(T, "compact", lambda rep, detail: rep)  # simulate the brief output growing back to the whole report
    grown = evals.run_suite(tasks, hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label="s", root=ROOT)
    assert {"size-review-file-bad-script", "size-review-folder-two-places"} <= {t["id"] for t in grown["tasks"] if not t["passed"]}


# ---------------------------------------------------------------- evals
def load_tasks():
    return evals.load_tasks(ROOT / "evals" / "tasks")


def run_all(project, label="t"):
    return evals.run_suite(load_tasks(), hooks_mod.eval_solver(project), hooks_mod.eval_checks(project), label=label, root=ROOT)


def test_there_are_enough_evals_of_every_kind():
    tasks = load_tasks()
    assert len(tasks) >= 100
    bad = [t for t in tasks if t["id"].startswith("neg-")]
    clean = [t for t in tasks if t["id"].startswith("pos-")]
    assert len(bad) >= 40 and len(clean) >= 15 and len([t for t in tasks if t.get("known_gap")]) >= 3
    assert all(t["checks"] for t in tasks)


def test_evals_pass_and_match_the_baseline(project):
    fresh = run_all(project)
    assert fresh["passed"] == fresh["total"], [t["id"] for t in fresh["tasks"] if not t["passed"]]
    baseline = json.loads((ROOT / "evals" / "reports" / "baseline.json").read_text())
    cmp = evals.compare_reports(baseline, fresh)
    assert not cmp["regressions"] and not cmp["new_tasks"] and not cmp["removed_tasks"], cmp
    pr = hooks_mod.pr_from_report(fresh, True, {t["id"] for t in load_tasks() if t.get("known_gap")})
    assert pr["precision"] == 1.0 and pr["recall"] == pytest.approx(pr["tp"] / (pr["tp"] + 3), abs=1e-3) and pr["known_gap_caught"] == 0


def test_every_rule_has_a_planted_bug_eval_and_a_detector(project):
    rules = get_rules(project)[0]
    covered = set()
    for t in load_tasks():
        if t["id"].startswith(("neg-", "gap-")) or t.get("expect_rules") or t.get("expect_questions"):
            covered |= set(t.get("expect_rules", [])) | set(t.get("expect_questions", []))
    assert set(rules) - covered == set()
    assert set(rules) - set(detectors.DETECTORS) == {"STR003"}  # the folder-scope rule is implemented in review.py
    for r in rules.values():
        assert r.example.strip() and r.explanation.strip() and r.fix.strip() and r.good.strip(), r.id


def test_every_example_file_is_used_by_an_eval_and_the_readme(project):
    refs = json.dumps(load_tasks())
    for d in ("negative", "positive"):
        readme = (ROOT / "examples" / d / "README.md").read_text()
        for f in sorted((ROOT / "examples" / d).iterdir()):
            if f.name == "README.md":
                continue
            assert f.name in refs or f"examples/{d}/{f.name}" in refs, f.name
            assert f.name in readme, f.name


def test_evals_fail_when_the_checker_is_broken(project, monkeypatch):
    """Vacuous-eval guard: a checker that finds nothing must fail the bug evals; one that flags everything must fail the clean evals."""
    real = dict(detectors.DETECTORS)
    monkeypatch.setattr(detectors, "DETECTORS", {k: (lambda a, p: []) for k in real})
    import luaurev.domain.review as R
    monkeypatch.setattr(R, "DETECTORS", detectors.DETECTORS)
    blind = run_all(project, "blind")
    failed = {t["id"] for t in blind["tasks"] if not t["passed"]}
    assert len([i for i in failed if i.startswith("neg-")]) >= 40 and "neg-sec004-loadstring-server" in failed
    assert not [i for i in failed if i.startswith("pos-")]
    noisy = {k: (lambda a, p: [detectors.Hit(1)]) for k in real}
    monkeypatch.setattr(detectors, "DETECTORS", noisy)
    monkeypatch.setattr(R, "DETECTORS", noisy)
    loud = run_all(project, "loud")
    failed = {t["id"] for t in loud["tasks"] if not t["passed"]}
    assert len([i for i in failed if i.startswith("pos-")]) >= 15


def test_precision_recall_check_counts_misses_and_false_alarms(project):
    chk = hooks_mod.eval_checks(project)["precision_recall"]
    task = {"expect_rules": ["A", "B"], "expect_questions": ["Q"]}
    ok, msg = chk({"defect_rules": ["A", "B", "C"], "question_rules": ["Q"]}, task)
    assert not ok and msg.startswith("tp=3 fp=1 fn=0")
    ok, msg = chk({"defect_rules": ["A"], "question_rules": []}, task)
    assert not ok and msg.startswith("tp=1 fp=0 fn=2")
    assert chk({"defect_rules": ["A"], "question_rules": []}, task, allow_miss=True)[0]
    assert not chk({"defect_rules": ["A", "C"], "question_rules": []}, task, allow_miss=True)[0]
    assert hooks_mod.pr_counts({"expect_rules": [], "expect_questions": ["Q"]}, {"defect_rules": ["Q"], "question_rules": []}) == (0, 1, 1)


def test_make_examples_output_is_current():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_examples.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout


def test_eval_pr_and_compare_cli(project, capsys, tmp_path):
    assert run(project, hooks_mod.HOOKS, ["eval-pr", "--report", "baseline", "--compare", "baseline"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["precision"] == 1.0 and 0.9 < out["recall"] < 1.0 and out["compare"]["precision_delta"] == 0 and out["compare"]["regressions"] == []


# ---------------------------------------------------------------- CLI
def test_cli_review_and_exit_codes(project, capsys):
    rc = run(project, hooks_mod.HOOKS, ["review", ex(NEG + "sec004_loadstring.server.luau"), "--project", SBX, "--backends", "none"])
    out = capsys.readouterr().out
    assert rc == 1 and "SEC004" in out and out.splitlines()[0].startswith("[example-sandbox] ERRORS")
    assert run(project, hooks_mod.HOOKS, ["review", ex(NEG + "api001_wait.server.luau"), "--project", SBX, "--backends", "none"]) == 0
    capsys.readouterr()
    assert run(project, hooks_mod.HOOKS, ["review", ex(NEG + "api001_wait.server.luau"), "--backends", "none"]) == 2
    assert "project_id is required" in capsys.readouterr().err
    assert run(project, hooks_mod.HOOKS, ["rules", "--category", "leaks"]) == 0 and "LEAK001" in capsys.readouterr().out
    assert run(project, hooks_mod.HOOKS, ["doctor"]) == 0
    assert "backends" in json.loads(capsys.readouterr().out)["domain"]


# ---------------------------------------------------------------- hygiene
def test_shared_machinery_is_imported_only_through_the_guide_adapter():
    offenders = []
    pat = re.compile(r"^\s*(from\s+\.+(?:luaurev\.)?core\b|import\s+luaurev\.core|from\s+luaurev\.core|from\s+\.+\s+import\s+core\b|from\s+guide_core\b|import\s+guide_core\b)", re.M)
    for f in (ROOT / "luaurev").rglob("*.py"):
        rel = f.relative_to(ROOT / "luaurev")
        if rel.parts[0] == "core" or rel.name == "guide_adapter.py":
            continue
        if pat.search(f.read_text()):
            offenders.append(str(rel))
    assert not offenders, f"import shared code via luaurev.guide_adapter only: {offenders}"
    from luaurev import guide_adapter as g

    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope"):
        assert hasattr(g, name), name
    assert g.mock is None and g.luau_safety is None  # this repo needs neither; documented in the adapter and README


def test_library_style_skills_schema(project):
    from luaurev import KINDS
    from guide_core.manifest import LibraryStore, base_schema
    from luaurev.domain.schema import DOMAIN_SCHEMA

    assert LibraryStore(project).validate_all(check_files=True)["ok"]
    assert validate_style(load_style(project)) == []
    assert agentfiles.validate_all_skills(project.root) == [] and agentfiles.sync(project.root, check=True) == []
    assert json.loads(project.schema_file.read_text()) == base_schema(KINDS, DOMAIN_SCHEMA)
    assert (ROOT / "skills" / "luau-reviewer-workflow" / "references").is_dir()


def test_layout_matches_shared_rule_7():
    for p in ("luaurev", "rules", "style", "library", "examples", "evals", "feedback", "skills", "adapters/mcp-clients", "tests", "references"):
        assert (ROOT / p).is_dir(), p
    for p in ("README.md", "CLAUDE.md", "AGENTS.md", "projects.yaml", "ASSET_LICENSING.md", "LICENSE"):
        assert (ROOT / p).is_file(), p
    assert (ROOT / "examples/positive/README.md").exists() and (ROOT / "examples/negative/README.md").exists()


def test_readme_is_honest_about_verification():
    text = (ROOT / "README.md").read_text().lower()
    for phrase in ("guide-core was not available", "never been run against", "not a parser", "stub", "projects.yaml", "what you still need to supply"):
        assert phrase in text, phrase


def test_rules_files_are_valid_and_settings_exist(project):
    rules, settings = get_rules(project)
    assert len(rules) == 41 and settings["learning"]["similarity_threshold"] == 0.6 and settings["verify_against_current_docs"] is True
    assert all(r.severity in ("error", "warning", "info") and r.confidence in ("low", "medium", "high") for r in rules.values())


def test_gitignore_keeps_private_data_out(project):
    assert "workspace/" in (ROOT / ".gitignore").read_text()
