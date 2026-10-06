import json

import pytest

from rbxlevel.core import retrieval
from rbxlevel.core.embeddings import HashingEmbedder, build_index
from rbxlevel.core.manifest import LibraryStore, load_schema, validate_asset


def test_example_library_is_valid(project):
    res = LibraryStore(project).validate_all()
    assert res["ok"], res["problems"]


def test_schema_rejects_bad_id_and_unknown_fields(project):
    schema = load_schema(project)
    bad = {"id": "Bad ID", "kind": "material", "title": "x", "path": "p", "license": {"owner": "user"},
           "status": "candidate", "surprise": 1}
    errors = validate_asset(bad, schema)
    assert any("id" in e for e in errors) or any("surprise" in e or "Additional" in e for e in errors)


def test_curated_needs_tags_license_and_description(project):
    schema = load_schema(project)
    base = {"id": "abc-123", "kind": "material", "title": "t", "path": "p", "status": "curated",
            "license": {"owner": "unknown"}}
    errs = validate_asset(base, schema)
    assert any("owner" in e for e in errs) and any("description" in e for e in errs)


def test_negative_example_must_explain_itself(project):
    schema = load_schema(project)
    neg = {"id": "neg-123", "kind": "material", "title": "t", "path": "p", "status": "curated",
           "license": {"owner": "user"}, "polarity": "negative"}
    assert any("negative" in e for e in validate_asset(neg, schema))


def test_upsert_writes_local_library_never_examples(project):
    store = LibraryStore(project)
    before = project.examples_library_file.read_text()
    asset = {"id": "my-new-one", "kind": "material", "title": "New", "path": "x.sbs",
             "license": {"owner": "user"}, "status": "candidate"}
    store.upsert(asset)
    assert project.examples_library_file.read_text() == before
    assert store.get("my-new-one")["_origin"] == "local"


def test_upsert_rejects_invalid(project):
    with pytest.raises(ValueError):
        LibraryStore(project).upsert({"id": "x", "kind": "nope"})


def test_ingest_is_dry_run_by_default_and_creates_candidates(project, tmp_path):
    src = tmp_path / "mats"
    src.mkdir()
    (src / "brick_wall.sbs").write_text("x")
    (src / "brick_wall.png").write_bytes(b"png")
    store = LibraryStore(project)
    recs = store.ingest_dir(src, kind="material", extensions=[".sbs"], tags=["brick"])
    assert len(recs) == 1 and recs[0]["status"] == "candidate" and recs[0]["preview"].endswith("brick_wall.png")
    assert not project.library_file.exists()  # nothing written
    store.ingest_dir(src, kind="material", extensions=[".sbs"], dry_run=False)
    assert project.library_file.exists()


def test_candidates_hidden_from_default_search(project):
    store = LibraryStore(project)
    hits = retrieval.search(store, "stone", k=10)
    ids = [h["id"] for h in hits["positive"]]
    assert "unreviewed-d" not in ids
    hits = retrieval.search(store, "stone", k=10, include_candidates=True)
    assert "unreviewed-d" in [h["id"] for h in hits["positive"]]


def test_text_search_ranks_relevant_first_with_reasons(project):
    hits = retrieval.search(LibraryStore(project), "chunky stone wall rounded bevels")
    assert hits["positive"][0]["id"] == "stone-wall-a"
    assert hits["positive"][0]["reasons"]


def test_negative_examples_are_returned_separately(project):
    hits = retrieval.search(LibraryStore(project), "stone", tags=["stone"])
    assert [h["id"] for h in hits["negative"]] == ["noisy-realistic-c"]
    assert "noisy-realistic-c" not in [h["id"] for h in hits["positive"]]
    assert hits["negative"][0]["correction_notes"]


def test_filters(project):
    store = LibraryStore(project)
    hits = retrieval.search(store, "", filters={"rating_min": 5})
    assert [h["id"] for h in hits["positive"]] == ["stone-wall-a"]
    hits = retrieval.search(store, "", filters={"tags_all": ["wood", "planks"]})
    assert [h["id"] for h in hits["positive"]] == ["wood-planks-b"]


def test_palette_similarity_prefers_matching_palette(project):
    store = LibraryStore(project)
    warm = retrieval.search(store, "", palette=["#8b5a2b", "#c68642"])
    cool = retrieval.search(store, "", palette=["#5c6670", "#8a96a3"])
    assert warm["positive"][0]["id"] == "wood-planks-b"
    assert cool["positive"][0]["id"] == "stone-wall-a"


def test_local_embeddings_index_and_search(project):
    store = LibraryStore(project)
    emb = HashingEmbedder(256)
    info = build_index(project, emb, store)
    assert info["assets"] == 4 and project.embeddings_file.exists()
    hits = retrieval.search(store, "plank wood", embedder=emb)
    assert hits["positive"][0]["id"] == "wood-planks-b"
    assert any("embedding" in r for r in hits["positive"][0]["reasons"])


def test_embeddings_of_other_provider_are_ignored(project):
    store = LibraryStore(project)
    build_index(project, HashingEmbedder(128), store)
    hits = retrieval.search(store, "wood", embedder=HashingEmbedder(256))  # different provider name
    assert all(not any("embedding" in r for r in h["reasons"]) for h in hits["positive"])


def test_asset_root_resolution(project, tmp_path, monkeypatch):
    root = tmp_path / "assets"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "f.sbs").write_text("x")
    monkeypatch.setenv("TOYTEST_ASSET_ROOT", str(root))
    store = LibraryStore(project)
    assert store.resolve_path("sub/f.sbs") == root / "sub" / "f.sbs"
    assert store.resolve_path("https://example.com/x.png") is None


def test_corrupt_jsonl_reports_line(project):
    project.library_file.parent.mkdir(parents=True)
    project.library_file.write_text('{"id": 1}\n{not json}\n')
    with pytest.raises(ValueError, match=":2:"):
        LibraryStore(project).load()
