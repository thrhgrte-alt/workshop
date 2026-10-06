#!/usr/bin/env python3
"""Run the toy-tool eval suite (self-written tasks in evals/tasks, real examples in evals/real, reported separately).

    python evals/run_evals.py run --label mine          # run, save evals/reports/mine.json + mine.md
    python evals/run_evals.py compare baseline mine     # regressions between two saved runs (exit 1 if any)
    python evals/run_evals.py mutate                    # prove the suite can fail: break the toy 6 ways, show what fails
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from guide_core import evals  # noqa: E402
import toytool  # noqa: E402

REPORTS = ROOT / "evals" / "reports"


def run(label: str, broken: str | None = None) -> dict:
    split = evals.load_split(ROOT / "evals" / "tasks", ROOT / "evals" / "real")
    return evals.run_split(split, lambda t: toytool.solve(t, broken), toytool.CHECKS, label=label, root=ROOT)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", default="run")
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    sub.add_parser("mutate")
    a = p.parse_args(argv)
    if a.cmd == "run":
        rep = run(a.label)
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / f"{a.label}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        (REPORTS / f"{a.label}.md").write_text(evals.render_markdown(rep), encoding="utf-8")
        sw, rl = rep["self_written"], rep["real"]
        print(f"self-written: {sw['passed']}/{sw['total']} passed (these only show the tool agrees with itself)")
        print(f"real:         {rl['passed']}/{rl['total']} passed" + ("  <- no real examples yet, see evals/real/README.md" if not rl["total"] else ""))
        for t in sw["tasks"] + rl["tasks"]:
            if not t["passed"]:
                print("FAIL", t["id"], "; ".join(c["message"] for c in t["checks"] if not c["passed"]))
        return 0 if sw["passed"] == sw["total"] and rl["passed"] == rl["total"] else 1
    if a.cmd == "compare":
        load = lambda n: json.loads((REPORTS / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
        b, n = load(a.base), load(a.new)
        out = {}
        for part in ("self_written", "real"):
            out[part] = evals.compare_detailed(b[part], n[part])
        print(json.dumps({k: {x: v[x] for x in ("base_score", "new_score", "regressions", "improvements", "regression_details")} for k, v in out.items()}, indent=1))
        return 1 if any(v["regressed"] for v in out.values()) else 0
    if a.cmd == "mutate":
        for mode in toytool.BROKEN_MODES:
            rep = run(f"mutant-{mode}", mode)
            failed = [t["id"] for t in rep["self_written"]["tasks"] if not t["passed"]]
            print(f"{mode:15s} -> {len(failed)} task(s) fail: {', '.join(failed[:6])}{' ...' if len(failed) > 6 else ''}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
