import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """The real repository, but with all private/writable data redirected to a temp workspace."""
    from sdai import project as make_project

    monkeypatch.setenv("SDAI_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "PROBE_RESULT", "SBSCOOKER", "SBSRENDER",
                "DESIGNER_PACKAGES"):
        monkeypatch.delenv(f"SDAI_{var}", raising=False)
    return make_project()
