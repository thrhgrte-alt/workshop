import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from setdress import project as make_project

    monkeypatch.setenv("SETDRESS_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "KIT"):
        monkeypatch.delenv(f"SETDRESS_{var}", raising=False)
    return make_project()


@pytest.fixture(scope="session")
def snapshot():
    from setdress.domain import api

    return api.load()
