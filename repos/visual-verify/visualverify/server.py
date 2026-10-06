"""MCP server entry point: ``python -m visualverify.server`` (stdio) or ``visual-verify-mcp``.

Set VISUALVERIFY_DISABLE_GROUPS=rare to leave out the `rare` tool group (see README "Tool groups")."""

import sys

from . import project
from .guide_adapter import all_tools, mcpkit
from .hooks import HOOKS


def main(argv=None) -> int:
    transport = "stdio"
    args = list(sys.argv[1:] if argv is None else argv)
    if "--http" in args:
        transport = "streamable-http"
    p = project()
    mcpkit.serve(p, all_tools(p, HOOKS), HOOKS.instructions, transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
