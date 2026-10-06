#!/usr/bin/env python3
"""Developer helper: run the eval tasks (optionally only those whose id starts with a prefix) and print failures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from visualverify import project, evalsupport as ES
from visualverify.guide_adapter import evals as ev

pre = sys.argv[1] if len(sys.argv) > 1 else ""
p = project()
tasks = [t for t in ev.load_tasks(p.root / "evals" / "tasks", strict=True) if t["id"].startswith(pre)]
rep = ev.run_suite(tasks, lambda t: ES.solve(t, project=p), ES.checks(), label="quick", root=p.root)
for t in rep["tasks"]:
    if not t["passed"]:
        print("FAIL", t["id"])
        for c in t["checks"]:
            if not c["passed"]:
                print("     ", c["message"])
print(f"{rep['passed']}/{rep['total']} passed")
