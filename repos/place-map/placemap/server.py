"""MCP server entry point: ``python -m placemap.server`` (stdio) or ``place-map-mcp``."""

import sys

from . import project
from .guide_adapter import all_tools, mcpkit, telemetry
from .hooks import HOOKS

serve = mcpkit.serve


def main(argv=None) -> int:
    transport = "stdio"
    args = list(sys.argv[1:] if argv is None else argv)
    if "--http" in args:
        transport = "streamable-http"
    p = project()
    tools = telemetry.Telemetry(p.workspace / "telemetry.jsonl").instrument(all_tools(p, HOOKS))
    serve(p, tools, HOOKS.instructions, transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
