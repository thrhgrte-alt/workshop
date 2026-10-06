"""Thin layer over the official MCP Python SDK (FastMCP).

Design rules (see README "Tool design"):

* read-only tools are annotated ``readOnlyHint=true``,
* anything that writes defaults to ``dry_run=true`` and returns a plan,
* tools return compact structured dicts, and raise ``ValueError`` with an
  actionable message on bad input (the SDK turns that into an MCP tool error).

Tool annotations are *hints* for clients; they are not a security boundary.
Real protection comes from path allowlists (``scope.py``) and local-only
transports.

Labels and groups. ``read_only`` is the read-only/write label every client sees (``label``). ``group`` (for example ``"rare"``)
marks tools most sessions never need so a client can leave them out: set ``<PREFIX>_DISABLE_GROUPS=rare`` (comma list) and
:func:`filter_groups` drops them. A description that starts with ``[rare]`` also counts as group ``rare`` (the convention two
repositories already used). ``<PREFIX>_DISABLE_RARE=1`` is accepted as the older spelling of ``DISABLE_GROUPS=rare``.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .project import Project


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., dict]
    description: str
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    expensive: bool = False
    group: str | None = None  # e.g. "rare": rarely used, can be disabled with <PREFIX>_DISABLE_GROUPS

    @property
    def label(self) -> str:
        return "read-only" if self.read_only else "write"

    @property
    def effective_group(self) -> str | None:
        if self.group:
            return self.group
        m = re.match(r"^\[([a-z][a-z0-9_-]*)\]", self.description or "")
        return m.group(1) if m else None

    def annotations(self):
        from mcp.types import ToolAnnotations

        return ToolAnnotations(
            title=self.name.replace("_", " "),
            readOnlyHint=self.read_only,
            destructiveHint=None if self.read_only else self.destructive,
            idempotentHint=self.idempotent if not self.read_only else None,
            openWorldHint=False,
        )


def disabled_groups(project: Project) -> set[str]:
    """Groups switched off through ``<PREFIX>_DISABLE_GROUPS`` (comma list) or the older ``<PREFIX>_DISABLE_RARE=1``."""
    out = {g.strip() for g in (project.env("DISABLE_GROUPS") or "").split(",") if g.strip()}
    if (project.env("DISABLE_RARE") or "").lower() in ("1", "true", "yes"):
        out.add("rare")
    return out


def filter_groups(project: Project, specs: Iterable[ToolSpec], groups: Iterable[str] | None = None) -> list[ToolSpec]:
    """Drop the tools whose group is disabled (``groups`` defaults to :func:`disabled_groups`)."""
    off = set(groups) if groups is not None else disabled_groups(project)
    return [s for s in specs if s.effective_group not in off]


def tool_catalog(specs: Iterable[ToolSpec]) -> list[dict]:
    """One line per tool (name, read-only/write label, group, first description line): what a skill or README lists."""
    return [{"name": s.name, "label": s.label, "group": s.effective_group, "line": (s.description or "").strip().split("\n")[0][:160]} for s in specs]


def build_server(project: Project, specs: list[ToolSpec], instructions: str, *, host: str = "127.0.0.1", port: int = 8765,
                 apply_groups: bool = False):
    """A FastMCP server with one tool per spec. ``apply_groups=True`` first drops the groups disabled in the environment."""
    from mcp.server.fastmcp import FastMCP

    if apply_groups:
        specs = filter_groups(project, specs)
    names = [s.name for s in specs]
    if len(names) != len(set(names)):
        raise ValueError("duplicate tool names")
    for s in specs:  # a bare `dict` return type cannot be turned into a structured-output schema
        if inspect.signature(s.fn).return_annotation in (dict, "dict", inspect.Signature.empty):
            raise ValueError(f"tool '{s.name}' must be annotated '-> dict[str, Any]' (bare 'dict' or no "
                             f"annotation cannot produce structured output)")
    server = FastMCP(project.name, instructions=instructions, host=host, port=port)
    for s in specs:
        desc = s.description + (" [expensive: runs a slow external process]" if s.expensive else "")
        server.add_tool(s.fn, name=s.name, description=desc, annotations=s.annotations(), structured_output=True)
    return server


def call_local(specs: list[ToolSpec], name: str, arguments: dict[str, Any] | None = None) -> dict:
    """Invoke a tool function directly (no MCP client). Used by the CLI and tests."""
    spec = next((s for s in specs if s.name == name), None)
    if spec is None:
        raise ValueError(f"unknown tool '{name}'. Available: {[s.name for s in specs]}")
    arguments = arguments or {}
    sig = inspect.signature(spec.fn)
    unknown = set(arguments) - set(sig.parameters)
    if unknown:
        raise ValueError(f"unknown argument(s) {sorted(unknown)} for '{name}'. Expected: {list(sig.parameters)}")
    return spec.fn(**arguments)


def serve(project: Project, specs: list[ToolSpec], instructions: str, transport: str = "stdio",
          host: str = "127.0.0.1", port: int = 8765, apply_groups: bool = False) -> None:
    if transport not in ("stdio", "streamable-http"):
        raise ValueError("transport must be 'stdio' or 'streamable-http'")
    if transport == "streamable-http" and host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("refusing to bind a network interface: this server has no authentication. "
                         "Use 127.0.0.1, or put an authenticating proxy in front of it deliberately.")
    build_server(project, specs, instructions, host=host, port=port, apply_groups=apply_groups).run(transport=transport)
