"""Render previews of a saved ``.sbs`` with Adobe's command-line tools (``sbscooker`` + ``sbsrender``).

The two tools ship with Substance 3D Designer / the Substance 3D Automation Toolkit. This module only
*builds and runs* their command lines - it does not reimplement rendering. Flag names below follow the
documented CLI shape but were NOT executed during development (no Designer here), so:

* ``render_preview(dry_run=True)`` (the default) returns the exact commands for you to inspect,
* real execution happens only with ``dry_run=False`` and both tools present,
* if a flag differs on your version, the failure output is returned verbatim; check ``sbsrender --help``.

Set ``SDAI_SBSCOOKER`` / ``SDAI_SBSRENDER`` to full paths if the tools are not on PATH, and
``SDAI_DESIGNER_PACKAGES`` to Designer's ``resources/packages`` folder (needed so library nodes resolve).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from sdai.core.project import Project
from sdai.core.safety import resolve_inside

USAGE_OUTPUT_SIZE_LOG2 = {256: 8, 512: 9, 1024: 10, 2048: 11, 4096: 12}


def find_tool(project: Project, name: str, env_key: str) -> str | None:
    explicit = project.env(env_key)
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which(name)


def build_commands(project: Project, sbs: Path, out_dir: Path, resolution: int = 1024) -> dict:
    if resolution not in USAGE_OUTPUT_SIZE_LOG2:
        raise ValueError(f"resolution must be one of {sorted(USAGE_OUTPUT_SIZE_LOG2)}")
    cooker = find_tool(project, "sbscooker", "SBSCOOKER") or "sbscooker"
    renderer = find_tool(project, "sbsrender", "SBSRENDER") or "sbsrender"
    packages = project.env("DESIGNER_PACKAGES")
    sbsar = out_dir / f"{sbs.stem}.sbsar"
    cook = [cooker, "--inputs", str(sbs), "--output-path", str(out_dir)]
    if packages:
        cook += ["--includes", packages]
    size = USAGE_OUTPUT_SIZE_LOG2[resolution]
    render = [renderer, "render", str(sbsar), "--output-path", str(out_dir), "--output-name", "{outputNodeName}",
              "--output-format", "png", "--set-value", f"$outputsize@{size},{size}"]
    return {"cook": cook, "render": render, "expected_sbsar": str(sbsar)}


def render_preview(project: Project, sbs_path: str, out_dir: str | None = None, resolution: int = 1024, *,
                   dry_run: bool = True, timeout: int = 300) -> dict:
    sbs = Path(sbs_path).expanduser()
    if sbs.suffix.lower() != ".sbs":
        raise ValueError("render_preview expects a .sbs file")
    if not sbs.exists():
        raise FileNotFoundError(f"{sbs} does not exist (save the graph from Designer first)")
    target = resolve_inside(out_dir or (project.output_dir / "previews" / sbs.stem), project.allowed_roots)
    cmds = build_commands(project, sbs, target, resolution)
    cooker_found = find_tool(project, "sbscooker", "SBSCOOKER")
    renderer_found = find_tool(project, "sbsrender", "SBSRENDER")
    info = {"dry_run": dry_run, "commands": {"cook": cmds["cook"], "render": cmds["render"]},
            "output_dir": str(target), "tools_found": {"sbscooker": bool(cooker_found), "sbsrender": bool(renderer_found)}}
    if dry_run:
        info["status"] = "planned"
        info["note"] = ("Nothing was run. Pass dry_run=false to execute. If the tools are missing, export the maps "
                        "from Designer (Output node > Export) and pass them to compare_material_to_rubric.")
        return info
    if not (cooker_found and renderer_found):
        info["status"] = "unavailable"
        info["note"] = "sbscooker/sbsrender not found; set SDAI_SBSCOOKER / SDAI_SBSRENDER or export maps manually."
        return info
    target.mkdir(parents=True, exist_ok=True)
    for step in ("cook", "render"):
        proc = subprocess.run(cmds[step], capture_output=True, text=True, timeout=timeout, cwd=target,
                              env={**os.environ})
        if proc.returncode != 0:
            info.update(status="failed", failed_step=step, returncode=proc.returncode,
                        stderr_tail=proc.stderr[-1500:], stdout_tail=proc.stdout[-500:])
            return info
    maps = sorted(str(p) for p in target.glob("*.png"))
    info.update(status="ok", maps=maps)
    return info
