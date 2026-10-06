"""MCP tools for the concept-art assistant.

Everything is a *concept image* workflow: directions -> prompts -> an adapter you choose -> measurements -> your verdict. Writing tools default to
``dry_run=true``. A generated image is never a production-ready mesh, Substance graph or Roblox scene, and every result says so.
The server never uploads anything and never calls a hosted provider by itself: the only generator that can run is the one YOU configure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .core import feedback as fb
from .core import retrieval
from .core.commontools import default_embedder
from .core.manifest import LibraryStore
from .core.mcpkit import ToolSpec
from .core.project import Project
from .core.rubric import load_rubric, score_rubric
from .core.safety import Plan, resolve_inside, safe_name
from .core.style import check_ranges, load_style
from .domain import adapters, analysis, direction as D, lora, prompts, runs

CONCEPT_ONLY = {"status": "concept_only", "note": "Concept art only: not a mesh, a Substance graph or a Roblox scene."}


def rubric_path(project: Project, kind: str) -> Path:
    return project.root / "evals" / f"rubric_{kind}.yaml"


def make_tools(project: Project) -> list[ToolSpec]:
    store = LibraryStore(project)

    def need_images() -> None:
        if not analysis.available():
            raise ValueError("image analysis needs numpy and Pillow: pip install -e \".[imaging]\"")

    def image_path(p: str) -> Path:
        roots = [*project.allowed_roots, project.root.resolve()] + ([project.asset_root.resolve()] if project.asset_root else [])
        path = resolve_inside(p, roots)
        if not path.exists() or path.suffix.lower() not in adapters.ALLOWED_OUTPUT:
            raise ValueError(f"'{p}' is not an existing image ({sorted(adapters.ALLOWED_OUTPUT)})")
        return path

    def library_images(kind: str | None = None) -> list[dict]:
        out = []
        for a in store.load():
            if a["status"] != "curated" or a.get("polarity", "positive") != "positive" or a["kind"] not in ("concept", "reference_image"):
                continue
            if kind and a.get("domain", {}).get("subject_kind") not in (None, kind):
                continue
            p = store.resolve_path(a["path"])
            if p and p.exists() and p.suffix.lower() in adapters.ALLOWED_OUTPUT:
                out.append({"id": a["id"], "path": p})
        return out

    def ranges() -> dict:
        return load_style(project).get("ranges", {})

    def measure_dict(path: Path, palette_target: list[str] | None, focal_box: list[float] | None) -> dict:
        need_images()
        return analysis.measure(path, target_palette=palette_target or (), focal_box=focal_box)

    def flags(m: dict, kind: str, novelty: float | None = None) -> list[dict]:
        flat = {k: v for k, v in m.items() if isinstance(v, (int, float))}
        flat["silhouette_components"] = m["silhouette_components"]
        flat["silhouette_fill"] = m["silhouette_fill"]
        if novelty is not None:
            flat["novelty"] = novelty
        r = {k: v for k, v in ranges().items() if kind == "prop" or not k.startswith("silhouette")}
        return [f for f in check_ranges(flat, r) if f["severity"] != "info" or f["metric"] not in ("focal_contrast", "novelty", "palette_adherence")]

    # --- read-only ------------------------------------------------------------------------------------------------------------------
    def search_concept_library(query: str = "", subject_kind: str | None = None, tags: list[str] | None = None, palette: list[str] | None = None, k: int = 5) -> dict[str, Any]:
        """Search curated concept images, reference images and directions (and 'avoid' examples). Optionally filter by subject_kind ('environment' or 'prop') and palette."""
        res = retrieval.search(store, query, filters={"domain.subject_kind": subject_kind} if subject_kind else None, tags=tags or (), palette=palette or (),
                               k=max(1, min(k, 20)), embedder=default_embedder(project))
        return {**res, **CONCEPT_ONLY}

    def analyze_image(path: str, kind: str = "environment", palette: list[str] | None = None, focal_box: list[float] | None = None) -> dict[str, Any]:
        """Pixel measurements for one image: value range and structure, edges, palette, saturation, silhouette stats, edge centroid; palette adherence when a palette is given,
        focal contrast when a focal_box [x0,y0,x1,y1] (0-1) is given. Measurements, not quality judgments."""
        if kind not in D.KINDS:
            raise ValueError(f"kind must be one of {D.KINDS}")
        p = image_path(path)
        m = measure_dict(p, palette, focal_box)
        return {"path": str(p), "metrics": m, "readability": analysis.readability(m, ranges()), "degenerate": analysis.degenerate(m), "flags": flags(m, kind), **CONCEPT_ONLY}

    def extract_reference_traits(paths: list[str], max_colors: int = 6) -> dict[str, Any]:
        """From 1-12 reference images (yours or licensed), extract a merged dominant palette, value range, saturation and edge density, and suggest a brief fragment
        (locked palette + trait hints). Describes pixels only; it does not understand subject matter or style names."""
        need_images()
        if not 1 <= len(paths) <= 12:
            raise ValueError("pass between 1 and 12 image paths")
        from .core.retrieval import color_distance

        per, merged = [], []
        for pth in paths:
            p = image_path(pth)
            m = analysis.measure(p)
            per.append({"path": str(p), "value_range": round(m["value_range"], 3), "saturation": round(m["saturation"], 3), "edge_density": round(m["edge_density"], 3), "palette": m["palette"]})
            merged += [(c["hex"], c["share"] / len(paths)) for c in m["palette"]]
        merged.sort(key=lambda t: -t[1])
        picked: list[tuple[str, float]] = []
        for hx, share in merged:
            for i, (ph, ps) in enumerate(picked):
                if color_distance(hx, ph) < 12:
                    picked[i] = (ph, ps + share)
                    break
            else:
                picked.append((hx, share))
        picked.sort(key=lambda t: -t[1])
        palette = [h for h, _ in picked[:max_colors]]
        avg = lambda key: sum(r[key] for r in per) / len(per)
        hints = []
        hints.append("high value contrast" if avg("value_range") >= 0.5 else "soft value contrast" if avg("value_range") < 0.3 else "moderate value contrast")
        hints.append("saturated colour" if avg("saturation") >= 0.45 else "muted colour" if avg("saturation") < 0.2 else "moderate saturation")
        hints.append("busy, detailed edges" if avg("edge_density") >= 0.2 else "calm, simple shapes" if avg("edge_density") < 0.05 else "moderate detail")
        fragment = {"notes": "reference traits: " + ", ".join(hints)}
        if len(palette) >= 3:
            fragment["locked"] = {"palette": palette[:5]}
        return {"images": per, "palette": palette, "trait_hints": hints, "suggested_brief_fragment": fragment,
                "palette_note": None if len(palette) >= 3 else "fewer than 3 distinct colours found: not suggesting a locked palette. Add more references or choose colours by hand.",
                "caution": "Derived from pixels only. Use references you own or are licensed to use; do not copy them.", **CONCEPT_ONLY}

    def propose_directions(brief: dict, n: int = 4, seed: int = 0, min_axes_different: int = 3) -> dict[str, Any]:
        """Meaningfully different visual directions for a brief {subject, kind: environment|prop, locked: {axes, palette}, avoid, notes}. Chosen by farthest-point sampling over
        explicit axes, so they differ on several axes at once. Locked axes stay fixed. Deterministic per seed."""
        return {**D.generate_directions(brief, n, seed, min_axes_different), **CONCEPT_ONLY}

    def build_prompt(brief: dict, direction: dict) -> dict[str, Any]:
        """Prompt + negative prompt for one direction. Environment and prop briefs use different structures. Refuses prompts that claim production readiness."""
        return prompts.build_prompt(brief, direction, load_style(project))

    def list_image_adapters() -> dict[str, Any]:
        """Image-generation adapters and whether each is usable here. dryrun writes nothing; placeholder draws labelled synthetic fixtures; command runs the program you configure."""
        return {"adapters": [a.describe() for a in adapters.registry().values()],
                "configure_command": "export CONCEPTAI_IMAGE_COMMAND='[\"python\",\"my_generate.py\",\"--prompt-file\",\"{prompt_file}\",\"--out\",\"{out}\",\"--seed\",\"{seed}\",\"--width\",\"{width}\",\"--height\",\"{height}\"]'",
                "note": "No hosted provider is built in. Wrap yours in a small script and point the command adapter at it."}

    def check_novelty(path: str, subject_kind: str | None = None) -> dict[str, Any]:
        """How different an image is from the curated library (1 - similarity to the nearest entry). Unknown (null) when the library has no images."""
        need_images()
        return {**analysis.novelty_vs_library(image_path(path), library_images(subject_kind)), "min_novelty": ranges().get("novelty", {}).get("min")}

    def check_composition_lock(path: str, focal_box: list[float], min_focal_contrast: float = 0.08, centroid_tolerance: float = 0.3) -> dict[str, Any]:
        """For a brief that locks composition: the focal box [x0,y0,x1,y1] (0-1) must stand out from its surround and the image's edge energy must centre near it."""
        need_images()
        lock = {"focal_box": focal_box, "min_focal_contrast": min_focal_contrast, "centroid_tolerance": centroid_tolerance}
        m = analysis.measure(image_path(path), focal_box=focal_box)
        findings = analysis.check_composition_lock(m, lock)
        return {"ok": not any(f["severity"] == "error" for f in findings), "findings": findings, "focal_contrast": m["focal_contrast"], "edge_centroid": m["edge_centroid"], "lock": lock}

    def compare_outputs(gen_id: str) -> dict[str, Any]:
        """How different the outputs of one generation run are from each other (1 - similarity), to catch 'different' directions that look alike."""
        need_images()
        rec = runs.load(project, gen_id)
        files = [o["path"] for o in rec["outputs"]]
        res = analysis.diversity(files)
        ids = [o["output_id"] for o in rec["outputs"]]
        res["pairs"] = [{"a": ids[p["a"]], "b": ids[p["b"]], "dissimilarity": p["dissimilarity"]} for p in res["pairs"]]
        weak = [p for p in res["pairs"] if p["dissimilarity"] < 0.15]
        return {**res, "too_similar": weak, "outputs": len(files)}

    def evaluate_concept(gen_id: str | None = None, output_id: str | None = None, path: str | None = None, brief: dict | None = None, direction: dict | None = None,
                         manual_scores: dict | None = None) -> dict[str, Any]:
        """Measure one concept and score it with the environment or prop rubric. Pass gen_id+output_id (a recorded run) or path+brief(+direction). Automatic criteria come
        from pixels; manual criteria (brief adherence, usability, hybrids' human half) stay unscored until manual_scores supplies them, so a partial score is never final."""
        need_images()
        if gen_id:
            rec = runs.load(project, gen_id)
            outs = {o["output_id"]: o for o in rec["outputs"]}
            if output_id not in outs:
                raise ValueError(f"pass output_id, one of {sorted(outs)}")
            out = outs[output_id]
            brief_, dirs = rec["brief"], {d["id"]: d for d in rec["directions"]}
            direction_ = dirs.get(out.get("direction_id"))
            p = Path(out["path"])
        else:
            if not (path and brief):
                raise ValueError("pass gen_id+output_id, or path+brief")
            p, brief_, direction_ = image_path(path), D.normalize_brief(brief), direction
        brief_ = D.normalize_brief(brief_)
        kind = brief_["kind"]
        target = [c["hex"] for c in direction_["palette"]] if direction_ else brief_["locked"].get("palette") or []
        lock = brief_["locked"].get("composition") or {}
        m = analysis.measure(p, target_palette=target, focal_box=lock.get("focal_box"))
        rd = analysis.readability(m, ranges())
        deg = analysis.degenerate(m)
        lib = analysis.novelty_vs_library(p, [e for e in library_images(kind) if Path(e["path"]).resolve() != p.resolve()])
        findings = analysis.check_composition_lock(m, lock) if lock else []
        nov_target = 0.3
        auto: dict[str, float | None] = {
            "not_degenerate": 0.0 if deg else 1.0,
            "locks_respected": (0.0 if any(f["severity"] == "error" for f in findings) else 1.0),
            "style_cohesion": m.get("palette_adherence"),
            "readability": rd["score"],
            "novelty_vs_library": None if lib["novelty"] is None else min(lib["novelty"] / nov_target, 1.0),
        }
        if kind == "prop":
            comps_ok = 1.0 if m["silhouette_components"] <= ranges().get("silhouette_components", {}).get("max", 4) else 0.5
            fill_lo, fill_hi = ranges().get("silhouette_fill", {}).get("min", 0.08), ranges().get("silhouette_fill", {}).get("max", 0.8)
            auto["silhouette_clear"] = comps_ok * (1.0 if fill_lo <= m["silhouette_fill"] <= fill_hi else 0.5)
        rubric = load_rubric(rubric_path(project, kind))
        scored = score_rubric(rubric, auto, manual_scores)
        return {"path": str(p), "kind": kind, "metrics": m, "readability": rd, "degenerate": deg, "novelty": lib, "composition_findings": findings,
                "flags": flags(m, kind, lib["novelty"]), "rubric": scored, "lock_applies": bool(lock),
                "judged_by_a_person": [r["id"] for r in scored["criteria"] if r["method"] != "auto"], **CONCEPT_ONLY}

    def get_generation(gen_id: str) -> dict[str, Any]:
        """The full record of one generation run: brief, adapter and declared model, prompts, settings, seeds, reference ids, outputs with hashes, and feedback."""
        return runs.load(project, gen_id)

    def list_generations(limit: int = 20) -> dict[str, Any]:
        """Recent generation runs (newest first) with subject, adapter, output and feedback counts."""
        return {"runs": runs.list_runs(project, max(1, min(limit, 100)))}

    def lora_validate_dataset(dataset_dir: str, check_near_duplicates: bool = True) -> dict[str, Any]:
        """Validate a LoRA/PEFT dataset folder (dataset.yaml + images): licences, owners, captions, trigger word, resolution, duplicates, AI-generated share. Read-only; copies and uploads nothing."""
        pol = lora.load_policy(project.root / "style" / "lora_policy.yaml")
        return lora.validate_dataset(image_dir(dataset_dir), pol, check_near_duplicates=check_near_duplicates)

    def lora_check_overfit(dataset_dir: str, output_paths: list[str], threshold: float = 0.95) -> dict[str, Any]:
        """Flag generated images that look nearly identical to a training image (possible memorising). Layout + palette similarity; a heuristic."""
        need_images()
        return lora.check_overfit([str(image_path(p)) for p in output_paths], image_dir(dataset_dir), threshold)

    def image_dir(p: str) -> Path:
        roots = [*project.allowed_roots, project.root.resolve()] + ([project.asset_root.resolve()] if project.asset_root else [])
        d = resolve_inside(p, roots)
        if not d.is_dir():
            raise ValueError(f"'{p}' is not a directory")
        return d

    # --- writing (dry_run=true by default) -----------------------------------------------------------------------------------------
    def generate_concepts(brief: dict, directions: list[dict] | None = None, n_directions: int = 3, adapter: str = "dryrun", per_direction: int = 1, seed: int = 0,
                          model: str | None = None, reference_paths: list[str] | None = None, settings: dict | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Generate concept images for several directions with the chosen adapter and record the run (model, prompts, references, settings, seeds, outputs, hashes).
        dry_run=true shows the prompts and what would be sent and writes nothing. dry_run=false needs an adapter that writes files (placeholder, or command if you configured it)."""
        b = D.normalize_brief(brief)
        dirs = directions or D.generate_directions(b, n_directions, seed)["directions"]
        if not 1 <= per_direction <= 4:
            raise ValueError("per_direction must be 1-4")
        ad = adapters.get(adapter)
        style = load_style(project)
        plans = [{"direction": d, "prompt": prompts.build_prompt(b, d, style)} for d in dirs]
        refs = [str(image_path(r)) for r in (reference_paths or [])]
        summary = {"subject": b["subject"], "kind": b["kind"], "directions": [d["id"] for d in dirs], "adapter": ad.describe(), "per_direction": per_direction,
                   "images_expected": len(dirs) * per_direction, "reference_images": refs, "reference_ids": b["reference_ids"]}
        steps = Plan(f"Generate {len(dirs) * per_direction} concept image(s) with adapter '{ad.name}'")
        steps.add("record", "workspace/generations/<gen_id>/run.json", "model, prompts, settings, seeds, references, outputs, hashes")
        steps.add("write", "workspace/generations/<gen_id>/", "output images" if ad.writes_files else "(dryrun writes no images)")
        if dry_run:
            return {**steps.to_dict(True), "summary": summary, "prompts": [{"direction_id": p["direction"]["id"], "positive": p["prompt"]["positive"], "negative": p["prompt"]["negative"],
                                                                         "width": p["prompt"]["width"], "height": p["prompt"]["height"]} for p in plans], **CONCEPT_ONLY}
        if not ad.writes_files:
            raise ValueError("the dryrun adapter writes nothing; choose 'placeholder' or a configured 'command' adapter, or keep dry_run=true")
        ok, why = ad.available()
        if not ok:
            raise ValueError(f"adapter '{ad.name}' is not available: {why}")
        gen_id = runs.new_id()
        out_dir = resolve_inside(runs.run_dir(project, gen_id), project.allowed_roots)
        out_dir.mkdir(parents=True, exist_ok=True)
        outputs, requests = [], []
        for i, pl in enumerate(plans):
            d, pr = pl["direction"], pl["prompt"]
            req = {"prompt": pr["positive"], "negative": pr["negative"], "width": pr["width"], "height": pr["height"], "seed": seed + 1000 * i, "count": per_direction,
                   "reference_images": refs, "stem": d["id"], "settings": {**(settings or {}), "direction": d}}
            files = ad.generate(req, out_dir)
            requests.append({**{k: v for k, v in req.items() if k != "settings"}, "settings": {k: v for k, v in req["settings"].items() if k != "direction"}, "direction_id": d["id"]})
            for j, f in enumerate(files):
                outputs.append({"output_id": f"{d['id']}_{j:02d}", "direction_id": d["id"], **f})
        model_name = model or ad.model
        rec = runs.make_record(gen_id, brief=b, adapter={**ad.describe(), "model": model_name, "model_verified": False}, directions=dirs, requests=requests, outputs=outputs,
                               reference_ids=b["reference_ids"], core_run_id=None)
        runs.save(project, rec)
        return {**steps.to_dict(False), "gen_id": gen_id, "summary": summary, "outputs": [{k: o[k] for k in ("output_id", "direction_id", "path", "seed", "sha256")} for o in outputs],
                "next": "evaluate_concept, compare_outputs, then rate_concept with the user's verdict",
                "warning": "placeholder images are synthetic fixtures, not generated art" if ad.name == "placeholder" else None, **CONCEPT_ONLY}

    def rate_concept(gen_id: str, output_id: str, decision: str, reason: str = "", scores: dict | None = None, corrections: list[dict] | None = None, rating: int | None = None,
                     dry_run: bool = True) -> dict[str, Any]:
        """Store the USER's verdict on one output (accept/reject/revise) with structured corrections [{dimension, direction, note}] and optional subjective scores.
        Only call with the user's actual words. dry_run=false writes it to the generation record and to the feedback log used by find_past_corrections."""
        rec = runs.load(project, gen_id)
        dims = load_style(project).get("correction_dimensions", [])
        for c in corrections or []:
            if dims and c.get("dimension") not in dims:
                raise ValueError(f"unknown correction dimension '{c.get('dimension')}'. Allowed: {dims}")
        if decision != "accept" and not (reason or corrections):
            raise ValueError("a reject/revise decision needs a reason or at least one correction")
        plan = {"gen_id": gen_id, "output_id": output_id, "decision": decision, "reason": reason, "corrections": corrections or [], "scores": scores or {}}
        if dry_run:
            return {"dry_run": True, "would_record": plan, "note": "nothing saved; call again with dry_run=false once the user has given their verdict"}
        runs.add_feedback(rec, output_id=output_id, decision=decision, scores=scores, notes=reason, corrections=corrections or [])
        if not rec.get("core_run_id"):
            out = next(o for o in rec["outputs"] if o["output_id"] == output_id)
            rec["core_run_id"] = fb.record_run(project, request=f"{rec['brief']['kind']} concept: {rec['brief']['subject']}", constraints={"locked": rec["brief"]["locked"]},
                                               retrieved=rec["reference_ids"], tools=["generate_concepts"], outputs=[o["path"] for o in rec["outputs"]], preview=out["path"],
                                               model=rec["adapter"].get("model"), extra={"gen_id": gen_id})
        row = fb.record_decision(project, rec["core_run_id"], decision, reason=reason, corrections=corrections, rating=rating, allowed_dimensions=dims)
        runs.save(project, rec)
        return {"dry_run": False, "recorded": row, "core_run_id": rec["core_run_id"], "gen_id": gen_id}

    def promote_concept(gen_id: str, output_id: str, title: str, description: str, tags: list[str], polarity: str = "positive", confirm: bool = False,
                        dry_run: bool = True) -> dict[str, Any]:
        """Curation step: add one generated image to the library. Without confirm=true it only creates an unreviewed CANDIDATE (ignored by default searches). confirm=true needs an
        'accept' decision from the user first. Entries are flagged ai_generated and concept_only so AI outputs never masquerade as human-made references."""
        rec = runs.load(project, gen_id)
        out = next((o for o in rec["outputs"] if o["output_id"] == output_id), None)
        if out is None:
            raise ValueError(f"unknown output '{output_id}'")
        if not rec.get("core_run_id"):
            raise ValueError("this output has no recorded feedback yet; call rate_concept first")
        domain = {"subject_kind": rec["brief"]["kind"], "concept_only": True, "ai_generated": True, "gen_id": gen_id, "output_id": output_id,
                  "axes": next((d["axes"] for d in rec["directions"] if d["id"] == out["direction_id"]), {}), "seed": out["seed"], "adapter": rec["adapter"]["name"],
                  "model": str(rec["adapter"].get("model", "")), "prompt": next((r["prompt"] for r in rec["requests"] if r["direction_id"] == out["direction_id"]), "")[:1200]}
        if rec["adapter"]["name"] == "placeholder":
            domain["synthetic_fixture"] = True
            raise ValueError("placeholder images are synthetic fixtures and are not promoted to the library")
        if dry_run:
            return {"dry_run": True, "would_create": {"status": "curated" if confirm else "candidate", "polarity": polarity, "domain": domain, "path": out["path"]}}
        palette = next(([p["hex"] for p in d["palette"]] for d in rec["directions"] if d["id"] == out["direction_id"]), [])
        res = fb.promote_run(project, rec["core_run_id"], kind="concept", title=title, description=description, tags=tags, path=out["path"], owner="user", polarity=polarity,
                             confirm=confirm, style={"palette": palette}, domain=domain, store=store)
        return {"dry_run": False, "asset": {k: res[k] for k in ("id", "status", "polarity", "kind")}, **CONCEPT_ONLY}

    def lora_make_config(dataset_dir: str, base_model: str, rank: int = 16, learning_rate: float = 1e-4, steps: int = 1500, batch_size: int = 1, resolution: int = 1024,
                         seed: int = 1234, output_name: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        """Write a trainer-agnostic LoRA config and a validation plan for a dataset that passes lora_validate_dataset. NEVER trains: it prepares files and prints a command template.
        dry_run=false writes workspace/lora/<name>/config.yaml and validation_plan.json."""
        pol = lora.load_policy(project.root / "style" / "lora_policy.yaml")
        cfg = lora.make_config(image_dir(dataset_dir), base_model=base_model, policy=pol, rank=rank, learning_rate=learning_rate, steps=steps, batch_size=batch_size,
                               resolution=resolution, seed=seed, output_name=output_name)
        plan = lora.validation_plan(cfg)
        steps_ = Plan(f"Prepare LoRA training files for dataset '{cfg['dataset']['id']}' (nothing is trained)")
        steps_.add("write", f"workspace/lora/{cfg['output']['name']}/config.yaml", "training config")
        steps_.add("write", f"workspace/lora/{cfg['output']['name']}/validation_plan.json", f"{plan['expected_images']} validation images to generate with your adapter")
        if dry_run:
            return {**steps_.to_dict(True), "config": cfg, "validation_plan": {k: plan[k] for k in ("arms", "seeds", "expected_images")}}
        d = resolve_inside(project.workspace / "lora" / safe_name(cfg["output"]["name"]), project.allowed_roots)
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        (d / "validation_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return {**steps_.to_dict(False), "config_path": str(d / "config.yaml"), "validation_plan_path": str(d / "validation_plan.json"), "command_template": cfg["command_template"]}

    ro = dict(read_only=True)
    wr = dict(read_only=False, idempotent=False)
    return [
        ToolSpec("search_concept_library", search_concept_library, search_concept_library.__doc__, **ro),
        ToolSpec("analyze_image", analyze_image, analyze_image.__doc__, **ro),
        ToolSpec("extract_reference_traits", extract_reference_traits, extract_reference_traits.__doc__, **ro),
        ToolSpec("propose_directions", propose_directions, propose_directions.__doc__, **ro),
        ToolSpec("build_prompt", build_prompt, build_prompt.__doc__, **ro),
        ToolSpec("list_image_adapters", list_image_adapters, list_image_adapters.__doc__, **ro),
        ToolSpec("check_novelty", check_novelty, check_novelty.__doc__, **ro),
        ToolSpec("check_composition_lock", check_composition_lock, check_composition_lock.__doc__, **ro),
        ToolSpec("compare_outputs", compare_outputs, compare_outputs.__doc__, **ro),
        ToolSpec("evaluate_concept", evaluate_concept, evaluate_concept.__doc__, **ro),
        ToolSpec("get_generation", get_generation, get_generation.__doc__, **ro),
        ToolSpec("list_generations", list_generations, list_generations.__doc__, **ro),
        ToolSpec("lora_validate_dataset", lora_validate_dataset, lora_validate_dataset.__doc__, **ro),
        ToolSpec("lora_check_overfit", lora_check_overfit, lora_check_overfit.__doc__, **ro),
        ToolSpec("generate_concepts", generate_concepts, generate_concepts.__doc__, expensive=True, **wr),
        ToolSpec("rate_concept", rate_concept, rate_concept.__doc__, **wr),
        ToolSpec("promote_concept", promote_concept, promote_concept.__doc__, **wr),
        ToolSpec("lora_make_config", lora_make_config, lora_make_config.__doc__, read_only=False, idempotent=True),
    ]
