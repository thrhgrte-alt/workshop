import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def project(tmp_path, monkeypatch):
    from luaurev import project as make_project

    monkeypatch.setenv("LUAUREV_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "RULESET", "BACKEND_TIMEOUT", "DISABLE_RARE"):
        monkeypatch.delenv(f"LUAUREV_{var}", raising=False)
    return make_project()


@pytest.fixture()
def tmp_project(tmp_path, monkeypatch):
    """A writable copy of the repository's data (rules, projects, style, examples) so tests can write rulesets without touching the real checkout."""
    from luaurev import project as make_project

    root = tmp_path / "repo"
    root.mkdir()
    for name in ("rules", "projects", "style", "library", "examples", "evals"):
        shutil.copytree(ROOT / name, root / name)
    shutil.copy(ROOT / "projects.yaml", root / "projects.yaml")
    monkeypatch.setenv("LUAUREV_WORKSPACE", str(tmp_path / "ws"))
    for var in ("LIBRARY", "ASSET_ROOT", "ALLOWED_PATHS", "EMBEDDER", "RULESET", "BACKEND_TIMEOUT", "DISABLE_RARE"):
        monkeypatch.delenv(f"LUAUREV_{var}", raising=False)
    return make_project(root)


@pytest.fixture()
def stubs(tmp_path):
    """Make stub executables on a PATH that contains only them (plus /bin and /usr/bin for sh and cat)."""
    from luaurev.hooks import make_stub

    d = tmp_path / "stubbin"
    d.mkdir()

    def make(name, stdout="", stderr="", exit_code=0):
        return make_stub(d, name, stdout, stderr, exit_code)

    make.dir = d
    return make
