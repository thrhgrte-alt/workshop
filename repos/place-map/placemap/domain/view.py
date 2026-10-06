"""A loaded place: snapshot + index + learned parameters + labels + role scorer, and the request resolution that turns "the vendors", "Bob" or a path into exact instances.

Rules of resolution (see the README): a path or a landmark alias is exact; a name that exactly one instance has is exact; a role request returns only the instances that play the role in
THIS place (confident score or explicit user label), with the score and the top features; borderline instances are listed as 'not selected' with a question; nothing is widened.
"""

from __future__ import annotations

from .. import learning_params as LP
from . import roles as R
from . import search as SE
from .graph import Graph
from .index import PlaceIndex, index_for
from .labels import LabelStore
from .places import PlaceCtx
from .snapshot import Snapshot, SnapshotStore, staleness
from .util import PLACE_SEP, clean_path_input, prefixed, split_tokens

_SCORER_CACHE: dict[tuple, R.Scorer] = {}


class PlaceView:
    def __init__(self, ctx: PlaceCtx, snap: Snapshot):
        self.ctx, self.snap = ctx, snap
        self.idx: PlaceIndex = index_for(snap)
        project = ctx.project
        self.ps = LP.store(project, ctx.project_id)
        self.pv = LP.values_for(project, ctx.project_id, ctx.place_id, self.ps)
        self.roles = R.load_roles(project, ctx.project_id)
        self.syn = R.load_synonyms(project, ctx.project_id)
        self.labels = LabelStore(ctx)
        self._graph: Graph | None = None
        self._scorer: R.Scorer | None = None
        self.limits = LP.limits(project)

    @classmethod
    def load(cls, ctx: PlaceCtx, ref: str = "latest") -> "PlaceView":
        return cls(ctx, SnapshotStore(ctx.wproject).load(ref))

    # pieces
    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = Graph(self.idx)
        return self._graph

    @property
    def scorer(self) -> R.Scorer:
        if self._scorer is None:
            snapshot = self.pv.snapshot
            key = (self.ctx.project.root, self.ctx.scope.key, self.snap.id, tuple(sorted((k, v["value"]) for k, v in snapshot.items() if k.startswith(("weight.", "band.", "feature.")))),
                   self.labels.file.stat().st_mtime_ns if self.labels.file.exists() else 0, tuple(sorted(self.roles)))
            sc = _SCORER_CACHE.get(key)
            if sc is None:
                if len(_SCORER_CACHE) > 6:
                    _SCORER_CACHE.clear()
                skip = set((self.limits.get("scoring") or {}).get("skip_classes", []))
                sc = R.Scorer(self.idx, self.roles, self.syn, self.pv, skip, self.labels.positive())
                _SCORER_CACHE[key] = sc
            self._scorer = sc
        return self._scorer

    @property
    def cap(self) -> int:
        return int(self.pv("output.max_findings"))

    def head(self) -> dict:
        st = staleness(self.snap.taken_at, float(self.pv("snapshots.stale_hours")))
        snap = {"id": self.snap.id, "taken_at": st["taken_at"], "age_hours": st["age_hours"], "stale": st["stale"], "parser": "schema_unverified"}
        if self.snap.data.get("full", True) is False:
            snap["partial"] = True
        if self.snap.data.get("truncated"):
            snap["truncated"] = True
        if st["stale"]:
            snap["offer"] = f"snapshot is {st['age_hours']}h old (stale after {st['stale_after_hours']}h): call plan_refresh to collect fresh data"
        return {**self.ctx.head(), "snapshot": snap}

    def pp(self, path: str) -> str:
        return prefixed(self.ctx.place_id, path)

    # roles
    def why(self, row: dict) -> list[str]:
        return R.explain(row)

    def role_resolution(self, role_id: str, k: int | None = None) -> dict:
        if role_id not in self.roles:
            raise ValueError(f"unknown role '{role_id}'. Known roles: {sorted(self.roles)}")
        k = k or self.cap
        sc = self.scorer
        rows = sc.run()[role_id]
        act = self.labels.active()
        conf, cand = sc.band_values(self.roles[role_id])
        selected, not_selected, rejected = [], [], []
        seen = set()
        for (path, role), lab in sorted(act.items()):
            if role != role_id:
                continue
            seen.add(path)
            if path not in self.snap.by_path:
                continue
            row = sc.score_one(role_id, path)
            entry = {"path": self.pp(path), "class": self.snap.by_path[path]["class"], "score": row["score"], "source": "user_label" if lab["label"] == 1 else "user_rejected", "why": self.why(row)}
            (selected if lab["label"] == 1 else rejected).append(entry)
        for r in rows:
            if r["path"] in seen:
                continue
            entry = {"path": self.pp(r["path"]), "class": self.snap.by_path[r["path"]]["class"], "score": r["score"], "why": [], "source": "scored"}
            row = sc.score_one(role_id, r["path"])
            entry["why"] = self.why(row)
            if r["band"] == "container":
                continue
            if r["band"] == "confident":
                selected.append(entry)
            elif r["band"] == "candidate":
                entry["reason"] = f"borderline ({cand} <= score < {conf}): confirm or reject with label_landmark"
                not_selected.append(entry)
        selected.sort(key=lambda e: (-e["score"], e["path"]))
        missing = sorted(p for (p, r), l in act.items() if r == role_id and l["label"] == 1 and p not in self.snap.by_path)
        res = {"role": role_id, "bands": {"confident": conf, "candidate": cand}, "selected": selected[:k], "not_selected": not_selected[:k], "rejected_by_user": rejected[:5]}
        if len(selected) > k:
            res["more_selected"] = len(selected) - k
        if len(not_selected) > k:
            res["more_not_selected"] = len(not_selected) - k
        if missing:
            res["labelled_but_missing_from_snapshot"] = [self.pp(m) for m in missing]
        if not_selected:
            res["ask"] = f"{len(not_selected)} borderline {role_id} candidate(s) were NOT selected: ask the user to confirm or reject them (label_landmark)"
        return res

    def path_roles(self, path: str, include_below: bool = False) -> list[dict]:
        out = []
        act = self.labels.active()
        for rid in self.roles:
            row = self.scorer.score_one(rid, path)
            lab = act.get((path, rid))
            if row["band"] != "below" or lab or include_below:
                out.append({"role": rid, "score": row["score"], "band": row["band"], "label": None if not lab else ("confirmed" if lab["label"] == 1 else "rejected"),
                            "why": self.why(row), "against": R.negatives(row)})
        return sorted(out, key=lambda r: -r["score"])

    # request resolution
    def role_in_query(self, query: str) -> str | None:
        """The role a request names: its words are exactly the role id's words or one of its aliases (rules/roles.yaml), ignoring 'npc'/'object'/'instance'."""
        qt = set(SE.query_tokens(query)) - {"npc", "object", "instance"}
        if not qt:
            return None
        for rid, role in self.roles.items():
            for phrase in (rid.replace("_", " "), *role.aliases):
                if qt == set(split_tokens(phrase)):
                    return rid
        return None

    def resolve(self, query: str, *, role: str | None = None, kind: str | None = None, under: str | None = None, k: int | None = None) -> dict:
        k = k or self.cap
        q = query.strip()
        if role:
            return {"mode": "role", **self.role_resolution(role, k)}
        if not q:
            raise ValueError("query is empty: name a path, a role, a landmark alias or text to search for")
        if SE.looks_like_path(q):
            if PLACE_SEP in q and q.split(PLACE_SEP, 1)[0] != self.ctx.place_id:
                raise ValueError(f"the path names place '{q.split(PLACE_SEP, 1)[0]}' but the active place is '{self.ctx.place_id}'. find_in_place searches one place; "
                                 f"use find_across_places to search several, or name the right place_id.")
            p = SE.exact_path(self.idx, q)
            if p:
                return {"mode": "path", "selected": [self._entry(p)], "not_selected": [self._entry(h["path"], reason=h["reason"]) for h in SE.literal_near(self.idx, p, self.idx.name(p), self.near_n())]}
            near = SE.near_matches(self.idx, clean_path_input(q).replace("/", " "), set(), self.near_n())
            return {"mode": "path", "selected": [], "not_selected": [self._entry(h["path"], score=h["score"], reason="similar path") for h in near],
                    "ask": f"no instance has the path '{clean_path_input(q)}' in this snapshot; similar ones are listed (the snapshot may be stale)"}
        alias = self.labels.aliases().get(" ".join(q.lower().split()))
        if alias and alias[0] in self.snap.by_path:
            return {"mode": "alias", "selected": [self._entry(alias[0], extra={"landmark_role": alias[1], "source": "user_label"})], "not_selected": []}
        rid = self.role_in_query(q)
        exact = SE.exact_name_matches(self.idx, q)
        if kind:
            exact = [p for p in exact if self.snap.by_path[p]["class"] in SE.CLASS_HINTS.get(kind.lower(), {kind})]
        if under:
            exact = [p for p in exact if p == under or p.startswith(under + "/")]
        if rid:
            res = {"mode": "role", **self.role_resolution(rid, k)}
            if exact:
                res["name_hint"] = f"{len(exact)} instance(s) are literally named like the request (e.g. {self.pp(exact[0])}); name one by path if that is what you meant"
            return res
        if len(exact) == 1:
            near = SE.literal_near(self.idx, exact[0], q, self.near_n())
            return {"mode": "exact_name", "selected": [self._entry(exact[0])], "not_selected": [self._entry(h["path"], reason=h["reason"]) for h in near]}
        if len(exact) > 1:
            return {"mode": "exact_name", "selected": [], "not_selected": [self._entry(p, reason="same name") for p in exact[:k]],
                    "ask": f"{len(exact)} instances are named like '{q}': none is selected. Which one? (use the path)"}
        hits = SE.hybrid(self.idx, q, k=k + self.near_n(), kind=kind, under=under, min_score=float(self.pv("search.min_score")))
        margin = float(self.pv("search.margin"))
        if hits and (len(hits) == 1 or hits[0]["score"] - hits[1]["score"] >= margin * hits[0]["score"]) and hits[0]["score"] >= 0.5:
            top = hits[0]
            return {"mode": "search", "selected": [self._entry(top["path"], score=top["score"], reason="; ".join(top["reasons"]))],
                    "not_selected": [self._entry(h["path"], score=h["score"], reason="; ".join(h["reasons"])) for h in hits[1:1 + self.near_n()]]}
        return {"mode": "search", "selected": [], "not_selected": [self._entry(h["path"], score=h["score"], reason="; ".join(h["reasons"])) for h in hits[:k]],
                **({"ask": "no clear single match: these are ranked text matches, none is selected. Name one by path, or say which you mean"} if hits else {"ask": "nothing matched"})}

    def near_n(self) -> int:
        return int(self.pv("search.near_matches"))

    def _entry(self, path: str, score: float | None = None, reason: str | None = None, extra: dict | None = None) -> dict:
        inst = self.snap.by_path[path]
        e = {"path": self.pp(path), "class": inst["class"]}
        if score is not None:
            e["score"] = score
        if reason:
            e["reason"] = reason
        rec = self.snap.scripts.get(path)
        if rec:
            e["script"] = {"kind": rec["kind"], "summary": rec["summary"][:160]}
        if extra:
            e.update(extra)
        return e
