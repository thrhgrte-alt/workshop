import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

NOW = "2026-10-06T12:00:00+00:00"
FRESH = "2026-10-06T11:00:00+00:00"


def _clean_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PLACEMAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("PLACEMAP_NOW", NOW)
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "PROJECTS", "PLACES", "DISABLE_GROUPS", "DISABLE_RARE", "INGEST_PATHS"):
        monkeypatch.delenv(f"PLACEMAP_{var}", raising=False)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from placemap import project as make_project

    _clean_env(monkeypatch, tmp_path)
    return make_project()


@pytest.fixture()
def tmp_project(tmp_path, monkeypatch):
    """A writable copy of the repository's data (rules, projects, style, examples, samples) so tests can write registries, synonyms and landmark files without touching the checkout."""
    from placemap import project as make_project

    root = tmp_path / "repo"
    root.mkdir()
    for name in ("rules", "projects", "style", "library", "examples", "samples", "evals"):
        shutil.copytree(ROOT / name, root / name)
    shutil.copy(ROOT / "projects.yaml", root / "projects.yaml")
    _clean_env(monkeypatch, tmp_path)
    return make_project(root)


@pytest.fixture()
def call(project):
    from placemap.guide_adapter import all_tools, mcpkit
    from placemap.hooks import HOOKS

    def _call(tool, **kw):
        return mcpkit.call_local(all_tools(project, HOOKS), tool, kw)

    return _call


@pytest.fixture()
def world(project, call):
    """Both mining places, the role lab and the tycoon ingested (fresh: one hour old)."""
    for pid, plid, f in (("demo_mine", "dive-and-mine", "mine-main"), ("demo_mine", "dive-and-mine-hardcore", "mine-hardcore"), ("demo_roles", "role-lab", "role-lab"),
                         ("demo_tycoon", "tycoon-main", "tycoon-main")):
        call("ingest_snapshot", project_id=pid, place_id=plid, file=f"examples/places/{f}.collect.json", dry_run=False, taken_at=FRESH)
    return project


def caller(project):
    from placemap.guide_adapter import all_tools, mcpkit
    from placemap.hooks import HOOKS

    def _call(tool, **kw):
        return mcpkit.call_local(all_tools(project, HOOKS), tool, kw)

    return _call


@pytest.fixture()
def tcall(tmp_project):
    """Tools bound to the writable copy of the repository data."""
    return caller(tmp_project)


@pytest.fixture(autouse=True)
def _fresh_caches():
    """The scorer, index and snapshot caches are keyed by snapshot id and parameter values; clear them so a test that patches code is never served an object built before the patch."""
    from placemap.domain import index, snapshot, view

    view._SCORER_CACHE.clear()
    index._INDEX_CACHE.clear()
    snapshot._load_cached.cache_clear()
    yield
