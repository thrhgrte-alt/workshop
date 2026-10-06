import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from playqa import project as make_project

    monkeypatch.setenv("PLAYQA_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "PROJECTS", "DISABLE_GROUPS", "DISABLE_RARE", "TELEMETRY"):
        monkeypatch.delenv(f"PLAYQA_{var}", raising=False)
    return make_project()


MAIN = {"project_id": "demo_mine", "place_id": "main"}
STUDIO = [{"name": "Demo Mine Main (SYNTHETIC)", "place_id": 0, "studio_id": "s1"}]
