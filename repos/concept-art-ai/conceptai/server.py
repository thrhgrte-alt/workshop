"""MCP server entry point: ``python -m conceptai.server`` (stdio) or ``concept-art-ai-mcp``."""

import sys

from . import project
from .core.cli import all_tools
from .core.mcpkit import serve
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
