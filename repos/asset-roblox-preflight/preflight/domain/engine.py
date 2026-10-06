"""The rule engine: context (limits, severities, overrides), the check registry, and the runner.

Checks only *measure and compare*. Severity, wording, limits, enablement and the safe flag all come from rules/*.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import yaml

from .measure import measure_asset
from .rules import Config, Overrides, SEV_ORDER, apply_project_layer, load_profiles, load_rules, render

# --- registry ---------------------------------------------------------------------------------------
CHECKS: list[tuple[Callable, tuple[str, ...]]] = []


def implements(*rule_ids: str):
    def deco(fn):
        CHECKS.append((fn, rule_ids))
        return fn

    return deco


def implemented_rule_ids() -> set[str]:
    return {r for _, ids in CHECKS for r in ids}


# --- config -----------------------------------------------------------------------------------------
_CACHE: dict = {}


def load_config(root: Path, project_id: str | None = None) -> Config:
    """Global rules, profiles and style, with the project's profiles.yaml layered on top when project_id is given."""
    root = Path(root)
    pfile = None
    if project_id:
        from ..projects import resolve

        pfile = root / resolve(root, project_id)["profile"]
        if not pfile.exists():
            raise ValueError(f"project '{project_id}' is registered but its profile file {pfile} does not exist")
    files = sorted((root / "rules").rglob("*.yaml")) + [root / "style" / "style.yaml"] + ([pfile] if pfile else [])
    key = (str(root), project_id, tuple((str(f), f.stat().st_mtime_ns) for f in files if f.exists()))
    if key in _CACHE:
        return _CACHE[key]
    rules = load_rules(root / "rules")
    profiles = load_profiles(root / "rules" / "profiles", rules)
    style = yaml.safe_load((root / "style" / "style.yaml").read_text(encoding="utf-8")) or {}
    if pfile:
        doc = yaml.safe_load(pfile.read_text(encoding="utf-8")) or {}
        if doc.get("project") != project_id:
            raise ValueError(f"{pfile.name} says project '{doc.get('project')}' but is registered for '{project_id}'")
        profiles, style = apply_project_layer(profiles, style, doc, rules, f"projects/{project_id}")
    cfg = Config(rules, profiles, style, project_id)
    if len(_CACHE) > 16:
        _CACHE.clear()
    _CACHE[key] = cfg
    return cfg


def check_coverage(cfg: Config) -> dict:
    impl = implemented_rule_ids()
    return {"rules_without_code": sorted(set(cfg.rules) - impl), "code_without_rules": sorted(impl - set(cfg.rules))}


# --- context ----------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, asset: dict, cfg: Config, profile: str, overrides: Overrides | None = None, collision_intent: str = "auto"):
        if profile not in cfg.profiles:
            raise ValueError(f"unknown profile '{profile}'. Choose one of {sorted(cfg.profiles)}")
        self.asset, self.cfg, self.profile_name = asset, cfg, profile
        self.profile = cfg.profiles[profile]
        self.ov = overrides or Overrides()
        self.collision_intent = collision_intent
        self.findings: list[dict] = []
        self.used_overrides: list[dict] = []
        self.checked_rules: set[str] = set()
        self.state: dict = {}
        self.metrics = measure_asset(asset)
        self.studs = float(self.profile["settings"].get("studs_per_unit", 1.0))
        pats = self.limit("COL_PROXY_MISSING", "name_patterns")
        self._col_re = [re.compile(p, re.I) for p in pats]
        self.objects = asset["objects"]
        self.meshes = [o for o in self.objects if o["kind"] == "mesh" and o.get("mesh") is not None]
        self.proxies = [o for o in self.meshes if self.is_collision(o)]
        self.visual = [o for o in self.meshes if not self.is_collision(o)]

    # classification
    def is_collision(self, obj: dict) -> bool:
        if obj.get("is_collision_declared") is not None:
            return bool(obj["is_collision_declared"])
        if (obj.get("extras") or {}).get("preflight_role") == "collision":
            return True
        return any(r.search(obj["name"]) for r in self._col_re)

    # rules
    def rule(self, rid: str) -> dict:
        return self.cfg.rules[rid]

    def enabled(self, rid: str) -> bool:
        r = self.cfg.rules[rid]
        if not r.get("enabled", True) or rid in (self.profile.get("disabled_rules") or []):
            return False
        at = r.get("applies_to")
        return not at or self.profile_name in at

    def severity(self, rid: str) -> str:
        return (self.profile.get("severity") or {}).get(rid) or self.cfg.rules[rid]["severity"]

    def limit(self, rid: str, key: str):
        found, value, src = self.ov.lookup(rid, key)
        if found:
            marker = {"rule": rid, "limit_key": key, "value": value, "source": "call" if src == "call" else "saved"}
            if src != "call":
                marker.update({"reason": src.get("reason"), "asset_type": src.get("asset_type")})
            if marker not in self.used_overrides:
                self.used_overrides.append(marker)
            return value
        pl = (self.profile.get("limits") or {}).get(rid) or {}
        if key in pl:
            return pl[key]
        spec = self.cfg.rules[rid]["limits"].get(key)
        if spec is None:
            raise KeyError(f"rule {rid} has no limit '{key}'")
        return spec["default"]

    def verify_flag(self, rid: str, key: str) -> bool:
        spec = self.cfg.rules[rid]["limits"].get(key) or {}
        return bool(spec.get("verify_against_current_docs"))

    # findings
    def add(self, rid: str, obj: str | None, measured, limit=None, *, message: str = "", fix_op: dict | None = None, source: str = "computed",
            limit_key: str | None = None, fix_vars: dict | None = None, detail: str | None = None) -> dict | None:
        if not self.enabled(rid):
            return None
        rule = self.cfg.rules[rid]
        sev = self.severity(rid)
        f = {"rule": rid, "severity": sev, "category": rule["category"], "object": obj, "measured": measured, "limit": limit,
             "message": message or rule["title"], "fix": render(rule["fix"], **{"object": obj if obj is not None else "the asset", "measured": measured, "limit": limit, **(fix_vars or {})}),
             "safe_fix": bool(fix_op) and bool(rule["safe"]), "fix_op": fix_op if rule["safe"] else None, "source": source,
             "verify_against_current_docs": bool(limit_key and self.verify_flag(rid, limit_key)), "title": rule["title"]}
        if detail:
            f["detail"] = detail
        w = self.ov.waiver(rid)
        if w:
            f["severity_original"] = sev
            f["severity"] = "info"
            f["overridden"] = {"reason": w.get("reason"), "asset_type": w.get("asset_type"), "waived": True}
            if not any(u.get("rule") == rid and u.get("waive") for u in self.used_overrides):
                self.used_overrides.append({"rule": rid, "waive": True, "reason": w.get("reason"), "asset_type": w.get("asset_type"), "source": "saved"})
        self.findings.append(f)
        return f

    # helpers
    def mesh_material_ids(self, obj: dict) -> list[int]:
        prims = obj["mesh"].get("primitives") or obj["mesh"].get("primitives_reported") or []
        return sorted({p["material"] for p in prims if p.get("material") is not None})

    def is_textured_material(self, mid: int) -> bool:
        mats = self.asset["materials"]
        return 0 <= mid < len(mats) and bool(mats[mid]["textures"])

    def textured(self, obj: dict) -> bool:
        return any(self.is_textured_material(m) for m in self.mesh_material_ids(obj))

    def visual_dims_world(self):
        """Union world bounding box (lo, hi) of visible meshes, or the largest reported dimensions."""
        los, his, reported = [], [], []
        for o in self.visual:
            m = self.metrics[o["id"]]
            if m.get("bbox_world"):
                los.append(m["bbox_world"][0])
                his.append(m["bbox_world"][1])
            elif m.get("dims_world"):
                reported.append(m["dims_world"])
        if los:
            lo = tuple(min(p[i] for p in los) for i in range(3))
            hi = tuple(max(p[i] for p in his) for i in range(3))
            return lo, hi, tuple(hi[i] - lo[i] for i in range(3))
        if reported:
            dims = tuple(max(d[i] for d in reported) for i in range(3))
            return None, None, dims
        return None, None, None

    @property
    def visual_triangles(self) -> int | None:
        vals = [self.metrics[o["id"]]["triangles"] for o in self.visual]
        return None if not vals or any(v is None for v in vals) else sum(vals)


def run_checks(ctx: Ctx, categories: set[str] | None = None) -> list[dict]:
    if not CHECKS:
        raise RuntimeError("no checks are registered (import preflight.domain.report, which loads them)")
    for fn, ids in CHECKS:
        cats = {ctx.cfg.rules[r]["category"] for r in ids if r in ctx.cfg.rules}
        if categories is not None and not (cats & categories):
            continue
        fn(ctx)
        ctx.checked_rules |= {r for r in ids if ctx.enabled(r)}
    ctx.findings.sort(key=lambda f: (SEV_ORDER[f["severity"]], f["category"], f["rule"], str(f["object"])))
    return ctx.findings
