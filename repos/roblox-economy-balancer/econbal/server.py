"""MCP server entry point: ``python -m econbal.server`` (stdio) or ``roblox-economy-balancer-mcp``."""

import sys

from . import project
from .guide_adapter import all_tools, mcpkit

serve = mcpkit.serve
from .hooks import HOOKS


def main(argv=None) -> int:
    transport = "stdio"
    args = list(sys.argv[1:] if argv is None else argv)
    if "--http" in args:
        transport = "streamable-http"
    p = project()
    serve(p, all_tools(p, HOOKS), HOOKS.instructions, transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
