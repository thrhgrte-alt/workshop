import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):  # tests dir (core_support) and the kit dir (core)
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core_support import make_project  # noqa: E402


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TOYTEST_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER"):
        monkeypatch.delenv(f"TOYTEST_{var}", raising=False)
    return make_project(tmp_path / "repo")
