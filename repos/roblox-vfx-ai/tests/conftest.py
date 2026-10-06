import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """The real repository, with every writable path redirected to a temp workspace."""
    from rbxvfx import project as make_project

    monkeypatch.setenv("RBXVFX_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER"):
        monkeypatch.delenv(f"RBXVFX_{var}", raising=False)
    return make_project()


@pytest.fixture(scope="session")
def snapshot():
    from rbxvfx.domain import api

    return api.load()
