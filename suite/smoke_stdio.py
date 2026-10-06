#!/usr/bin/env python3
"""Launch a repository's MCP server as a real subprocess over stdio and call two tools.

Usage: python suite/smoke_stdio.py <repo-dir> <package> <tool-to-call> [json-args]
Proves the shipped entry point (`python -m <package>.server`) speaks MCP, beyond the in-memory tests.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(repo: str, package: str, tool: str, args: dict) -> int:
    env = {**os.environ, f"{package.upper()}_ROOT": repo, "PYTHONPATH": repo}
    params = StdioServerParameters(command=sys.executable, args=["-m", f"{package}.server"], env=env, cwd="/")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            result = await session.call_tool(tool, args)
            print(json.dumps({"server": init.serverInfo.name, "tool_count": len(tools),
                              "read_only": sum(bool(t.annotations and t.annotations.readOnlyHint) for t in tools),
                              "called": tool, "is_error": result.isError,
                              "result_keys": sorted((result.structuredContent or {}).keys())[:8]}))
            return 1 if result.isError else 0


if __name__ == "__main__":
    repo, package, tool = sys.argv[1:4]
    sys.exit(asyncio.run(main(os.path.abspath(repo), package, tool, json.loads(sys.argv[4]) if len(sys.argv) > 4 else {})))
