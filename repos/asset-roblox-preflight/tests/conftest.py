import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from preflight import project as make_project

    monkeypatch.setenv("PREFLIGHT_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "DISABLE_RARE"):
        monkeypatch.delenv(f"PREFLIGHT_{var}", raising=False)
    return make_project()


@pytest.fixture()
def call(project):
    from preflight import hooks
    from preflight.guide_adapter import all_tools, call_local

    def _call(tool, **kw):
        return call_local(all_tools(project, hooks.HOOKS), tool, kw)

    return _call


@pytest.fixture(scope="session")
def assets():
    d = ROOT / "examples" / "assets"
    return lambda name: str(next(p for p in d.glob(name + ".*") if p.suffix != ".mtl"))
