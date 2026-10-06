"""``python -m guide_core``: tiny CLI (version and a self-check). Domain tools live in the repositories built on guide-core."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys

from . import SCHEMA_VERSION, __version__

MODULES = ("dryrun", "scope", "config", "feedback", "retrieval", "evals", "mock", "luau_safety", "mcpkit", "observe", "params", "propose", "gate", "promote", "skillgen", "telemetry")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="guide-core")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="print the version and the on-disk schema version")
    sub.add_parser("doctor", help="import every public module and report optional dependencies")
    args = p.parse_args(argv)
    if args.cmd == "version":
        print(json.dumps({"guide_core": __version__, "schema_version": SCHEMA_VERSION}))
        return 0
    report = {"guide_core": __version__, "modules": {}, "optional": {}}
    for m in MODULES:
        try:
            importlib.import_module(f"guide_core.{m}")
            report["modules"][m] = "ok"
        except Exception as exc:  # report, do not crash
            report["modules"][m] = f"error: {type(exc).__name__}: {exc}"
    for dep, label in (("lupa", "mock (Luau DataModel mock)"), ("numpy", "imaging"), ("PIL", "imaging"), ("sentence_transformers", "embeddings"), ("mcp", "MCP server")):
        report["optional"][f"{dep} -> {label}"] = importlib.util.find_spec(dep) is not None
    print(json.dumps(report, indent=2))
    return 0 if all(v == "ok" for v in report["modules"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
