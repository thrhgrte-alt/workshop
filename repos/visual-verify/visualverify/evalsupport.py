"""Eval support: the deterministic solver that runs a task's ``input`` on generated images, and the checks that judge its result.

Task input (YAML)::

    op: engine | tools_seq | recheck | learning | hubshot | files | determinism
    images: {name: {gen: ..., size: ..., where: ws|outside}}      # generated with visualverify.evalgen into a fresh temp folder
    tool/args/overrides/profiles ...                             # per op, see the solver

The solver returns plain dicts. Evals are SELF-WRITTEN: they show the tool agrees with the values the generators and hand calculations say it should
produce. They are not a measure of how well the tool serves real renders (see evals/real/README.md).
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import evalgen as G
from . import learning_params as LP
from .domain import checks as C
from .domain import engine as E
from .domain import hubshot
from .domain import learning as LN
from .guide_adapter import ToolSpec, all_tools, evals as ev, mcpkit, scope as S, telemetry

ROOT = Path(__file__).resolve().parents[1]
BANNED_PHRASES = ("looks good", "look good", "looks great", "looks fine", "looks nice", "looks correct", "is good", "is fine", "is perfect", "well done", "great job", "beautiful", "ugly")


def _ref(v: Any, paths: dict[str, str]) -> Any:
    if isinstance(v, str) and v.startswith("@") and v[1:] in paths:
        return paths[v[1:]]
    if isinstance(v, dict):
        return {k: _ref(x, paths) for k, x in v.items()}
    if isinstance(v, list):
        return [_ref(x, paths) for x in v]
    return v


def _make_images(td: Path, images: dict[str, dict], dirs: dict[str, Path]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, spec in (images or {}).items():
        spec = dict(spec)
        where = spec.pop("where", "ws")
        ext = spec.pop("ext", "png")
        base = dirs.get(where)
        if base is None:
            raise ValueError(f"image '{name}': unknown location '{where}' (known: {sorted(dirs)})")
        path = base / f"{name}.{ext}"
        G.write(spec, path)
        paths[name] = str(path)
    return paths


class _Scope:
    def __init__(self, label="eval"):
        self.label, self.project_id, self.place_id = label, "eval", None


def _engine_ctx(project, td: Path, overrides: dict | None, scope: S.Scope | None, use_store: bool, profiles: dict | None) -> E.Ctx:
    th = LP.Thresholds(project, scope, overrides, use_store=use_store)
    pdir = td / "scope"
    for name, prof in (profiles or {}).items():
        f = pdir / "profiles" / f"{name}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(prof), encoding="utf-8")
    return E.Ctx(project, scope or _Scope(), th, [td / "ws", td / "outside_not_allowed_marker"], profile_dir=pdir, project_dir=pdir)


@contextlib.contextmanager
def _env(td: Path, registry: dict | None):
    keys = ("VISUALVERIFY_WORKSPACE", "VISUALVERIFY_PROJECTS", "VISUALVERIFY_ALLOWED_PATHS", "VISUALVERIFY_DISABLE_GROUPS", "VISUALVERIFY_TELEMETRY")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["VISUALVERIFY_WORKSPACE"] = str(td / "ws")
    if registry is not None:
        text = json.dumps(registry).replace("{td}", str(td))
        reg = td / "projects.yaml"
        reg.write_text(text, encoding="utf-8")  # JSON is valid YAML
        os.environ["VISUALVERIFY_PROJECTS"] = str(reg)
    else:
        os.environ["VISUALVERIFY_PROJECTS"] = str(ROOT / "projects.yaml")
    for k in ("VISUALVERIFY_ALLOWED_PATHS", "VISUALVERIFY_DISABLE_GROUPS", "VISUALVERIFY_TELEMETRY"):
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _tree(ws: Path) -> list[str]:
    return sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file() and not str(p.relative_to(ws)).startswith("imgs/"))


def solve(task: dict, *, project=None, scope: S.Scope | None = None, overrides: dict | None = None, use_store: bool = False) -> dict:
    """Run one eval task. ``overrides`` are threshold values applied in memory (the gate uses them); ``use_store`` reads learned values for ``scope``."""
    from . import project as make_project

    project = project or make_project(ROOT)
    inp = task["input"]
    op = inp["op"]
    if inp.get("pin_defaults"):  # this task checks the shipped defaults themselves: learned values and proposals do not apply to it
        scope, overrides, use_store = None, None, False
    ov = {**(inp.get("overrides") or {}), **(overrides or {})}
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "ws" / "imgs").mkdir(parents=True)
        (td / "outside").mkdir()
        dirs = {"ws": td / "ws" / "imgs", "outside": td / "outside"}
        for k, sub in (inp.get("dirs") or {}).items():
            dirs[k] = td / sub
            dirs[k].mkdir(parents=True, exist_ok=True)
        if op == "recheck":
            th = LP.Thresholds(project, scope, ov, use_store=use_store)
            return LN.recheck(inp["rows"], th)
        if op == "files":
            return _files_op(project, inp)
        if op == "hubshot":
            return _hubshot_op(td, inp, project)
        paths = _make_images(td, inp.get("images"), dirs)
        if op == "engine":
            ctx = _engine_ctx(project, td, ov, scope, use_store, inp.get("profiles"))
            fn = E.RUNNERS[inp["tool"]]
            try:
                rep = fn(ctx, **_ref(inp.get("args", {}), paths))
            except (ValueError, PermissionError) as exc:
                if not inp.get("expect_refusal"):
                    raise
                return {"refused": True, "error": str(exc), "error_type": type(exc).__name__}
            out = E.render(ctx, rep, bool(inp.get("detail")), {})
            return {"summary": out["summary"], "out": out, "rows": [C.public(r) for r in rep["rows"]], "measured": rep["measured"], "detail": rep["detail"],
                    "warnings": rep["warnings"], "chars": telemetry.output_chars(out), "text": json.dumps(out)}
        if op == "determinism":
            ctx1 = _engine_ctx(project, td, ov, scope, use_store, inp.get("profiles"))
            ctx2 = _engine_ctx(project, td, ov, scope, use_store, inp.get("profiles"))
            args = _ref(inp.get("args", {}), paths)
            a = json.dumps(E.render(ctx1, E.RUNNERS[inp["tool"]](ctx1, **args), True, {}), sort_keys=True)
            b = json.dumps(E.render(ctx2, E.RUNNERS[inp["tool"]](ctx2, **args), True, {}), sort_keys=True)
            return {"identical": a == b, "chars": len(a)}
        if op in ("tools_seq", "learning"):
            return _tools_seq(td, inp, paths, project, op == "learning")
    raise ValueError(f"unknown eval op '{op}'")


def _files_op(project, inp: dict) -> dict:
    """Facts about the shipped files (thresholds registered as params, placeholders labelled, locked rails)."""
    meta = LP.raw_thresholds(project)
    specs = {s.name: s for s in LP.param_specs(project)}
    store = LP.store(project)
    return {"n_thresholds": len(meta), "n_specs": len(specs), "all_registered": set(meta) == set(store.specs), "all_defaults_equal": all(specs[n].default == meta[n]["default"] for n in meta),
            "locked": sorted(n for n, s in specs.items() if s.locked), "unlocked_limits": sorted(n for n, s in specs.items() if n.startswith("limits.") and not s.locked),
            "placeholder_flag": bool(__import__("yaml").safe_load((project.root / "style" / "thresholds.yaml").read_text(encoding="utf-8")).get("placeholder")),
            "verify_flagged": sorted(n for n, s in specs.items() if s.verify_against_current_docs),
            "checks_have_op": all(m.get("op") for m in meta.values() if m.get("kind") == "check"),
            "dimensions": sorted({m["dimension"] for m in meta.values()})}


def _hubshot_op(td: Path, inp: dict, project) -> dict:
    import base64

    spec = inp["image"]
    p = G.write(spec, td / "cap.png")
    raw_png = p.read_bytes()
    b64 = base64.b64encode(raw_png).decode()
    form = inp["form"]
    if form == "raw":
        data = raw_png
    elif form == "mcp_block":
        data = json.dumps({"content": [{"type": "text", "text": "ok"}, {"type": "image", "data": b64, "mimeType": "image/png"}]}).encode()
    elif form == "wrapped_result":
        data = json.dumps({"result": {"content": [{"type": "image", "data": b64, "mimeType": "image/png"}]}}).encode()
    elif form.startswith("field:"):
        data = json.dumps({form.split(":", 1)[1]: ("data:image/png;base64," + b64) if inp.get("data_url") else b64}).encode()
    elif form == "unknown_json":
        data = json.dumps({"status": "ok", "frames": 3}).encode()
    elif form == "bad_base64":
        data = json.dumps({"image": "!!!not base64!!!" * 3}).encode()
    elif form == "garbage":
        data = b"\x00\x01 not an image"
    elif form == "broken_json":
        data = b"{this is not json"
    elif form == "not_an_image_b64":
        data = json.dumps({"image": base64.b64encode(b"hello world, not an image at all").decode()}).encode()
    else:
        raise ValueError(f"unknown form {form}")
    try:
        img, info = hubshot.decode(data, inp.get("max_bytes", 1 << 20), inp.get("max_pixels", 4_000_000))
        return {"refused": False, "size": list(img.size), "input_schema": info["input_schema"], "form": info.get("form")}
    except hubshot.CaptureRefused as exc:
        return {"refused": True, "error": str(exc)}


def _tools_seq(td: Path, inp: dict, paths: dict[str, str], project, learning: bool) -> dict:
    results: list[dict] = []
    with _env(td, inp.get("registry")):
        from .hooks import HOOKS

        p = project
        specs = all_tools(p, HOOKS)
        ws = td / "ws"
        refs: dict[str, Any] = {}

        def subst(v):
            if isinstance(v, str) and v.startswith("$ref:"):
                return ev.dig({"steps": results}, v[5:])
            if isinstance(v, str) and v.startswith("@") and v[1:] in paths:
                return paths[v[1:]]
            if isinstance(v, str) and "{td}" in v:
                return v.replace("{td}", str(td))
            if isinstance(v, dict):
                return {k: subst(x) for k, x in v.items()}
            if isinstance(v, list):
                return [subst(x) for x in v]
            return v

        extra: dict[str, Any] = {}
        for st in inp["steps"]:
            kind = st.get("do", "tool")
            try:
                if kind == "refuse_all":
                    r = _refuse_all(specs, st.get("with_place", False))
                elif kind == "tool":
                    r = mcpkit.call_local(specs, st["tool"], subst(st.get("args", {})))
                else:
                    r = _learn_step(p, st, subst)
                results.append({"ok": True, "result": r, "chars": telemetry.output_chars(r)})
            except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as exc:
                results.append({"ok": False, "error": str(exc), "error_type": type(exc).__name__, "result": {}, "chars": 0})
        names = sorted(t.name for t in specs)
        wrote = _tree(ws) if ws.exists() else []
        texts = {w: (ws / w).read_text(encoding="utf-8") for w in wrote if ((w.endswith(".json") and "/profiles/" in w) or (w.endswith(".jsonl") and w.startswith("feedback/")))
                 and (ws / w).stat().st_size < 40000}
        return {"steps": results, "wrote": wrote, "texts": texts, "versions_written": sum("/versions/" in w and w.endswith(".json") and not w.endswith("index.json") for w in wrote), "n_errors": sum(not r["ok"] for r in results), "tool_names": names,
                "read_only": sorted(t.name for t in specs if t.read_only), "writers": sorted(t.name for t in specs if not t.read_only)}


def _refuse_all(specs: list[ToolSpec], with_place: bool) -> dict:
    """Call every tool with only its required arguments and NO project_id: each must refuse naming project_id (nothing is guessed)."""
    import inspect

    bad = []
    for t in specs:
        args = {n: "x" for n, prm in inspect.signature(t.fn).parameters.items() if prm.default is inspect.Parameter.empty}
        if "decision" in args:
            args["decision"] = "accept"
        if with_place and "place_id" in inspect.signature(t.fn).parameters:
            args["place_id"] = "arena"
        try:
            mcpkit.call_local(specs, t.name, args)
            bad.append(t.name)
        except ValueError as exc:
            if "project_id" not in str(exc):
                bad.append(f"{t.name}: {str(exc)[:80]}")
        except Exception as exc:  # any other failure is not a refusal for the right reason
            bad.append(f"{t.name}: {type(exc).__name__}")
    return {"not_refused": bad, "checked": len(specs)}


def _learn_step(project, st: dict, subst) -> dict:
    from . import learn as LR

    sc = S.require_scope(st.get("project_id"), st.get("place_id"), registry=S.ProjectRegistry.load(os.environ["VISUALVERIFY_PROJECTS"]))
    kind = st["do"]
    if kind == "propose":
        return LR.propose(project, sc, st.get("min_runs", 3), st.get("min_rate", 0.5))
    if kind == "gate":
        return LR.gate(project, sc, subst(st["proposal"]))
    if kind == "promote":
        return LR.promote(project, subst(st["proposal"]), st.get("approved_by", ""), st.get("confirm", False))
    if kind == "rollback":
        return LR.rollback(project, st["param"], sc, st.get("approved_by", ""), st.get("reason", "eval"))
    if kind == "value":
        return {"value": LP.Thresholds(project, sc)(st["param"]), "source": LP.Thresholds(project, sc).source(st["param"])}
    raise ValueError(f"unknown step '{kind}'")


# --- gate wiring ---------------------------------------------------------------------------------------------------------------------------
def solver_for(project, scope: S.Scope) -> Callable[[dict | None], Callable[[dict], Any]]:
    """``solver_for(None)`` = the thresholds in force for ``scope``; ``solver_for(proposal)`` = the same with the proposal's change applied in memory."""
    def make(prop: dict | None):
        ov = {prop["change"]["param"]: prop["change"]["after"]} if prop and prop.get("kind") == "param" else {}
        return lambda task: solve(task, project=project, scope=scope, overrides=ov, use_store=True)

    return make


# --- checks ----------------------------------------------------------------------------------------------------------------------------------
def _row(result: dict, rid: str) -> dict | None:
    return next((r for r in result.get("rows", []) if r["id"] == rid), None)


def check_row(result, task, id, passed=None, value=None, tol=1e-6, limit=None):
    r = _row(result, id)
    if r is None:
        return False, f"no check '{id}' in {[x['id'] for x in result.get('rows', [])]}"
    msgs, ok = [], True
    if passed is not None:
        ok &= r["passed"] is passed
        msgs.append(f"passed={r['passed']!r} (expected {passed!r})")
    if value is not None:
        close = r["value"] is not None and abs(r["value"] - value) <= tol
        ok &= close
        msgs.append(f"value={r['value']!r} (expected {value} +/- {tol})")
    if limit is not None:
        close = r["limit"] is not None and abs(r["limit"] - limit) <= 1e-9
        ok &= close
        msgs.append(f"limit={r['limit']!r} (expected {limit})")
    return ok, f"{id}: " + "; ".join(msgs)


def check_row_range(result, task, id, min=None, max=None):
    r = _row(result, id)
    v = None if r is None else r["value"]
    ok = v is not None and (min is None or v >= min) and (max is None or v <= max)
    return ok, f"{id}.value={v!r} expected in [{min}, {max}]"


def check_skipped(result, task, id):
    r = _row(result, id)
    return r is not None and r["passed"] is None and "skipped" in r, f"{id} skipped (got {r})"


def check_approx(result, task, path, value, tol=1e-6):
    got = ev.dig(result, path)
    return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"


def check_list_approx(result, task, path, value, tol=1e-6):
    got = ev.dig(result, path)
    ok = isinstance(got, list) and len(got) == len(value) and all(abs(a - b) <= tol for a, b in zip(got, value))
    return ok, f"{path}={got!r}; expected {value} +/- {tol}"


def check_is(result, task, path, value):
    got = ev.dig(result, path)
    return got == value, f"{path}={got!r}; expected {value!r}"


def check_greater(result, task, path, than):
    got = ev.dig(result, path)
    return isinstance(got, (int, float)) and got > than, f"{path}={got!r} should be > {than}"


def check_less(result, task, path, than):
    got = ev.dig(result, path)
    return isinstance(got, (int, float)) and got < than, f"{path}={got!r} should be < {than}"


def check_text_has(result, task, path, text):
    got = str(ev.dig(result, path) or "")
    return text.lower() in got.lower(), f"{path} should mention {text!r} (got {got[:200]!r})"


def check_text_lacks(result, task, path, text):
    got = str(ev.dig(result, path) or "")
    return text.lower() not in got.lower(), f"{path} must not mention {text!r}"


def check_no_verdict_words(result, task, path="text"):
    got = str(ev.dig(result, path) or "").lower()
    hit = [p for p in BANNED_PHRASES if p in got]
    return not hit, f"{path} must not praise or judge the image; found {hit}"


def check_first_key(result, task, key="summary"):
    out = result.get("out") or {}
    return bool(out) and next(iter(out)) == key, f"first key is {next(iter(out), None)!r}, expected {key!r}"


def check_first_key_of_step(result, task, step, key="summary"):
    r = ev.dig(result, f"steps.{step}.result") or {}
    return bool(r) and next(iter(r)) == key, f"first key of step {step} is {next(iter(r), None)!r}, expected {key!r}"


def check_len_at_most(result, task, path, n):
    got = ev.dig(result, path)
    return isinstance(got, (list, dict, str)) and len(got) <= n, f"len({path})={len(got) if got is not None else None} should be <= {n}"


def check_budget(result, task, chars, step=None):
    got = result.get("chars") if step is None else ev.dig(result, f"steps.{step}.chars")
    return isinstance(got, int) and got <= chars, f"output is {got} characters; budget {chars}"


def check_step_ok(result, task, step):
    ok = ev.dig(result, f"steps.{step}.ok")
    return ok is True, f"step {step} should succeed (got {ev.dig(result, f'steps.{step}.error')!r})"


def check_step_error(result, task, step, text, error_type=None):
    err = ev.dig(result, f"steps.{step}.error") or ""
    ok = ev.dig(result, f"steps.{step}.ok") is False and text.lower() in err.lower()
    if error_type:
        ok &= ev.dig(result, f"steps.{step}.error_type") == error_type
    return ok, f"step {step} should be refused mentioning {text!r} (got {err[:200]!r})"


def check_text_in_file(result, task, file_part, text, present=True):
    hits = [t for name, t in (result.get("texts") or {}).items() if file_part in name]
    ok = bool(hits) and all((text in t) == present for t in hits)
    return ok, f"files matching {file_part!r}: {len(hits)}; {'contains' if present else 'lacks'} {text!r}: {ok}"


def check_proposal_has(result, task, path, param, before, after, tol=1e-9):
    rows = ev.dig(result, path) or []
    hit = next((r for r in rows if r.get("param") == param), None)
    ok = hit is not None and abs(hit["before"] - before) <= tol and abs(hit["after"] - after) <= tol
    return ok, f"{path} should hold a proposal {param} {before} -> {after}; has {[(r.get('param'), r.get('before'), r.get('after')) for r in rows]}"


def check_no_proposal_for(result, task, path, param):
    rows = ev.dig(result, path) or []
    return not any(r.get("param") == param for r in rows), f"{path} must hold no proposal for {param}; has {[r.get('param') for r in rows]}"


def check_wrote_nothing(result, task):
    return result.get("wrote") == [], f"wrote {result.get('wrote')}"


def check_wrote_only(result, task, prefix):
    w = result.get("wrote", [])
    return bool(w) and all(x.startswith(prefix) for x in w), f"wrote {w}; expected all under {prefix!r}"


def check_set_has(result, task, path, items):
    got = set(ev.dig(result, path) or [])
    return set(items) <= got, f"{path}={sorted(got)}; expected to contain {items}"


def check_set_lacks(result, task, path, items):
    got = set(ev.dig(result, path) or [])
    return not (set(items) & got), f"{path}={sorted(got)}; must not contain {items}"


def check_all_pass(result, task):
    return result.get("all_pass") is True, f"all_pass={result.get('all_pass')} failed={result.get('failed_ids')}"


def check_fails_in_dimension(result, task, dimension):
    return dimension in (result.get("failed_dimensions") or []), f"failed dimensions {result.get('failed_dimensions')}; expected {dimension!r} among them"


def check_refused(result, task, text=""):
    return bool(result.get("refused")) and text.lower() in str(result.get("error", "")).lower(), f"refused with {text!r} (got {str(result.get('error'))[:200]!r})"


def check_not_refused(result, task):
    return result.get("refused") is False, f"not refused (got {result})"


def checks() -> dict[str, Callable]:
    return {"row": check_row, "row_range": check_row_range, "skipped": check_skipped, "approx": check_approx, "list_approx": check_list_approx, "is": check_is, "greater": check_greater,
            "less": check_less, "text_has": check_text_has, "text_lacks": check_text_lacks, "no_verdict_words": check_no_verdict_words, "first_key": check_first_key, "first_key_of_step": check_first_key_of_step,
            "len_at_most": check_len_at_most, "budget": check_budget, "step_ok": check_step_ok, "step_error": check_step_error, "wrote_nothing": check_wrote_nothing,
            "wrote_only": check_wrote_only, "proposal_has": check_proposal_has, "no_proposal_for": check_no_proposal_for, "text_in_file": check_text_in_file, "set_has": check_set_has, "set_lacks": check_set_lacks, "all_pass": check_all_pass, "fails_in_dimension": check_fails_in_dimension,
            "refused": check_refused, "not_refused": check_not_refused}
