"""The deterministic eval solver and the checks the eval tasks use (evals/tasks/*.yaml, evals/real/*.yaml).

A task's ``input.op`` picks what the solver does. Almost every op runs the REAL tools against a throwaway workspace into which the synthetic places (examples/places) were ingested through
``ingest_snapshot``, so a broken tool breaks the evals. ``input.ingest`` lets a task (in particular a REAL one) ingest its own saved hub output first.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import learning_params as LP
from .domain import collector, ingest as IN, mock_extra, places, roles as R, scan
from .domain.index import PlaceIndex
from .domain.snapshot import Snapshot
from .domain.util import fingerprint
from .domain.view import PlaceView
from .guide_adapter import all_tools, evals as evals_mod, luau_safety, mcpkit, scope as S

NOW = "2026-10-06T12:00:00+00:00"
FRESH = "2026-10-06T11:00:00+00:00"
FIXTURES = [("demo_mine", "dive-and-mine", "mine-main"), ("demo_mine", "dive-and-mine-hardcore", "mine-hardcore"), ("demo_roles", "role-lab", "role-lab"),
            ("demo_tycoon", "tycoon-main", "tycoon-main")]
DEMO = {"project_id": "demo_mine", "place_id": "dive-and-mine"}
dig = evals_mod.dig


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: v for k, v in kv.items() if v is not None})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class World:
    """Synthetic places ingested through the real tool, in a private temporary workspace (never the user's)."""

    def __init__(self, project):
        self.project = project
        self._base: Path | None = None
        self._dirs: list[Path] = []

    def _tool(self, name):
        from .hooks import HOOKS

        specs = all_tools(self.project, HOOKS)
        return lambda **kw: mcpkit.call_local(specs, name, kw)

    def _build(self, ws: Path, fixtures, now: str = NOW, taken: str = FRESH) -> None:
        with env(PLACEMAP_WORKSPACE=str(ws), PLACEMAP_NOW=now):
            ingest = self._tool("ingest_snapshot")
            for pid, plid, f in fixtures:
                path = f if str(f).endswith(".json") else f"examples/places/{f}.collect.json"
                ingest(project_id=pid, place_id=plid, file=str(self.project.root / path), dry_run=False, taken_at=taken)

    @property
    def base(self) -> Path:
        if self._base is None:
            self._base = Path(tempfile.mkdtemp(prefix="placemap-eval-base-"))
            self._build(self._base, FIXTURES)
        return self._base

    @contextlib.contextmanager
    def use(self, which: str = "base", now: str | None = None, ingest: list | None = None):
        if which == "base" and not ingest:
            ws = self.base
        elif which == "empty":
            ws = Path(tempfile.mkdtemp(prefix="placemap-eval-"))
            self._dirs.append(ws)
        else:
            ws = Path(tempfile.mkdtemp(prefix="placemap-eval-"))
            self._dirs.append(ws)
            shutil.copytree(self.base, ws, dirs_exist_ok=True)
        if ingest:
            self._build(ws, [(i["project_id"], i["place_id"], i["file"]) for i in ingest])
        with env(PLACEMAP_WORKSPACE=str(ws), PLACEMAP_NOW=now or NOW):
            yield ws

    def cleanup(self) -> None:
        for d in [*self._dirs, *([self._base] if self._base else [])]:
            shutil.rmtree(d, ignore_errors=True)


def _json_size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":")))


def dummy_args(tool, scope: dict | None = None) -> dict:
    given = {"query": "Bob", "request": "x", "run_id": "run-x", "decision": "accept", "path": "Workspace/Vendors/Bob", "role": "vendor", "label_id": "lbl-x", "data": {"format": "x"}}
    args: dict = {}
    for n, prm in inspect.signature(tool.fn).parameters.items():
        if n in given and prm.default is inspect.Parameter.empty:
            args[n] = given[n]
    if scope:
        for n, v in scope.items():
            if n in inspect.signature(tool.fn).parameters:
                args[n] = v
    return args


def mini_view(project, nodes: list[dict], place: str = "mini", synonyms_project: str | None = None) -> PlaceView:
    """A PlaceView over a tiny inline collector-node list (for the unit-level feature evals). Nothing is stored."""
    d = {"format": IN.COLLECT_FORMAT, "collector_version": 1, "place": {"name": "Mini", "place_id": 0}, "taken_unix": 1790000000, "roots": sorted({n["p"].split("/")[0] for n in nodes}), "full": True, "nodes": nodes}
    for n in d["nodes"]:
        if "s" in n and "src" in n["s"] and "fp" not in n["s"]:
            n["s"]["fp"] = fingerprint(n["s"]["src"])
    parsed = IN.parse_input(d)

    class NoCache:
        def get(self, h):
            return None

    built = IN.build_snapshot(parsed, {"project_id": "mini", "place_id": place}, None, NoCache())
    snap = Snapshot(built.data)

    class Ctx:
        pass

    ctx = places.resolve(project, "demo_roles", "role-lab")
    v = PlaceView.__new__(PlaceView)
    v.ctx, v.snap, v.idx = ctx, snap, PlaceIndex(snap)
    v.ps = LP.store(project, "demo_roles")
    v.pv = LP.values_for(project, "demo_roles", "role-lab", v.ps)
    v.roles, v.syn = R.load_roles(project, None), R.load_synonyms(project, synonyms_project)
    v._graph = v._scorer = None
    v.limits = LP.limits(project)

    class NoLabels:
        file = Path("/nonexistent/labels.jsonl")

        def positive(self):
            return {}

        def active(self):
            return {}

        def aliases(self):
            return {}

    v.labels = NoLabels()
    skip = set((v.limits.get("scoring") or {}).get("skip_classes", []))
    v._scorer = R.Scorer(v.idx, v.roles, v.syn, v.pv, skip, {})
    return v


def eval_solver(project):
    world = World(project)
    from .hooks import HOOKS

    specs = all_tools(project, HOOKS)
    truth = json.loads((project.root / "examples" / "places" / "ground_truth.json").read_text(encoding="utf-8"))

    def call(name: str, args: dict) -> dict:
        try:
            res = mcpkit.call_local(specs, name, args)
            return {"ok": True, "result": res, "chars": _json_size(res)}
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    def subst(v, steps):
        if isinstance(v, dict):
            return {k: subst(x, steps) for k, x in v.items()}
        if isinstance(v, list):
            return [subst(x, steps) for x in v]
        if isinstance(v, str) and v.startswith("$ref:"):
            return dig({"steps": steps}, v[5:])
        return v

    def pr(world_ws) -> dict:
        per, tp_all, fp_all, fn_all, decoys_conf = {}, 0, 0, 0, []
        for place_key, (pid, plid) in {"mine-main": ("demo_mine", "dive-and-mine"), "role-lab": ("demo_roles", "role-lab")}.items():
            ctx = places.resolve(project, pid, plid)
            v = PlaceView.load(ctx)
            gt = truth[place_key]
            for role in v.roles:
                expected = {f"{plid}::{p}" for p in gt.get(role, [])}
                sel = {e["path"] for e in v.role_resolution(role, k=500)["selected"]}
                tp, fp, fn = len(sel & expected), len(sel - expected), len(expected - sel)
                tp_all += tp
                fp_all += fp
                fn_all += fn
                per[f"{place_key}:{role}"] = {"tp": tp, "fp": fp, "fn": fn, "false_positives": sorted(sel - expected)[:5], "missed": sorted(expected - sel)[:5]}
                for d in gt.get("decoys", []):
                    if f"{plid}::{d}" in sel:
                        decoys_conf.append(f"{plid}::{d} as {role}")
        return {"precision": tp_all / (tp_all + fp_all) if tp_all + fp_all else 0.0, "recall": tp_all / (tp_all + fn_all) if tp_all + fn_all else 0.0, "tp": tp_all, "fp": fp_all, "fn": fn_all,
                "decoys_confident": sorted(set(decoys_conf)), "per_role": per, "set": "self-written synthetic places (shows the tool agrees with the author, not real-world precision)"}

    def collector_mock(inp: dict) -> dict:
        if not mock_extra.M.available():
            return {"mock": "skipped"}
        lim = LP.limits(project)["collector"]
        limits = {k: v["value"] for k, v in lim.items() if isinstance(v, dict) and "value" in v}
        limits.update(inp.get("limits", {}))
        m = mock_extra.ExtendedMock()
        m.set_place(inp.get("open_name", inp.get("place_name", "Mini (SYNTHETIC)")), inp.get("open_id", 0))
        m.build_tree(inp["tree"])
        try:
            code = collector.build_collector(place_number=inp.get("place_id", 0), place_name=inp.get("place_name", "Mini (SYNTHETIC)"), label="eval/place", limits=limits, roots=inp.get("roots", ["Workspace"]),
                                             full=inp.get("full", False), known_fps=inp.get("known", {}), emit=inp.get("emit", "return"), skip_classes=lim.get("skip_classes", []), flt=IN.default_filter())
        except ValueError as exc:
            return {"error": str(exc), "mock": "not run"}
        out = {"lint": luau_safety.lint(code), "chars": len(code), "has_guard": "wrong place" in code, "read_only": all(t not in code for t in (".Parent =", "Instance.new", "SetAttribute(", ".Source =", ":Destroy(")),
               "mock": "ran"}
        try:
            raw = m.run(code)
        except Exception as exc:
            out["error"] = str(exc).splitlines()[0]
            return out
        if inp.get("emit", "return") == "print":
            raw = "\n".join(m.printed)
            out["printed_lines"] = len(m.printed)
        parsed = IN.parse_input(raw)
        out.update({"format": parsed.format, "paths": sorted(n["path"] for n in parsed.instances), "n_nodes": len(parsed.instances), "secrets_skipped": parsed.skipped_secrets,
                    "attrs": {n["path"]: n["attrs"] for n in parsed.instances if n["attrs"]}, "sources_sent": sorted(p for p, s in parsed.script_src.items() if s["src"] is not None),
                    "fp_matches_python": all(s["fp"] == fingerprint(s["src"]) for s in parsed.script_src.values() if s["src"] is not None),
                    "positions": {n["path"]: n["pos"] for n in parsed.instances if n["pos"]}, "meta": {k: parsed.meta.get(k) for k in ("truncated", "full", "roots", "skipped")}})
        return out

    def solve(task: dict) -> Any:
        inp, op = task["input"], task["input"]["op"]
        which = inp.get("world", "base")
        if op == "tool":
            with world.use(which, inp.get("now"), inp.get("ingest")):
                return call(inp["tool"], inp.get("args", {}))
        if op == "steps":
            steps: list[dict] = []
            with world.use("empty" if which == "empty" else "fresh", inp.get("now"), inp.get("ingest")) as ws:
                for st in inp["steps"]:
                    steps.append(call(st["tool"], subst(st.get("args", {}), steps)))
                files = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
            return {"steps": steps, "workspace_files": files, "n_errors": sum(1 for s in steps if not s["ok"])}
        if op == "pr":
            with world.use("base"):
                return pr(None)
        if op == "pr_after_steps":
            res = {}
            with world.use("fresh"):
                for st in inp["steps"]:
                    call(st["tool"], st.get("args", {}))
                res = pr(None)
            return res
        if op == "scan":
            a = scan.analyze(inp["source"], inp.get("class", "ModuleScript"))
            return {**a, "requires_chains": ["/".join(r["chain"]) if r["chain"] else None for r in a["requires"]], "fired": sorted({r["name"] for r in a["remotes_fired"]}),
                    "handled": sorted({r["name"] for r in a["remotes_handled"]}), "all_text": json.dumps(a)}
        if op == "parse":
            data = inp.get("data")
            if inp.get("file"):
                data = (project.root / inp["file"]).read_text(encoding="utf-8")
            try:
                p = IN.parse_input(data)
            except ValueError as exc:
                return {"error": str(exc)}
            return {"format": p.format, "status": p.status, "kind": p.kind, "n_instances": len(p.instances), "paths": [n["path"] for n in p.instances], "script_paths": sorted(p.script_src),
                    "warnings": p.warnings, "secrets_skipped": p.skipped_secrets, "attrs": {n["path"]: n["attrs"] for n in p.instances if n["attrs"]}, "meta_keys": sorted(p.meta)}
        if op == "collector_mock":
            return collector_mock(inp)
        if op == "collector_build":
            lim = LP.limits(project)["collector"]
            limits = {k: v["value"] for k, v in lim.items() if isinstance(v, dict) and "value" in v}
            try:
                code = collector.build_collector(place_number=inp.get("place_id", 0), place_name=inp.get("place_name", "Mini (SYNTHETIC)"), label="eval/place", limits=limits, roots=inp["roots"], full=False,
                                                 known_fps=inp.get("known", {}), emit="return", skip_classes=[], flt=IN.default_filter())
            except ValueError as exc:
                return {"error": str(exc)}
            return {"error": None, "lint": luau_safety.lint(code), "chars": len(code)}
        if op == "feature":
            v = mini_view(project, inp["nodes"], synonyms_project=inp.get("synonyms_project"))
            row = v.scorer.score_one(inp["role"], inp["path"])
            return {"score": row["score"], "features": row["features"], "band": row["band"], "weights": row["weights"]}
        if op == "formula":
            w, f = inp["weights"], inp["f"]
            return {"score": R.Scorer.combine(w, f)}
        if op == "determinism":
            outs = []
            for _ in range(2):
                with world.use(which):
                    outs.append(call(inp["tool"], inp.get("args", {})))
            return {"identical": outs[0] == outs[1], "ok": outs[0]["ok"]}
        if op == "refuse_all":
            bad = []
            with world.use("empty"):
                for t in specs:
                    if t.name in inp.get("exempt", []):
                        continue
                    args = dummy_args(t)
                    try:
                        mcpkit.call_local(specs, t.name, args)
                        bad.append(t.name)
                    except S.ScopeError as exc:
                        if "project_id" not in str(exc):
                            bad.append(f"{t.name}: {str(exc)[:80]}")
                    except Exception as exc:
                        bad.append(f"{t.name}: {type(exc).__name__}")
            return {"not_refused": bad, "checked": len(specs)}
        if op == "scope_stated":
            missing = []
            with world.use("base"):
                for t in specs:
                    if not t.read_only or t.name in inp.get("exempt", []):
                        continue
                    args = dummy_args(t, scope={**DEMO, "project_id": "demo_mine", "place_id": "dive-and-mine", "place_a": "dive-and-mine", "place_b": "dive-and-mine-hardcore", "k": 5})
                    if t.name in ("get_path_info", "who_uses", "show_dependencies", "summarize_area"):
                        args["path"] = "ReplicatedStorage/Modules/Economy" if t.name != "summarize_area" else "Workspace/Vendors"
                    if t.name == "list_remotes":
                        args.pop("name", None)
                    try:
                        res = mcpkit.call_local(specs, t.name, args)
                    except Exception as exc:
                        missing.append(f"{t.name}: {type(exc).__name__} {str(exc)[:60]}")
                        continue
                    if not (res.get("project_id") == "demo_mine" and (res.get("place_id") == "dive-and-mine" or res.get("cross_place"))) or not res.get("summary"):
                        missing.append(t.name)
            return {"missing": missing}
        if op == "tools_meta":
            from .guide_adapter import mcpkit as mk

            allt = specs
            old = os.environ.get("PLACEMAP_DISABLE_GROUPS")
            os.environ["PLACEMAP_DISABLE_GROUPS"] = "rare"
            try:
                core = all_tools(project, HOOKS)
            finally:
                if old is None:
                    os.environ.pop("PLACEMAP_DISABLE_GROUPS", None)
                else:
                    os.environ["PLACEMAP_DISABLE_GROUPS"] = old
            return {"names": sorted(t.name for t in allt), "read_only": sorted(t.name for t in allt if t.read_only), "writers": sorted(t.name for t in allt if not t.read_only),
                    "max_description_chars": max(len(t.description) for t in allt), "longest": max(allt, key=lambda t: len(t.description)).name,
                    "rare": sorted({t.name for t in allt} - {t.name for t in core}), "n_all": len(allt), "n_core": len(core),
                    "writers_without_dry_run": [t.name for t in allt if not t.read_only and "dry_run" in inspect.signature(t.fn).parameters and inspect.signature(t.fn).parameters["dry_run"].default is not True],
                    "unlabelled": [t.name for t in allt if not (t.description or "").strip()], "mk": bool(mk)}
        if op == "budgets":
            budgets = yaml.safe_load((project.root / "evals" / "budgets.yaml").read_text(encoding="utf-8"))["tools"]
            over, sizes = [], {}
            with world.use("base"):
                for name, b in budgets.items():
                    r = call(name, b["args"])
                    if not r["ok"]:
                        over.append(f"{name}: failed: {r['error'][:80]}")
                        continue
                    sizes[name] = r["chars"]
                    if r["chars"] > b["max_chars"]:
                        over.append(f"{name}: {r['chars']} > {b['max_chars']}")
            return {"over": over, "sizes": sizes, "tools": len(budgets)}
        if op == "writes_nothing":
            with world.use("fresh") as ws:
                before = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
                res = [call(c["tool"], c.get("args", {})) for c in inp["calls"]]
                after = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
            return {"changed_files": sorted(set(after) ^ set(before)), "all_ok": all(r["ok"] for r in res), "dry_run_flags": [r["result"].get("dry_run") for r in res if r["ok"]], "errors": [r.get("error") for r in res if not r["ok"]]}
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def eval_checks(project) -> dict:
    def approx(result, task, path, value, tol=1e-4):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def list_equals(result, task, path, items):
        got = dig(result, path) or []
        return sorted(got) == sorted(items), f"{path}={sorted(got)}; expected exactly {sorted(items)}"

    def list_empty(result, task, path):
        got = dig(result, path)
        return got == [] or got is None, f"{path}={got!r} should be empty"

    def not_empty(result, task, path):
        got = dig(result, path)
        return bool(got), f"{path}={got!r} should not be empty"

    def length(result, task, path, n):
        got = dig(result, path)
        return isinstance(got, (list, dict, str)) and len(got) == n, f"len({path})={None if got is None else len(got)}; expected {n}"

    def error_contains(result, task, text, path="error"):
        err = dig(result, path) or ""
        return text.lower() in str(err).lower(), f"{path} should mention '{text}' (got {str(err)[:200]!r})"

    def ok(result, task, path="ok"):
        got = dig(result, path)
        return got is True, f"{path}={got!r} should be true (error: {str(dig(result, 'error'))[:160]!r})"

    def refused(result, task, text=None):
        good = result.get("ok") is False and (text is None or text.lower() in str(result.get("error", "")).lower())
        return good, f"expected a refusal{f' mentioning {text!r}' if text else ''} (got ok={result.get('ok')}, error={str(result.get('error'))[:200]!r})"

    def greater(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got > than, f"{path}={got!r} should be > {than}"

    def less(result, task, path, than):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got < than, f"{path}={got!r} should be < {than}"

    def text_contains(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() in got.lower(), f"{path} should mention '{text}' (got {got[:200]!r})"

    def text_excludes(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() not in got.lower(), f"{path} must not mention '{text}' (got {got[:200]!r})"

    def starts_with_all(result, task, path, prefix):
        got = dig(result, path) or []
        bad = [g for g in got if not str(g).startswith(prefix)]
        return bool(got) and not bad, f"every item of {path} should start with {prefix!r}; offenders {bad[:3]}"

    def mock_ok_or_skipped(result, task):
        return result.get("mock") in ("ran", "skipped"), f"mock status {result.get('mock')!r} (error: {result.get('error')})"

    def step(result, task, step, path, value=None, **kw):
        got = dig(result, f"steps.{step}.{path}")
        return got == value, f"steps.{step}.{path}={got!r}; expected {value!r}"

    def step_error_contains(result, task, step, text):
        err = dig(result, f"steps.{step}.error") or ""
        return text.lower() in err.lower(), f"step {step} should be refused mentioning '{text}' (got {err[:200]!r})"

    def step_ok(result, task, step):
        got = dig(result, f"steps.{step}.ok")
        return got is True, f"step {step} should succeed (got {dig(result, f'steps.{step}.error')!r})"

    def same(result, task, a, b):
        x, y = dig(result, a), dig(result, b)
        return x is not None and x == y, f"{a}={x!r} should equal {b}={y!r}"

    def pluck(result, path, field):
        got = dig(result, path) or []
        return [dig(x, field) for x in got]

    def pluck_equals(result, task, path, field, items):
        got = pluck(result, path, field)
        key = lambda x: json.dumps(x, sort_keys=True, default=str)  # noqa: E731
        return sorted(got, key=key) == sorted(items, key=key), f"{path}[].{field}={got}; expected exactly {items}"

    def pluck_contains(result, task, path, field, items):
        got = pluck(result, path, field)
        return all(i in got for i in items), f"{path}[].{field}={got}; expected to contain {items}"

    def pluck_excludes(result, task, path, field, items):
        got = pluck(result, path, field)
        return not any(i in got for i in items), f"{path}[].{field}={got}; must not contain {items}"

    def top_hit(result, task, path):
        res = result.get("result") or {}
        first = (res.get("selected") or res.get("not_selected") or [{}])[0].get("path")
        return first == path, f"first hit is {first!r}; expected {path!r}"

    def every_hit_has_place(result, task, path="result.hits"):
        hits = dig(result, path) or []
        bad = [h["path"] for h in hits if not h["path"].startswith(h["place"] + "::")]
        return bool(hits) and not bad, f"{len(hits)} hit(s); without their place prefix: {bad[:3]}"

    return {"same": same, "pluck_equals": pluck_equals, "pluck_contains": pluck_contains, "pluck_excludes": pluck_excludes, "top_hit": top_hit, "every_hit_has_place": every_hit_has_place, "approx": approx, "list_contains": list_contains, "list_excludes": list_excludes, "list_equals": list_equals, "list_empty": list_empty, "not_empty": not_empty, "length": length,
            "error_contains": error_contains, "ok": ok, "refused": refused, "greater": greater, "less": less, "text_contains": text_contains, "text_excludes": text_excludes,
            "starts_with_all": starts_with_all, "mock_ok_or_skipped": mock_ok_or_skipped, "step": step, "step_error_contains": step_error_contains, "step_ok": step_ok}
