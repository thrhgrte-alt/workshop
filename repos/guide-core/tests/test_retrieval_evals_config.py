import json
from pathlib import Path

import pytest
import yaml

from guide_core import config, evals, retrieval
from guide_core.config import ConfigError, LockedValueError


# --- retrieval.search_items ---------------------------------------------------------------------------------------
ITEMS = [{"id": "a", "title": "round the bevels on stone", "tags": ["stone"]},
         {"id": "b", "title": "less glossy metal", "tags": ["metal"]},
         {"id": "c", "title": "stone wall palette", "tags": ["stone", "palette"]}]


def test_search_items_text_and_tags():
    assert [h["id"] for h in retrieval.search_items(ITEMS, "stone bevels")][0] == "a"
    assert [h["id"] for h in retrieval.search_items(ITEMS, tags=["palette"])] == ["c"]
    assert retrieval.search_items(ITEMS, "zebra") == []
    top = retrieval.search_items(ITEMS, "stone", tags=["palette"])
    assert top[0]["id"] == "c" and any(r.startswith("tags") for r in top[0]["reasons"])


def test_search_items_embedding_is_optional_and_blended():
    from guide_core.embeddings import HashingEmbedder

    emb = HashingEmbedder()
    vecs = {it["id"]: emb.embed_texts([it["title"]])[0] for it in ITEMS}
    hits = retrieval.search_items(ITEMS, "glossy metal", embedder=emb, vectors=vecs)
    assert hits[0]["id"] == "b" and any("embedding" in r for r in hits[0]["reasons"])


def test_search_items_respects_k():
    assert len(retrieval.search_items(ITEMS, "stone", k=1)) == 1


# --- evals --------------------------------------------------------------------------------------------------------
def _write(d, name, text):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")


def test_validate_task():
    assert evals.validate_task({"id": "t", "checks": [{"type": "equals"}]}) == []
    assert any("non-empty list" in p for p in evals.validate_task({"id": "t", "checks": []}))  # a task with no checks would pass vacuously
    assert any("'type'" in p for p in evals.validate_task({"id": "t", "checks": [{"path": "a"}]}))
    assert any("source" in p for p in evals.validate_task({"id": "t", "checks": [{"type": "x"}], "source": "bogus"}))
    assert any("tags" in p for p in evals.validate_task({"id": "t", "checks": [{"type": "x"}], "tags": "a"}))


def test_strict_load_rejects_vacuous_task(tmp_path):
    _write(tmp_path / "t", "a.yaml", "- {id: x, checks: []}\n")
    assert evals.load_tasks(tmp_path / "t")[0]["id"] == "x"  # lenient (vendored behaviour)
    with pytest.raises(ValueError, match="vacuously"):
        evals.load_tasks(tmp_path / "t", strict=True)


def test_load_split_keeps_sets_apart_and_tolerates_empty_real(tmp_path):
    _write(tmp_path / "own", "a.yaml", "- {id: own1, checks: [{type: equals, path: a, value: 1}]}\n")
    (tmp_path / "real").mkdir()
    split = evals.load_split(tmp_path / "own", tmp_path / "real")
    assert [t["id"] for t in split["self_written"]] == ["own1"] and split["real"] == [] and split["self_written"][0]["source"] == "self_written"
    assert evals.load_split(tmp_path / "own", tmp_path / "missing")["real"] == []
    _write(tmp_path / "real", "r.yaml", "- {id: own1, checks: [{type: equals, path: a, value: 1}]}\n")
    with pytest.raises(ValueError, match="both"):
        evals.load_split(tmp_path / "own", tmp_path / "real")


def test_run_split_reports_separately_and_empty_real_is_not_a_pass(tmp_path):
    _write(tmp_path / "own", "a.yaml", "- {id: own1, checks: [{type: equals, path: a, value: 1}]}\n- {id: own2, checks: [{type: equals, path: a, value: 2}]}\n")
    split = evals.load_split(tmp_path / "own", tmp_path / "real")
    rep = evals.run_split(split, lambda t: {"a": 1}, {}, label="L", root=tmp_path)
    assert (rep["self_written"]["passed"], rep["self_written"]["total"]) == (1, 2)
    assert rep["real"]["total"] == 0 and rep["real_count"] == 0
    md = evals.render_markdown(rep)
    assert "**Self-written**" in md and "1/2 passed" in md and "0 cases - no real-world result exists yet" in md and "`own2`" in md


def test_compare_detailed_lists_failing_messages(tmp_path):
    _write(tmp_path / "t", "a.yaml", "- {id: t1, checks: [{type: equals, path: a, value: 1}]}\n")
    tasks = evals.load_tasks(tmp_path / "t")
    good = evals.run_suite(tasks, lambda t: {"a": 1}, {}, label="good", root=tmp_path)
    bad = evals.run_suite(tasks, lambda t: {"a": 2}, {}, label="bad", root=tmp_path)
    cmp = evals.compare_detailed(good, bad)
    assert cmp["regressed"] and cmp["regressions"] == ["t1"] and "got 2" in cmp["regression_details"]["t1"][0]
    assert evals.compare_detailed(bad, good)["regressed"] is False and evals.compare_detailed(bad, good)["improvements"] == ["t1"]
    md = evals.render_markdown(bad, cmp)
    assert "regressions: t1" in md


def test_save_report_json_and_markdown(tmp_path):
    _write(tmp_path / "t", "a.yaml", "- {id: t1, checks: [{type: equals, path: a, value: 1}]}\n")
    rep = evals.run_suite(evals.load_tasks(tmp_path / "t"), lambda t: {"a": 1}, {}, label="run1", root=tmp_path)
    j, m = evals.save_report(rep, tmp_path / "out"), evals.save_markdown(rep, tmp_path / "out")
    assert json.loads(j.read_text())["passed"] == 1 and "1/1 passed" in m.read_text()


# --- config ---------------------------------------------------------------------------------------------------------
SCHEMA = {"type": "object", "required": ["limits"], "properties": {"limits": {"type": "object", "properties": {"max_tris": {"type": "integer", "minimum": 1}}}}}


def test_load_yaml_errors(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        config.load_yaml(tmp_path / "no.yaml")
    assert config.load_yaml(tmp_path / "no.yaml", must_exist=False) == {}
    (tmp_path / "bad.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        config.load_yaml(tmp_path / "bad.yaml")
    (tmp_path / "list.yaml").write_text("- 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        config.load_yaml(tmp_path / "list.yaml")


def test_schema_validation_reports_all_problems_with_paths(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump({"limits": {"max_tris": 0}, "extra": 1}), encoding="utf-8")
    with pytest.raises(ConfigError, match="limits.max_tris"):
        config.load_config(f, SCHEMA)
    f.write_text("other: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="limits"):
        config.load_config(f, SCHEMA)
    assert config.validate({"limits": {"max_tris": 5}}, SCHEMA) == []


def test_locks_by_list_and_by_marker(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("locked: [limits.max_tris]\nlimits: {max_tris: 100, max_bones: 50}\nrules:\n  SEC001: {confidence: high, locked: true}\n"
                 "  SEC002: {confidence: low, verify_against_current_docs: true}\n", encoding="utf-8")
    c = config.load_config(f)
    assert c.is_locked("limits.max_tris") and c.is_locked("rules.SEC001.confidence") and not c.is_locked("limits.max_bones") and not c.is_locked("rules.SEC002")
    assert c.unverified() == ["rules.SEC002"] and c.get("limits.max_bones") == 50 and c.get("nope", 7) == 7


def test_layer_resolution_reports_sources_and_blocks_locked(tmp_path):
    f = tmp_path / "base.yaml"
    f.write_text("locked: [limits.max_tris]\nlimits: {max_tris: 100, max_bones: 50}\n", encoding="utf-8")
    base = config.load_config(f)
    res = config.layer(base, ("project", {"limits": {"max_bones": 60}}), ("place", {"limits": {"max_bones": 70, "new": 1}}))
    assert res.get("limits.max_bones") == 70 and res.source_of("limits.max_bones") == "place" and res.source_of("limits.max_tris") == str(f)
    assert res.get("limits.new") == 1
    with pytest.raises(LockedValueError, match="locked"):
        config.layer(base, ("project", {"limits": {"max_tris": 200}}))
    soft = config.layer(base, ("project", {"limits": {"max_tris": 200}}), strict=False)
    assert soft.get("limits.max_tris") == 100 and soft.rejected[0]["attempted"] == 200 and soft.rejected[0]["layer"] == "project"
    same = config.layer(base, ("project", {"limits": {"max_tris": 100}}))  # restating the locked value is harmless
    assert same.rejected == []


def test_layer_cannot_remove_a_lock_by_editing_markers():
    base = {"locked": ["a.b"], "a": {"b": 1}}
    res = config.layer(base, ("p", {"locked": [], "a": {"b": 1}}))
    assert "a.b" in res.locks


def test_style_and_rubric_loaders_are_reexported():
    assert config.load_style is not None and config.score_rubric is not None and config.check_ranges is not None


def test_toy_rubric_scores_partially_until_real_examples_and_a_person_exist():
    rubric = evals.load_rubric(Path(__file__).resolve().parents[1] / "evals" / "rubric.yaml")
    assert evals.validate_rubric(rubric) == [] and rubric["pass_at"] == 0.7
    auto = {"toy_evals_pass": True, "mutants_are_caught": True, "no_regression_vs_baseline": True}  # real_examples_pass has no data: evals/real/ is empty
    res = evals.score_rubric(rubric, auto=auto)
    assert res["complete"] is False and res["passed"] is False  # unscored criteria: not a verdict
    assert res["unscored"] == ["real_examples_pass", "api_is_small_and_documented", "learning_gates_feel_right"]
    assert res["score"] == 1.0  # of what WAS scored
    failing = evals.score_rubric(rubric, auto={**auto, "toy_evals_pass": False})
    assert failing["required_failures"] == ["toy_evals_pass"] and failing["passed"] is False
