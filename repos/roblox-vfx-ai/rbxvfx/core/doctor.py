"""Runtime capability report: what works *on this machine right now*."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from pathlib import Path
from typing import Callable

from . import agentfiles
from .manifest import LibraryStore
from .project import Project
from .style import load_style


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def capability_report(project: Project, domain_checks: Callable[[Project], dict] | None = None) -> dict:
    report: dict = {
        "project": project.name,
        "python": platform.python_version(),
        "platform": platform.system(),
        "optional_dependencies": {
            "mcp (MCP server)": _has("mcp"),
            "numpy+Pillow (image measurements)": _has("numpy") and _has("PIL"),
            "sentence-transformers (CLIP-style embeddings)": _has("sentence_transformers"),
        },
        "workspace": str(project.workspace),
    }
    if sys.version_info < (3, 10):
        report["python_warning"] = "Python 3.10+ required"
    try:
        store = LibraryStore(project)
        rows = store.load()
        validation = store.validate_all()
        report["library"] = {
            "assets": len(rows),
            "curated": sum(r["status"] == "curated" for r in rows),
            "candidates_awaiting_curation": sum(r["status"] == "candidate" for r in rows),
            "from_tracked_examples": sum(r["_origin"] == "examples" for r in rows),
            "from_local_library": sum(r["_origin"] == "local" for r in rows),
            "valid": validation["ok"],
            "problems": validation["problems"],
        }
    except Exception as exc:
        report["library"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        style = load_style(project)
        report["style"] = {"name": style["name"], "valid": True}
    except Exception as exc:
        report["style"] = {"valid": False, "error": str(exc)}
    skill_problems = agentfiles.validate_all_skills(project.root)
    report["skills"] = {"valid": not skill_problems, "problems": skill_problems}
    drift = agentfiles.sync(project.root, check=True)
    report["agent_files"] = {"in_sync": not drift, "differences": drift}
    report["git_lfs_installed"] = shutil.which("git-lfs") is not None
    if domain_checks:
        report["domain"] = domain_checks(project)
    return report
