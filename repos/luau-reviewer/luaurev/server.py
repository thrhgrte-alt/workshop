"""MCP server entry point: ``python -m luaurev.server`` (stdio) or ``luau-reviewer-mcp``.

Set LUAUREV_DISABLE_RARE=1 to leave out the `rare` tool group (see README "Tool groups"): the day-to-day tools stay, the rarely used ones are not loaded into the model's context."""

import sys

from . import project
from .guide_adapter import config, mcpkit
from .hooks import HOOKS
from .tools import RARE_TOOLS


def selected_tools(p):
    tools = config.all_tools(p, HOOKS)
    if p.env("DISABLE_RARE") in ("1", "true", "yes"):
        tools = [t for t in tools if t.name not in RARE_TOOLS]
    return tools


def main(argv=None) -> int:
    transport = "stdio"
    args = list(sys.argv[1:] if argv is None else argv)
    if "--http" in args:
        transport = "streamable-http"
    p = project()
    mcpkit.serve(p, selected_tools(p), HOOKS.instructions, transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
