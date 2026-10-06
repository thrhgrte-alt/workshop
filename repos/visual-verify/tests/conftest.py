import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

ENV_VARS = ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "PROJECTS", "DISABLE_GROUPS", "DISABLE_RARE", "TELEMETRY", "WORKSPACE")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from visualverify import project as make_project

    monkeypatch.setenv("VISUALVERIFY_WORKSPACE", str(tmp_path / "ws"))
    for var in ENV_VARS[:-1]:
        monkeypatch.delenv(f"VISUALVERIFY_{var}", raising=False)
    return make_project()


@pytest.fixture()
def images(project):
    """Write generated images into the workspace (a folder every project may read) and return a name -> path function."""
    from visualverify import evalgen as G

    base = project.workspace / "imgs"

    def make(name: str, spec: dict) -> str:
        return str(G.write(spec, base / f"{name}.png"))

    return make
