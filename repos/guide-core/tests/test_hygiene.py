"""Repository hygiene: the documented API exists, versions agree, docs say the honest things, modules stay in their lane."""

import ast
import importlib
import re
import sys
from pathlib import Path

import pytest

import guide_core

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "guide_core"
MODULES = sorted(p.stem for p in PKG.glob("*.py") if p.stem != "__init__")


def test_every_name_in_docs_api_exists():
    text = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    checked = 0
    for m in re.finditer(r"### `(guide_core\.\w+)`\n(.*?)(?=\n### |\Z)", text, re.S):
        mod = importlib.import_module(m.group(1))
        for span in re.findall(r"`([^`]+)`", m.group(2)):
            if span.startswith((".", "guide_core", "pip ", "[", "(")) or " " in span.split("(")[0] or "=" in span.split("(")[0]:
                continue
            head = span.split("(")[0]
            obj = mod
            for part in head.split("."):
                assert hasattr(obj, part), f"docs/api.md lists `{span}` under {m.group(1)} but it does not exist"
                obj = getattr(obj, part)
            checked += 1
    assert checked > 100  # the parser actually read the document


def test_versions_agree():
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert f'version = "{guide_core.__version__}"' in pyproject
    assert re.search(rf"^## {re.escape(guide_core.__version__)} - ", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert isinstance(guide_core.SCHEMA_VERSION, int)
    assert guide_core.__doc__ and "learning layer" in guide_core.__doc__ or "learning" in guide_core.__doc__


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports_and_has_a_docstring(name):
    mod = importlib.import_module(f"guide_core.{name}")
    assert (mod.__doc__ or "").strip(), f"guide_core.{name} needs a module docstring"


def test_spec_module_names_exist():
    for name in ("dryrun", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "config", "scope",
                 "observe", "params", "propose", "gate", "promote", "skillgen", "telemetry"):
        importlib.import_module(f"guide_core.{name}")


def test_source_is_python_310_compatible():
    for f in PKG.glob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f), feature_version=(3, 10))
    assert (ROOT / "pyproject.toml").read_text().count('requires-python = ">=3.10"') == 1


NETWORK = {"socket", "urllib", "http", "requests", "httpx", "ssl", "smtplib", "ftplib", "aiohttp", "xmlrpc", "websockets"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def test_no_module_talks_to_the_network_or_studio():
    # mcpkit builds a local stdio/loopback server through the mcp SDK; everything else must not import network code at all.
    for f in PKG.glob("*.py"):
        assert not (_imports(f) & NETWORK), f"{f.name} imports network code"
    text = "\n".join(f.read_text(encoding="utf-8") for f in PKG.glob("*.py"))
    assert "subprocess" not in "\n".join(_imports(f).__repr__() for f in PKG.glob("*.py") if f.stem not in ("evals",))  # only evals runs `git rev-parse`
    assert "os.system" not in text and "eval(" not in re.sub(r"evaluate|\.eval\(|# .*", "", text).replace("__eval", "")


def test_learning_modules_never_apply_delete_or_auto_promote():
    forbidden = ("delete", "remove", "purge", "unlink", "rmtree", "auto_promote", "auto_apply", "autopromote")
    for name in ("observe", "propose", "gate", "promote", "params", "skillgen", "telemetry"):
        mod = importlib.import_module(f"guide_core.{name}")
        for attr in dir(mod):
            assert not any(w in attr.lower() for w in forbidden), f"{name}.{attr}"
        src = (PKG / f"{name}.py").read_text(encoding="utf-8")
        deleting = [ln for ln in src.splitlines() if re.search(r"unlink\(|rmtree|os\.remove|os\.rmdir", ln) and "tmp" not in ln]  # only a half-written temp file may be cleaned up
        assert not deleting, f"{name} deletes files: {deleting}"
    # the learning layer never writes into a repository's own files: only stores it was given
    for name in ("propose", "gate"):
        src = (PKG / f"{name}.py").read_text(encoding="utf-8")
        assert "write_text(" not in src


def test_every_write_function_that_changes_learned_state_requires_a_named_approver():
    from guide_core import params, promote

    for fn in (params.ParamStore.update, params.ParamStore.rollback, promote.KnowledgeStore.promote_global, promote.KnowledgeStore.contradict, promote.promote_proposal):
        assert "approved_by" in fn.__code__.co_varnames[: fn.__code__.co_argcount + fn.__code__.co_kwonlyargcount], fn.__qualname__


def test_readme_states_the_honest_limits():
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    for phrase in ("only as far as the corrections and evals", "weights never change", "never run against", "self-written", "evals/real", "placeholder",
                   "cannot learn taste", "mostly default parameters", "lupa"):
        assert phrase in text, f"README must say: {phrase}"


def test_agent_files_exist_and_agree():
    for name in ("README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md", "LICENSE", "docs/extraction-notes.md", "docs/migrating-older-repos.md", "docs/api.md",
                 "evals/real/README.md", "skills/build-a-repo-on-guide-core/SKILL.md"):
        assert (ROOT / name).is_file(), name
    from guide_core import agentfiles

    assert agentfiles.validate_all_skills(ROOT) == []
    assert agentfiles.sync(ROOT, check=True) == []  # CLAUDE.md etc. are in step with AGENTS.md


def test_evals_real_folder_is_empty_except_for_its_readme():
    assert sorted(p.name for p in (ROOT / "evals" / "real").iterdir() if p.is_file()) == [".gitkeep", "README.md"]
