import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

from core_support import make_project  # noqa: E402


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TOYTEST_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER"):
        monkeypatch.delenv(f"TOYTEST_{var}", raising=False)
    return make_project(tmp_path / "repo")
