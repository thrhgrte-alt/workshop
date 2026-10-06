"""Thin layer over the official MCP Python SDK (FastMCP).

Design rules (see README "Tool design"):

* read-only tools are annotated ``readOnlyHint=true``,
* anything that writes defaults to ``dry_run=true`` and returns a plan,
* tools return compact structured dicts, and raise ``ValueError`` with an
  actionable message on bad input (the SDK turns that into an MCP tool error).

Tool annotations are *hints* for clients; they are not a security boundary.
Real protection comes from path allowlists (``safety.py``) and local-only
transports.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

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

    def annotations(self):
        from mcp.types import ToolAnnotations

        return ToolAnnotations(
            title=self.name.replace("_", " "),
            readOnlyHint=self.read_only,
            destructiveHint=None if self.read_only else self.destructive,
            idempotentHint=self.idempotent if not self.read_only else None,
            openWorldHint=False,
        )


def build_server(project: Project, specs: list[ToolSpec], instructions: str, *, host: str = "127.0.0.1", port: int = 8765):
    from mcp.server.fastmcp import FastMCP

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
          host: str = "127.0.0.1", port: int = 8765) -> None:
    if transport not in ("stdio", "streamable-http"):
        raise ValueError("transport must be 'stdio' or 'streamable-http'")
    if transport == "streamable-http" and host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("refusing to bind a network interface: this server has no authentication. "
                         "Use 127.0.0.1, or put an authenticating proxy in front of it deliberately.")
    build_server(project, specs, instructions, host=host, port=port).run(transport=transport)
