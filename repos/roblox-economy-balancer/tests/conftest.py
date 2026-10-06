import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from econbal import project as make_project

    monkeypatch.setenv("ECONBAL_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "SPEC", "PROJECTS", "DISABLE_GROUPS"):
        monkeypatch.delenv(f"ECONBAL_{var}", raising=False)
    return make_project()


@pytest.fixture(scope="session")
def toys():
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_examples_mod", ROOT / "scripts" / "make_examples.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
