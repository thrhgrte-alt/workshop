"""Generic landmark roles: loading, the six generic feature functions, and the scoring formula.

    score(r, x) = clamp( sum_i w_i * f_i(x) / sum_i |w_i| , 0, 1 )          f_i(x) in [0, 1]

The six feature functions (token match, class presence, remote and string references, repetition, reference density, proximity cluster) are implemented ONCE below and reused by every role;
a role is only a weighted list of them (rules/roles.yaml, plus projects/<project_id>/roles.yaml). Weights, bands and the feature constants are named parameters, so a project's learned
values (see domain/labels.py) replace the defaults without touching these files. No game-specific names appear anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from ..guide_adapter import Project
from .index import PlaceIndex
from .util import clamp, norm_token, parent_path, split_tokens

FUNCTIONS = ("token_match", "class_presence", "remote_string_refs", "repetition", "reference_density", "proximity_cluster")
FIELDS = ("name", "attribute_keys")
SOURCES = ("remotes", "strings")


@dataclass
class Feature:
    id: str
    fn: str
    w: float
    cfg: dict = field(default_factory=dict)


@dataclass
class Role:
    id: str
    description: str
    features: dict[str, Feature]
    bands: dict[str, float]
    source: str = "default"
    aliases: tuple = ()


# --- loading ------------------------------------------------------------------------------------------------------------------------------
def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.is_file() else {}


def project_files(project: Project, project_id: str | None) -> tuple[Path | None, Path | None]:
    if not project_id:
        return None, None
    d = project.root / "projects" / project_id
    return d / "roles.yaml", d / "synonyms.yaml"


def parse_roles(data: dict, source: str) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for rid, body in (data.get("roles") or {}).items():
        feats = {}
        for fid, spec in (body.get("features") or {}).items():
            spec = dict(spec)
            fn, w = spec.pop("fn", None), spec.pop("w", None)
            feats[fid] = Feature(fid, fn, float(w) if isinstance(w, (int, float)) else w, spec)
        roles[rid] = Role(rid, body.get("description", ""), feats, dict(body.get("bands") or {}), source, tuple(str(a) for a in body.get("aliases") or ()))
    return roles


def load_roles(project: Project, project_id: str | None = None) -> dict[str, Role]:
    roles = parse_roles(_read(project.root / "rules" / "roles.yaml"), "default")
    rf, _ = project_files(project, project_id)
    if rf is not None:
        for rid, role in parse_roles(_read(rf), "project").items():
            roles[rid] = role  # a project role with a default id replaces it for that project only
    return roles


def load_synonyms(project: Project, project_id: str | None = None) -> dict[str, set[str]]:
    base = (_read(project.root / "rules" / "synonyms.yaml").get("synonyms") or {})
    out: dict[str, set[str]] = {k: {norm_token(t) for tok in v for t in split_tokens(str(tok))} for k, v in base.items()}
    _, sf = project_files(project, project_id)
    if sf is not None:
        for k, v in (_read(sf).get("synonyms") or {}).items():
            out.setdefault(k, set()).update(norm_token(t) for tok in v for t in split_tokens(str(tok)))
    return out


def synonym_tokens(role: Role, feat: Feature, syn: dict[str, set[str]]) -> set[str]:
    toks = {norm_token(t) for tok in feat.cfg.get("tokens", []) for t in split_tokens(str(tok))}
    toks |= syn.get(f"{role.id}.{feat.id}", set())
    return toks


def validate_roles(roles: dict[str, Role], syn: dict[str, set[str]]) -> list[str]:
    problems = []
    seen: dict[frozenset, str] = {}
    for rid, r in roles.items():
        for phrase in (rid.replace("_", " "), *r.aliases):
            key = frozenset(split_tokens(phrase))
            if key in seen and seen[key] != rid:
                problems.append(f"role {rid}: the words '{phrase}' also name role {seen[key]} (a request could not tell them apart)")
            seen.setdefault(key, rid)
    for rid, r in roles.items():
        if not r.features:
            problems.append(f"role {rid}: no features")
        bands = r.bands
        if not (isinstance(bands.get("confident"), (int, float)) and isinstance(bands.get("candidate"), (int, float)) and 0 <= bands["candidate"] <= bands["confident"] <= 1):
            problems.append(f"role {rid}: bands need 0 <= candidate <= confident <= 1")
        for f in r.features.values():
            if f.fn not in FUNCTIONS:
                problems.append(f"role {rid}.{f.id}: unknown fn {f.fn!r} (one of {FUNCTIONS})")
                continue
            if not isinstance(f.w, float):
                problems.append(f"role {rid}.{f.id}: w must be a number")
            if f.fn == "token_match":
                if f.cfg.get("field") not in FIELDS:
                    problems.append(f"role {rid}.{f.id}: token_match needs field in {FIELDS}")
                if not synonym_tokens(r, f, syn):
                    problems.append(f"role {rid}.{f.id}: no tokens (add `tokens:` or rules/synonyms.yaml key '{rid}.{f.id}')")
            if f.fn == "remote_string_refs":
                if not synonym_tokens(r, f, syn):
                    problems.append(f"role {rid}.{f.id}: no tokens (add rules/synonyms.yaml key '{rid}.{f.id}')")
                bad = [s for s in f.cfg.get("sources", list(SOURCES)) if s not in SOURCES]
                if bad:
                    problems.append(f"role {rid}.{f.id}: unknown sources {bad}")
            if f.fn == "class_presence" and not f.cfg.get("classes"):
                problems.append(f"role {rid}.{f.id}: class_presence needs classes")
            if f.fn == "proximity_cluster" and not f.cfg.get("near_roles"):
                problems.append(f"role {rid}.{f.id}: proximity_cluster needs near_roles")
    return problems


# --- the generic feature functions -------------------------------------------------------------------------------------------------------
class Scorer:
    """Scores every scorable instance of one snapshot for every role. ``pv(name)`` returns the value in force of a named parameter (defaults or learned)."""

    def __init__(self, idx: PlaceIndex, roles: dict[str, Role], syn: dict[str, set[str]], pv: Callable[[str], float], skip_classes: set[str], positive_labels: dict[str, set[str]] | None = None):
        self.idx, self.roles, self.syn, self.pv = idx, roles, syn, pv
        self.skip = skip_classes
        self.positive = positive_labels or {}
        self._rows: dict[str, list[dict]] | None = None
        self._by_path: dict[str, dict[str, dict]] = {}
        self._syn_cache: dict[tuple, set[str]] = {}
        self._have_cache: dict[tuple, frozenset] = {}
        self._script_tokens: dict[tuple, frozenset] = {}

    # f_i ------------------------------------------------------------------------------------------------------------------------------
    def _syn(self, role: Role, feat: Feature) -> set[str]:
        key = (role.id, feat.id)
        hit = self._syn_cache.get(key)
        if hit is None:
            hit = self._syn_cache[key] = synonym_tokens(role, feat, self.syn)
        return hit

    def token_match(self, role: Role, feat: Feature, path: str) -> float:
        syn = self._syn(role, feat)
        have = set(self.idx.name_tokens(path)) if feat.cfg["field"] == "name" else self.idx.attr_key_tokens(path)
        k = int(feat.cfg.get("k", self.pv("feature.token_k")))
        target = min(len(syn), k)
        return clamp(len(have & syn) / target) if target else 0.0

    def class_presence(self, role: Role, feat: Feature, path: str) -> float:
        classes = set(feat.cfg["classes"])
        snap = self.idx.snap
        if feat.cfg.get("include_self") and snap.by_path[path]["class"] in classes:
            return 1.0
        return 1.0 if snap.descendant_classes(path) & classes else 0.0

    def _tokens_of_script(self, sp: str, sources: tuple[str, ...]) -> frozenset:
        key = (sp, sources)
        hit = self._script_tokens.get(key)
        if hit is None:
            rec = self.idx.snap.scripts[sp]
            have: set[str] = set()
            if "remotes" in sources:
                for r in rec.get("remotes_fired", []) + rec.get("remotes_handled", []):
                    have.update(split_tokens(r["name"]))
            if "strings" in sources:
                have.update(rec.get("string_tokens", []))
            hit = self._script_tokens[key] = frozenset(have)
        return hit

    def remote_string_refs(self, role: Role, feat: Feature, path: str) -> float:
        syn = self._syn(role, feat)
        sources = tuple(feat.cfg.get("sources", list(SOURCES)))
        refs = frozenset(self.idx.referencing_scripts(path))
        key = (sources, refs)
        have = self._have_cache.get(key)
        if have is None:
            acc: set[str] = set()
            for sp in refs:
                acc |= self._tokens_of_script(sp, sources)
            have = self._have_cache[key] = frozenset(acc)
        k = int(feat.cfg.get("k", self.pv("feature.token_k")))
        target = min(len(syn), k)
        return clamp(len(have & syn) / target) if target else 0.0

    def repetition(self, role: Role, feat: Feature, path: str) -> float:
        n = self.idx.n_similar(path, self.pv("feature.repetition_jaccard"))
        return n / (n + self.pv("feature.repetition_c"))

    def reference_density(self, role: Role, feat: Feature, path: str) -> float:
        c = self.idx.ref_count(path)
        if c == 0 or self.idx.median_ref <= 0:
            return 0.0
        r = c / self.idx.median_ref
        return r / (1.0 + r)

    def proximity_cluster(self, role: Role, feat: Feature, path: str, landmarks: dict[str, list[str]]) -> float:
        d = self.pv("feature.proximity_distance")
        best = None
        for other in feat.cfg["near_roles"]:
            if other == role.id:
                continue
            for lm in landmarks.get(other, ()):
                if lm == path:
                    continue
                dist = self.idx.distance(path, lm)
                if dist is not None and (best is None or dist < best):
                    best = dist
        return clamp(1.0 - best / d) if best is not None and d > 0 else 0.0

    # scoring ------------------------------------------------------------------------------------------------------------------------------
    def weights(self, role: Role) -> dict[str, float]:
        return {fid: float(self.pv(f"weight.{role.id}.{fid}")) for fid in role.features}

    def band_values(self, role: Role) -> tuple[float, float]:
        return float(self.pv(f"band.{role.id}.confident")), float(self.pv(f"band.{role.id}.candidate"))

    def values(self, role: Role, path: str, landmarks: dict[str, list[str]] | None) -> dict[str, float]:
        out = {}
        for fid, feat in role.features.items():
            if feat.fn == "proximity_cluster":
                out[fid] = self.proximity_cluster(role, feat, path, landmarks) if landmarks is not None else 0.0
            else:
                out[fid] = getattr(self, feat.fn)(role, feat, path)
        return out

    @staticmethod
    def combine(weights: dict[str, float], f: dict[str, float]) -> float:
        denom = sum(abs(w) for w in weights.values())
        return clamp(sum(weights[k] * f[k] for k in weights) / denom) if denom else 0.0

    def scorable(self) -> list[str]:
        return [p for p, i in self.idx.snap.by_path.items() if i["class"] not in self.skip]

    def run(self) -> dict[str, list[dict]]:
        """{role: rows sorted by score}. Two passes: pass 1 without the proximity feature finds the confident landmarks, pass 2 adds the proximity feature near them."""
        if self._rows is not None:
            return self._rows
        paths = self.scorable()
        w = {r.id: self.weights(r) for r in self.roles.values()}
        bands = {r.id: self.band_values(r) for r in self.roles.values()}
        pass1: dict[str, dict[str, tuple[float, dict]]] = {}
        needs2 = {r.id for r in self.roles.values() if any(f.fn == "proximity_cluster" for f in r.features.values())}
        landmarks: dict[str, list[str]] = {}
        for role in self.roles.values():
            res = {}
            for p in paths:
                f = self.values(role, p, None)
                res[p] = (self.combine(w[role.id], f), f)
            pass1[role.id] = res
            landmarks[role.id] = sorted({p for p, (s, _) in res.items() if s >= bands[role.id][0]} | self.positive.get(role.id, set()))
        rows: dict[str, list[dict]] = {}
        for role in self.roles.values():
            out = []
            for p in paths:
                s, f = pass1[role.id][p]
                if role.id in needs2:
                    f = self.values(role, p, landmarks)
                    s = self.combine(w[role.id], f)
                if s <= 0 and p not in self.positive.get(role.id, set()):
                    continue
                conf, cand = bands[role.id]
                out.append({"path": p, "score": round(s, 4), "band": "confident" if s >= conf else "candidate" if s >= cand else "below", "features": {k: round(v, 4) for k, v in f.items()}})
            # a container only inherits the evidence of what is inside it, so it is not a landmark itself when (a) a descendant scores higher for the same role, or
            # (b) it is a plain Folder with a descendant that is at least a candidate (a folder of vendors is not a vendor)
            best: dict[str, tuple[float, str]] = {}
            for r in out:
                par = parent_path(r["path"])
                while par:
                    if par not in best or r["score"] > best[par][0]:
                        best[par] = (r["score"], r["path"])
                    par = parent_path(par)
            pos = self.positive.get(role.id, set())
            cand = bands[role.id][1]
            for r in out:
                b = best.get(r["path"])
                if b and r["path"] not in pos and (b[0] > r["score"] + 1e-9 or (self.idx.snap.by_path[r["path"]]["class"] in ("Folder", "Configuration") and b[0] >= cand)):
                    r["band"], r["container_of"] = "container", b[1]
            out.sort(key=lambda r: (-r["score"], r["path"]))
            rows[role.id] = out
        self._rows = rows
        self._by_path = {rid: {r["path"]: r for r in lst} for rid, lst in rows.items()}
        return rows

    def score_one(self, role_id: str, path: str) -> dict:
        """One instance and role with the full feature breakdown (runs the passes if needed)."""
        role = self.roles[role_id]
        self.run()
        hit = self._by_path.get(role_id, {}).get(path)
        w = self.weights(role)
        if hit is None:
            f = self.values(role, path, None)
            hit = {"path": path, "score": round(self.combine(w, f), 4), "features": {k: round(v, 4) for k, v in f.items()}}
            conf, cand = self.band_values(role)
            hit["band"] = "confident" if hit["score"] >= conf else "candidate" if hit["score"] >= cand else "below"
        hit = dict(hit)
        hit["role"] = role_id
        hit["weights"] = w
        hit["contributions"] = {k: round(w[k] * hit["features"][k], 4) for k in w}
        return hit


def explain(row: dict, n: int = 3) -> list[str]:
    """Top features that raise the score (positive contributions), as 'name f=0.50 x w=3.0'."""
    contrib = row.get("contributions") or {}
    w = row.get("weights") or {}
    top = sorted((k for k in contrib if contrib[k] > 0), key=lambda k: -contrib[k])[:n]
    return [f"{k} (f={row['features'][k]:.2f} x w={w[k]:g})" for k in top]


def negatives(row: dict) -> list[str]:
    contrib = row.get("contributions") or {}
    return [f"{k} ({contrib[k]:+.2f})" for k in contrib if contrib[k] < 0]
