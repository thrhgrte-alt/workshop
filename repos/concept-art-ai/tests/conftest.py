import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from conceptai import project as make_project

    monkeypatch.setenv("CONCEPTAI_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "IMAGE_COMMAND", "IMAGE_MODEL"):
        monkeypatch.delenv(f"CONCEPTAI_{var}", raising=False)
    return make_project()
