"""Wires the concept-art domain into the shared CLI, MCP server and eval runner."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from . import tools as tools_mod
from .core import feedback as fb
from .core import retrieval
from .core.cli import DomainHooks, all_tools, emit
from .core.manifest import LibraryStore
from .core.mcpkit import call_local
from .core.project import Project
from .core.style import load_style
from .domain import adapters, analysis, direction as D, lora, prompts, synthetic

INSTRUCTIONS = """\
Concept-art assistant for Roblox-style ENVIRONMENTS and PROPS. A generated image is concept art only: never present it as a production-ready mesh,
Substance graph or finished Roblox scene. Workflow: get_style_brief -> find_past_corrections -> search_concept_library (read all avoid-examples) ->
extract_reference_traits (only images the user owns or is licensed to use) -> propose_directions (several that differ on >= 3 axes; environments and
props are separate workflows) -> build_prompt -> generate_concepts with dry_run=true, review the prompts, then dry_run=false with the adapter the user
has configured (list_image_adapters; 'placeholder' images are synthetic fixtures) -> analyze_image / evaluate_concept / compare_outputs /
check_novelty / check_composition_lock -> look at the images yourself -> rate_concept ONLY with the user's actual words -> promote_concept
(candidate first, confirm only after the user says yes). Pixel metrics are measurements, not taste: brief adherence, usability and style fit stay
unscored until a person scores them. The LoRA tools prepare and validate only; this server never trains. It never uploads files or calls a hosted
image provider itself."""

EXTENSIONS = {"concept": [".png", ".jpg", ".jpeg", ".webp"], "reference_image": [".png", ".jpg", ".jpeg", ".webp"], "direction": [".json", ".yaml"]}


def correction_dimensions(project: Project) -> list[str]:
    return load_style(project).get("correction_dimensions", [])


def doctor_checks(project: Project) -> dict:
    return {
        "imaging": "available" if analysis.available() else "unavailable (pip install -e '.[imaging]'): analysis and the placeholder adapter need numpy + Pillow",
        "adapters": [a.describe() for a in adapters.registry().values()],
        "image_generation": "no hosted provider is built in; configure CONCEPTAI_IMAGE_COMMAND to wrap your own generator",
        "lora": "prepares and validates only; this repository never trains",
        "works_without_a_generator": ["directions", "prompts", "reference trait extraction", "image analysis", "novelty and composition-lock checks", "rubric",
                                      "run records and feedback", "LoRA dataset validation and config", "evals", "MCP server"],
        "needs_user_setup": ["actual image generation (your command adapter + model)", "licensed reference images", "a LoRA trainer (optional)"],
    }


@contextlib.contextmanager
def _temp_workspace():
    old = os.environ.get("CONCEPTAI_WORKSPACE")
    with tempfile.TemporaryDirectory() as d:
        os.environ["CONCEPTAI_WORKSPACE"] = d
        try:
            yield Path(d)
        finally:
            if old is None:
                os.environ.pop("CONCEPTAI_WORKSPACE", None)
            else:
                os.environ["CONCEPTAI_WORKSPACE"] = old


def _dir_of(inp: dict, brief: dict) -> dict:
    """A direction for tests: generated for the brief, with optional axis overrides and palette."""
    d = D.generate_directions(brief, 1, inp.get("seed", 0))["directions"][0]
    if inp.get("palette"):
        d = {**d, "palette": [{"role": r, "hex": h} for r, h in zip(("dominant", "secondary", "accent", "shadow", "highlight"), inp["palette"])]}
    return d


def _imgs(specs: dict, root: Path) -> dict[str, str]:
    out = {}
    for name, spec in specs.items():
        s = dict(spec)
        if s["kind"] == "direction":
            s["direction"] = _dir_of(s, s.get("brief", {"subject": "x", "kind": "environment"}))
        out[name] = synthetic.from_spec(s, root / f"{name}.png")
    return out


def _write_script(root: Path, body: str) -> list[str]:
    script = root / "gen.py"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script), "{out}", "{seed}"]


GEN_OK = "import sys\nfrom PIL import Image\nImage.new('RGB',(64,64),(10,20,30)).save(sys.argv[1])\n"


def eval_solver(project: Project):
    def solve(task: dict) -> dict:
        inp, op = task["input"], task["input"]["op"]
        if op == "directions":
            try:
                res = D.generate_directions(inp["brief"], inp.get("n", 4), inp.get("seed", 0), inp.get("min_axes_different", 3))
            except ValueError as exc:
                return {"error": str(exc)}
            ds = res["directions"]
            locked = res["locked_axes"]
            used = {a: len({d["axes"][a] for d in ds}) for a in ds[0]["axes"]} if ds else {}
            out = {"count": len(ds), "min_axes_differing": res["min_axes_differing"], "min_pairwise_distance": res["min_pairwise_distance"], "warnings": res["warnings"],
                   "unique_ids": len({d["id"] for d in ds}) == len(ds), "locked_held": all(d["axes"][a] == v for d in ds for a, v in locked.items()),
                   "axes_used": used, "free_axes": res["free_axes"], "all_hex": all(len(d["palette"]) == 5 and all(len(p["hex"]) == 7 for p in d["palette"]) for d in ds),
                   "kinds": sorted({d["kind"] for d in ds}), "palette_locked": all(d["palette_locked"] for d in ds) if ds else None,
                   "first_palette": [p["hex"] for p in ds[0]["palette"]] if ds else [],
                   "incompatible": sum(1 for d in ds if not D._compatible(d["kind"], d["axes"]))}
            if inp.get("check_deterministic"):
                again = D.generate_directions(inp["brief"], inp.get("n", 4), inp.get("seed", 0), inp.get("min_axes_different", 3))
                out["deterministic"] = again == res
            if inp.get("other_seed") is not None:
                other = D.generate_directions(inp["brief"], inp.get("n", 4), inp["other_seed"], inp.get("min_axes_different", 3))
                out["seed_changes_result"] = [d["id"] for d in other["directions"]] != [d["id"] for d in ds]
            return out
        if op == "prompt":
            try:
                brief = inp["brief"]
                d = _dir_of(inp, D.normalize_brief(brief))
                p = prompts.build_prompt(brief, d, load_style(project))
            except ValueError as exc:
                return {"error": str(exc)}
            return {"positive": p["positive"], "negative": p["negative"], "aspect_ratio": p["aspect_ratio"], "width": p["width"], "height": p["height"], "sections": sorted(p["sections"]),
                    "status": p["status"], "note": p["note"]}
        if op == "adapter":
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                brief = {"subject": "a stone well", "kind": "environment"}
                d = _dir_of(inp, brief)
                req = {"prompt": "p", "negative": "n", "width": inp.get("width", 128), "height": inp.get("height", 72), "seed": inp.get("seed", 3), "count": inp.get("count", 2),
                       "settings": {"direction": d}, "stem": "t", **inp.get("request", {})}
                name = inp["adapter"]
                try:
                    if name == "command":
                        body = {"ok": GEN_OK, "exit1": "import sys\nsys.exit(3)\n", "nofile": "pass\n",
                                "badext": "import sys\nopen(sys.argv[1].replace('.png', '.exe'), 'wb').write(b'x')\n",
                                "symlink": "import sys, pathlib\npathlib.Path(sys.argv[1]).symlink_to(pathlib.Path(__file__))\n"}.get(inp.get("script"))
                        argv = _write_script(root, body) if body else None
                        ad = adapters.CommandAdapter(argv, model="test-model")
                    else:
                        ad = adapters.get(name)
                    files = ad.generate(req, root / "out")
                    again = ad.generate(req, root / "out2") if inp.get("check_deterministic") else None
                except ValueError as exc:
                    return {"error": str(exc)}
                return {"files": len(files), "names": [Path(f["path"]).name for f in files], "seeds": [f["seed"] for f in files], "has_sha": all(len(f["sha256"]) == 64 for f in files),
                        "distinct_files": len({f["sha256"] for f in files}) == len(files) if files else None,
                        "deterministic": [f["sha256"] for f in files] == [f["sha256"] for f in again] if again is not None else None,
                        "available": ad.available()[0]}
        if op in ("measure", "palette", "readability"):
            with tempfile.TemporaryDirectory() as td:
                paths = _imgs({"a": inp["image"]}, Path(td))
                m = analysis.measure(paths["a"], target_palette=inp.get("palette", ()), focal_box=inp.get("focal_box"))
                rd = analysis.readability(m, load_style(project).get("ranges", {}))
                return {**{k: v for k, v in m.items() if isinstance(v, (int, float))}, "readability": rd["score"], "degenerate": analysis.degenerate(m), "first_palette": m["palette"][0]["hex"],
                        "silhouette_components": m["silhouette_components"]}
        if op == "similarity":
            with tempfile.TemporaryDirectory() as td:
                paths = _imgs(inp["images"], Path(td))
                a, b = inp["pair"]
                return {"similarity": analysis.similarity(paths[a], paths[b])}
        if op == "novelty":
            with tempfile.TemporaryDirectory() as td:
                paths = _imgs({"q": inp["image"], **{f"lib{i}": s for i, s in enumerate(inp.get("library", []))}}, Path(td))
                entries = [{"id": f"lib{i}", "path": paths[f"lib{i}"]} for i in range(len(inp.get("library", [])))]
                res = analysis.novelty_vs_library(paths["q"], entries)
                return {"novelty": res["novelty"], "nearest": res["nearest"], "compared": res["compared"]}
        if op == "diversity":
            with tempfile.TemporaryDirectory() as td:
                paths = _imgs(inp["images"], Path(td))
                res = analysis.diversity([paths[k] for k in inp["order"]])
                return {"min_dissimilarity": res["min_dissimilarity"], "pairs": len(res["pairs"])}
        if op == "lock":
            with tempfile.TemporaryDirectory() as td:
                paths = _imgs({"a": inp["image"]}, Path(td))
                lock = inp["lock"]
                try:
                    m = analysis.measure(paths["a"], focal_box=lock.get("focal_box"))
                    f = analysis.check_composition_lock(m, lock)
                except ValueError as exc:
                    return {"error": str(exc)}
                return {"codes": sorted(x["code"] for x in f), "errors": [x["code"] for x in f if x["severity"] == "error"], "focal_contrast": m.get("focal_contrast")}
        if op == "evaluate":
            with _temp_workspace():
                with tempfile.TemporaryDirectory() as td:
                    tb = all_tools(project, HOOKS)
                    paths = _imgs({"a": inp["image"]}, Path(td))
                    roots = os.environ.get("CONCEPTAI_ALLOWED_PATHS")
                    os.environ["CONCEPTAI_ALLOWED_PATHS"] = os.pathsep.join([td, os.environ["CONCEPTAI_WORKSPACE"]])
                    try:
                        direction = _dir_of({"seed": inp["image"].get("seed", 0)}, inp["brief"]) if inp.get("with_direction", True) else None
                        res = call_local(tb, "evaluate_concept", {"path": paths["a"], "brief": inp["brief"], "direction": direction, "manual_scores": inp.get("manual_scores")})
                    except Exception as exc:
                        return {"error": str(exc)}
                    finally:
                        if roots is None:
                            os.environ.pop("CONCEPTAI_ALLOWED_PATHS", None)
                        else:
                            os.environ["CONCEPTAI_ALLOWED_PATHS"] = roots
                r = res["rubric"]
                return {"passed": r["passed"], "complete": r["complete"], "unscored": r["unscored"], "required_failures": r["required_failures"], "score": r["score"],
                        "judged_by_a_person": res["judged_by_a_person"], "status": res["status"], "criteria": {c["id"]: c["score"] for c in r["criteria"]}}
        if op == "search_library":
            store = LibraryStore(project)
            hits = retrieval.search(store, inp.get("query", ""), filters={"domain.subject_kind": inp["subject_kind"]} if inp.get("subject_kind") else None, tags=inp.get("tags", ()), k=inp.get("k", 3))
            return {"retrieved": [h["id"] for h in hits["positive"]], "avoid": [h["id"] for h in hits["negative"]]}
        if op == "lora":
            return _lora_case(project, inp)
        if op == "feedback":
            with _temp_workspace():
                rid = fb.record_run(project, request=inp["request"], retrieved=[])
                fb.record_decision(project, rid, "revise", reason=inp["reason"], corrections=inp["corrections"], allowed_dimensions=correction_dimensions(project))
                found = fb.corrections_for(project, inp["later_request"])
            return {"found_run": bool(found and found[0]["run_id"] == rid)}
        if op == "generate_and_rate":
            return _generate_and_rate(project, inp)
        raise ValueError(f"unknown eval op '{op}'")

    return solve


def _lora_case(project: Project, inp: dict) -> dict:
    """Build a dataset folder from a compact spec and validate it (or make a config)."""
    import yaml

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        n = inp.get("items", 15)
        size = inp.get("size", [640, 640])
        items = []
        def spec_for(i: int, size=size) -> dict:
            bg = "#{:02x}{:02x}{:02x}".format((40 + i * 37) % 200 + 20, (90 + i * 53) % 200 + 20, (150 + i * 29) % 200 + 20)
            fg = "#{:02x}{:02x}{:02x}".format((200 + i * 17) % 230 + 10, (30 + i * 71) % 230 + 10, (120 + i * 43) % 230 + 10)
            x0, y0 = (i % 5) * 0.16, (i // 5) * 0.28 + 0.05
            return {"kind": "box", "bg": bg, "fg": fg, "box": [x0, y0, x0 + 0.25, y0 + 0.3], "size": size}

        for i in range(n):
            spec = spec_for(i)
            name = f"img/{i:03d}.png"
            if i == inp.get("near_duplicate_of_first_at"):
                spec = spec_for(0, [size[0] + 32, size[1] + 32])
            synthetic.from_spec(spec, root / name)
            it = {"file": name, "caption": f"{inp.get('trigger', 'cptstyle')} stylised ruins, warm light, number {i}", "license": "own-work", "owner": "user", "source": "own sketchbook"}
            items.append(it)
        for patch in inp.get("patches", []):
            idx = patch["index"]
            if "delete_keys" in patch:
                for k in patch["delete_keys"]:
                    items[idx].pop(k, None)
            items[idx].update(patch.get("set", {}))
        if inp.get("copy_item0_to"):
            (root / inp["copy_item0_to"]).write_bytes((root / "img/000.png").read_bytes())
            items.append({**items[0], "file": inp["copy_item0_to"]})
        meta = {"id": "test_ds", "trigger_word": inp.get("trigger", "cptstyle")}
        meta.update(inp.get("meta", {}))
        (root / "dataset.yaml").write_text(yaml.safe_dump({"dataset": meta, "items": items}), encoding="utf-8")
        pol = lora.load_policy(project.root / "style" / "lora_policy.yaml")
        if inp.get("action") == "config":
            try:
                cfg = lora.make_config(root, base_model=inp.get("base_model", "my-base"), policy=pol, **inp.get("params", {}))
            except ValueError as exc:
                return {"error": str(exc)}
            plan = lora.validation_plan(cfg)
            return {"name": cfg["output"]["name"], "rank": cfg["adapter"]["rank"], "alpha": cfg["adapter"]["alpha"], "unverified": "has not trained" in cfg["unverified"],
                    "command_is_template": "<your-trainer>" in cfg["command_template"], "plan_images": plan["expected_images"], "held_out_all": all(p["held_out"] for p in plan["prompts"]),
                    "has_base_arm": any(a["id"] == "base" for a in plan["arms"]), "licenses": cfg["provenance"]["licenses"]}
        rep = lora.validate_dataset(root, pol)
        return {"ok": rep["ok"], "errors": rep["errors"], "codes": sorted({f["code"] for f in rep["findings"]}), "error_codes": sorted({f["code"] for f in rep["findings"] if f["severity"] == "error"}),
                "items": rep["items"], "ai_generated": rep["ai_generated"]}


def _generate_and_rate(project: Project, inp: dict) -> dict:
    """The full tool path on a temp workspace: dry run, generate (placeholder), compare, evaluate, rate, promote."""
    with _temp_workspace() as ws:
        tb = all_tools(project, HOOKS)
        brief = inp["brief"]
        out: dict = {}
        dry = call_local(tb, "generate_concepts", {"brief": brief, "n_directions": inp.get("n", 3), "adapter": "placeholder"})
        out["dry_run_writes_nothing"] = dry["dry_run"] and not (ws / "generations").exists()
        out["dry_prompts"] = len(dry["prompts"])
        try:
            call_local(tb, "generate_concepts", {"brief": brief, "adapter": "dryrun", "dry_run": False})
        except ValueError as exc:
            out["dryrun_refused"] = "writes nothing" in str(exc)
        res = call_local(tb, "generate_concepts", {"brief": brief, "n_directions": inp.get("n", 3), "adapter": "placeholder", "dry_run": False, "model": "test-model-x"})
        gen_id = res["gen_id"]
        rec = call_local(tb, "get_generation", {"gen_id": gen_id})
        out["record_fields"] = sorted(k for k in ("brief", "adapter", "directions", "requests", "outputs", "reference_ids", "feedback") if k in rec)
        out["declared_model"] = rec["adapter"]["model"]
        out["model_verified"] = rec["adapter"]["model_verified"]
        out["outputs"] = len(rec["outputs"])
        out["status"] = rec["status"]
        out["hashes_ok"] = all(adapters.sha256_file(o["path"]) == o["sha256"] for o in rec["outputs"])
        out["prompts_recorded"] = all(r["prompt"] and r["negative"] is not None for r in rec["requests"])
        div = call_local(tb, "compare_outputs", {"gen_id": gen_id})
        out["pairs"] = len(div["pairs"])
        first = rec["outputs"][0]["output_id"]
        ev = call_local(tb, "evaluate_concept", {"gen_id": gen_id, "output_id": first})
        out["unscored_has_manual"] = {"brief_adherence", "usability_as_reference"} <= set(ev["rubric"]["unscored"])
        out["not_passed_without_judgment"] = not ev["rubric"]["passed"]
        try:
            call_local(tb, "rate_concept", {"gen_id": gen_id, "output_id": first, "decision": "revise", "reason": "", "corrections": []})
        except ValueError as exc:
            out["revise_needs_reason"] = "reason or at least one correction" in str(exc)
        try:
            call_local(tb, "rate_concept", {"gen_id": gen_id, "output_id": first, "decision": "revise", "corrections": [{"dimension": "vibes", "note": "x"}], "dry_run": False})
        except ValueError as exc:
            out["bad_dimension_rejected"] = "unknown correction dimension" in str(exc)
        dry_rate = call_local(tb, "rate_concept", {"gen_id": gen_id, "output_id": first, "decision": "accept"})
        out["rate_dry_run"] = dry_rate["dry_run"] and not call_local(tb, "get_generation", {"gen_id": gen_id})["feedback"]
        call_local(tb, "rate_concept", {"gen_id": gen_id, "output_id": first, "decision": "revise", "reason": "too dark", "corrections": [{"dimension": "lighting", "direction": "more", "note": "brighter"}], "dry_run": False})
        after = call_local(tb, "get_generation", {"gen_id": gen_id})
        out["feedback_saved"] = len(after["feedback"]) == 1 and after["feedback"][0]["subjective"] is True and after["core_run_id"] is not None
        try:
            call_local(tb, "promote_concept", {"gen_id": gen_id, "output_id": first, "title": "t", "description": "d", "tags": ["x"], "dry_run": False})
        except ValueError as exc:
            out["placeholder_not_promoted"] = "synthetic fixtures" in str(exc)
        out["listed"] = len(call_local(tb, "list_generations", {})["runs"])
        return out


def eval_checks(project: Project) -> dict:
    from .core.evals import dig

    def error_contains(result, task, text):
        err = result.get("error") or ""
        return text.lower() in err.lower(), f"error should mention '{text}' (got {err[:200]!r})"

    def approx(result, task, path, value, tol=0.01):
        got = dig(result, path)
        return isinstance(got, (int, float)) and abs(got - value) <= tol, f"{path}={got!r}; expected {value} +/- {tol}"

    def at_least(result, task, path, value):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got >= value, f"{path}={got!r}; expected >= {value}"

    def at_most(result, task, path, value):
        got = dig(result, path)
        return isinstance(got, (int, float)) and got <= value, f"{path}={got!r}; expected <= {value}"

    def list_contains(result, task, path, items):
        got = dig(result, path) or []
        return set(items) <= set(got), f"{path}={got}; expected to contain {items}"

    def list_excludes(result, task, path, items):
        got = dig(result, path) or []
        return not (set(items) & set(got)), f"{path}={got}; must not contain {items}"

    def text_has(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() in got.lower(), f"{path} should contain {text!r}"

    def text_lacks(result, task, path, text):
        got = str(dig(result, path) or "")
        return text.lower() not in got.lower(), f"{path} should not contain {text!r}"

    def is_none(result, task, path):
        got = dig(result, path, "MISSING")
        return got is None, f"{path}={got!r}; expected null (unknown)"

    return {"error_contains": error_contains, "approx": approx, "at_least": at_least, "at_most": at_most, "list_contains": list_contains, "list_excludes": list_excludes,
            "text_has": text_has, "text_lacks": text_lacks, "is_none": is_none}


def register_cli(sub, project: Project) -> None:
    p = sub.add_parser("directions", help="propose directions for a brief file (JSON/YAML); prints them")
    p.add_argument("brief")
    p.add_argument("-n", type=int, default=4)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(handler=_directions)

    p = sub.add_parser("prompt", help="print the prompt for direction INDEX of a brief")
    p.add_argument("brief")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(handler=_prompt)

    p = sub.add_parser("analyze", help="pixel measurements for an image")
    p.add_argument("image")
    p.add_argument("--kind", default="environment", choices=D.KINDS)
    p.add_argument("--palette", nargs="*", default=[])
    p.set_defaults(handler=_analyze)

    p = sub.add_parser("lora-validate", help="validate a LoRA dataset folder (never trains)")
    p.add_argument("dataset")
    p.set_defaults(handler=_lora_validate)

    p = sub.add_parser("adapters", help="list image adapters and whether each is usable")
    p.set_defaults(handler=lambda a, pr: emit({"adapters": [x.describe() for x in adapters.registry().values()]}) or 0)


def _brief(path: str) -> dict:
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _directions(a, pr) -> int:
    res = D.generate_directions(_brief(a.brief), a.n, a.seed)
    emit({"min_axes_differing": res["min_axes_differing"], "warnings": res["warnings"], "directions": [{"id": d["id"], "axes": d["axes"], "palette": [p["hex"] for p in d["palette"]]} for d in res["directions"]]})
    return 0


def _prompt(a, pr) -> int:
    b = _brief(a.brief)
    d = D.generate_directions(b, a.index + 1, a.seed)["directions"][a.index]
    emit(prompts.build_prompt(b, d, load_style(pr)))
    return 0


def _analyze(a, pr) -> int:
    emit(call_local(all_tools(pr, HOOKS), "analyze_image", {"path": a.image, "kind": a.kind, "palette": a.palette or None}))
    return 0


def _lora_validate(a, pr) -> int:
    res = call_local(all_tools(pr, HOOKS), "lora_validate_dataset", {"dataset_dir": a.dataset})
    emit(res)
    return 0 if res["ok"] else 1


HOOKS = DomainHooks(
    tools=tools_mod.make_tools,
    instructions=INSTRUCTIONS,
    ingest_extensions=EXTENSIONS,
    eval_solver=eval_solver,
    eval_checks=eval_checks,
    doctor_checks=doctor_checks,
    register_cli=register_cli,
    correction_dimensions=correction_dimensions,
)
