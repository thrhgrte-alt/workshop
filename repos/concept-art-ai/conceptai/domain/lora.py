"""Optional, separate LoRA / PEFT workflow: validate a dataset, write a training config, plan validation samples.

This module NEVER trains anything and never starts a process. It prepares and checks. Training itself is done by a trainer you choose and run
yourself; the config produced here is trainer-agnostic, its numbers are generic starting points (unverified for your model), and the command
shown is a template with placeholders.

Why a gate: a LoRA learns whatever is in its dataset, including other people's work and earlier AI outputs. So the dataset must state a licence
and owner for every image, near-duplicates are flagged, and AI-generated images are capped and must have been curated by a person.
Nothing is copied or uploaded: the dataset stays where it is.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from . import analysis

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_POLICY = {
    "allowed_licenses": ["own-work", "licensed-for-training", "cc0", "cc-by-4.0"],
    "needs_attribution": ["cc-by-4.0"],
    "min_items": 15, "max_items": 400, "min_side": 512,
    "caption_chars": [8, 400], "max_ai_generated_share": 0.2, "near_duplicate_similarity": 0.97,
}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,40}$")
TRIGGER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,30}$")


def finding(severity: str, code: str, message: str, where: str = "") -> dict:
    return {"severity": severity, "code": code, "message": message, "where": where}


def load_policy(path: Path | None) -> dict:
    pol = dict(DEFAULT_POLICY)
    if path and Path(path).exists():
        pol.update(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    return pol


def load_dataset(directory: Path) -> dict:
    f = Path(directory) / "dataset.yaml"
    if not f.exists():
        raise ValueError(f"{directory}: needs a dataset.yaml (see references/lora-dataset.md)")
    data = yaml.safe_load(f.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("dataset.yaml needs an 'items' list")
    return data


def validate_dataset(directory: Path, policy: dict | None = None, *, check_near_duplicates: bool = True) -> dict:
    pol = {**DEFAULT_POLICY, **(policy or {})}
    root = Path(directory).resolve()
    data = load_dataset(root)
    meta = data.get("dataset", {})
    f: list[dict] = []
    if not ID_RE.match(str(meta.get("id", ""))):
        f.append(finding("error", "bad_dataset_id", "dataset.id must match [A-Za-z][A-Za-z0-9_-]*"))
    trigger = meta.get("trigger_word")
    if trigger is not None and not TRIGGER_RE.match(str(trigger)):
        f.append(finding("error", "bad_trigger", "trigger_word must be 3-31 letters/digits/underscores, starting with a letter"))
    items = data["items"]
    if len(items) < pol["min_items"]:
        f.append(finding("error", "too_few_items", f"{len(items)} items; at least {pol['min_items']} are needed for a style LoRA"))
    if len(items) > pol["max_items"]:
        f.append(finding("warning", "many_items", f"{len(items)} items; more than {pol['max_items']} is rarely needed and slows curation"))
    hashes: dict[str, str] = {}
    usable: list[tuple[str, Path]] = []
    ai = 0
    for it in items:
        name = str(it.get("file", ""))
        where = name or "?"
        p = (root / name).resolve() if name else None
        if p is None or root not in p.parents:
            f.append(finding("error", "bad_path", "file must be a path inside the dataset folder", where))
            continue
        if p.suffix.lower() not in IMAGE_EXT:
            f.append(finding("error", "bad_extension", f"{p.suffix or 'no extension'} is not one of {sorted(IMAGE_EXT)}", where))
            continue
        if not p.exists():
            f.append(finding("error", "missing_file", "file does not exist", where))
            continue
        lic = str(it.get("license", "")).lower()
        if lic not in pol["allowed_licenses"]:
            f.append(finding("error", "license_not_allowed", f"license '{it.get('license')}' is not in the allowed list {pol['allowed_licenses']}; "
                             "unknown or all-rights-reserved images must not be trained on", where))
        if lic in pol["needs_attribution"] and not it.get("source"):
            f.append(finding("error", "attribution_missing", f"license '{lic}' requires a 'source' with attribution", where))
        if not it.get("owner"):
            f.append(finding("error", "owner_missing", "every item needs an 'owner' (who holds the rights)", where))
        cap = str(it.get("caption", "")).strip()
        lo, hi = pol["caption_chars"]
        if not cap:
            f.append(finding("error", "caption_missing", "caption is empty", where))
        elif not lo <= len(cap) <= hi:
            f.append(finding("warning", "caption_length", f"caption has {len(cap)} characters; expected {lo}-{hi}", where))
        elif trigger and trigger.lower() not in cap.lower():
            f.append(finding("warning", "trigger_missing", f"caption does not contain the trigger word '{trigger}'", where))
        if it.get("ai_generated"):
            ai += 1
            if not it.get("curated"):
                f.append(finding("error", "ai_not_curated", "an AI-generated image may only be used if a person curated it (curated: true)", where))
        try:
            from PIL import Image

            with Image.open(p) as im:
                w, h = im.size
        except Exception as exc:  # unreadable image
            f.append(finding("error", "unreadable", f"cannot open image: {exc}", where))
            continue
        if min(w, h) < pol["min_side"]:
            f.append(finding("warning", "low_resolution", f"{w}x{h}; the short side should be at least {pol['min_side']}", where))
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if digest in hashes:
            f.append(finding("error", "duplicate_file", f"identical to {hashes[digest]}", where))
        else:
            hashes[digest] = name
            usable.append((name, p))
    if items and ai / len(items) > pol["max_ai_generated_share"]:
        f.append(finding("error", "too_much_ai", f"{ai}/{len(items)} items are AI-generated (max share {pol['max_ai_generated_share']:.0%}); training on its own outputs makes a style drift and narrow"))
    near = []
    if check_near_duplicates and analysis.available() and 1 < len(usable) <= 300:
        for i in range(len(usable)):
            for j in range(i + 1, len(usable)):
                if analysis.similarity(usable[i][1], usable[j][1]) >= pol["near_duplicate_similarity"]:
                    near.append((usable[i][0], usable[j][0]))
        for a, b in near[:20]:
            f.append(finding("warning", "near_duplicate", f"'{a}' and '{b}' are nearly identical; keep one", a))
    errors = sum(x["severity"] == "error" for x in f)
    return {"ok": errors == 0, "errors": errors, "warnings": sum(x["severity"] == "warning" for x in f), "items": len(items), "usable": len(usable),
            "ai_generated": ai, "findings": f, "dataset": meta.get("id"), "trigger_word": trigger,
            "note": "Checks metadata and files. It cannot judge whether a licence statement is true: that is your responsibility."}


def make_config(directory: Path, *, base_model: str, policy: dict | None = None, rank: int = 16, alpha: int | None = None, learning_rate: float = 1e-4,
                steps: int = 1500, batch_size: int = 1, resolution: int = 1024, seed: int = 1234, output_name: str | None = None) -> dict:
    """Training config for any trainer. Refuses when the dataset has errors."""
    report = validate_dataset(directory, policy)
    if not report["ok"]:
        raise ValueError(f"dataset has {report['errors']} error(s); fix them first (e.g. {report['findings'][0]['message']})")
    if not str(base_model).strip():
        raise ValueError("base_model is required: name or path of the model YOU are allowed to fine-tune (its licence may restrict fine-tuning)")
    if not 1 <= rank <= 256:
        raise ValueError("rank must be 1-256")
    if not 1e-6 <= learning_rate <= 1e-2:
        raise ValueError("learning_rate must be between 1e-6 and 1e-2")
    if not 100 <= steps <= 100000:
        raise ValueError("steps must be 100-100000")
    meta = load_dataset(directory).get("dataset", {})
    name = output_name or f"{meta['id']}_lora"
    if not ID_RE.match(name):
        raise ValueError("output_name must match [A-Za-z][A-Za-z0-9_-]*")
    cfg = {
        "schema": "conceptai-lora-config/1",
        "dataset": {"id": meta["id"], "path": str(Path(directory).resolve()), "items": report["items"], "trigger_word": meta.get("trigger_word"), "caption_dropout": 0.05},
        "base_model": {"id_or_path": base_model, "note": "Check the base model's licence allows fine-tuning and sharing derivatives."},
        "adapter": {"type": "lora", "rank": rank, "alpha": alpha or rank},
        "training": {"learning_rate": learning_rate, "steps": steps, "batch_size": batch_size, "resolution": resolution, "seed": seed, "save_every": max(steps // 5, 100)},
        "output": {"name": name, "dir": f"workspace/lora/{name}"},
        "validation": {"plan_file": f"workspace/lora/{name}/validation_plan.json"},
        "provenance": {"licenses": sorted({str(i.get('license')) for i in load_dataset(directory)['items']}), "ai_generated_items": report["ai_generated"]},
        "unverified": "Hyperparameters are generic starting points chosen without running a trainer. Tune them with your trainer's documentation; this repo has not trained anything.",
        "command_template": "<your-trainer> --config <path-to-this-config>   # placeholder: this repository does not run training",
    }
    return cfg


def validation_plan(cfg: dict, held_out_prompts: list[str] | None = None, seeds: tuple[int, ...] = (11, 22, 33), strengths: tuple[float, ...] = (0.6, 1.0)) -> dict:
    """Fixed prompts and seeds for before/after comparison, including prompts the dataset never described (to check it generalises)."""
    trig = cfg["dataset"].get("trigger_word") or ""
    held = held_out_prompts or ["a market stall at dusk", "a stone bridge over a river", "a wooden barrel with iron bands"]
    prompts = [{"id": f"p{i}", "text": f"{trig} {t}".strip(), "held_out": True} for i, t in enumerate(held)]
    arms = [{"id": "base", "lora": False}] + [{"id": f"lora_{s}", "lora": True, "strength": s} for s in strengths]
    runs = [{"prompt": p["id"], "arm": a["id"], "seed": sd} for p in prompts for a in arms for sd in seeds]
    return {"schema": "conceptai-lora-validation/1", "prompts": prompts, "arms": arms, "seeds": list(seeds), "runs": runs, "expected_images": len(runs),
            "measure": ["palette_adherence vs the dataset palette", "novelty/overfit: similarity to training images (>= 0.95 suggests memorising)", "readability (value range, structure)"],
            "human_review": ["Does the LoRA arm match the style more than base?", "Does it still follow prompts it was not trained on?", "Any recognisable copy of a training image?", "Any unwanted artefact (text, watermark)?"],
            "note": "Image generation for this plan is done by YOUR adapter. Scores are heuristics; the human review decides."}


def check_overfit(output_paths: list[str], dataset_dir: Path, threshold: float = 0.95) -> dict:
    """Flag generated images that look nearly identical to a training image (layout + palette similarity)."""
    data = load_dataset(dataset_dir)
    root = Path(dataset_dir).resolve()
    train = [(i["file"], root / i["file"]) for i in data["items"] if (root / i["file"]).exists()]
    flagged = []
    for out in output_paths:
        best = max(((analysis.similarity(out, p), name) for name, p in train), default=(0.0, None))
        if best[0] >= threshold:
            flagged.append({"output": str(out), "training_image": best[1], "similarity": round(best[0], 4)})
    return {"checked": len(output_paths), "flagged": flagged, "threshold": threshold, "ok": not flagged}
