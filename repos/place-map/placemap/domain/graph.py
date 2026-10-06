"""Dependency graph over one snapshot: which script requires which module, which scripts fire or handle which remote, which DataStore keys they touch.

Edges come from the static analysis in domain/scan.py (a lexer plus path-expression resolver, not a Luau parser), resolved against the snapshot's tree. A require that cannot be resolved by
path is matched by module NAME only if exactly one module has that name (flagged ``matched_by: name``); otherwise it stays unresolved and says why. Nothing is guessed silently.
"""

from __future__ import annotations

from collections import defaultdict

from .index import PlaceIndex
from .snapshot import REMOTE_CLASSES
from .util import parent_path


class Graph:
    def __init__(self, idx: PlaceIndex):
        self.idx = idx
        snap = idx.snap
        self.modules_by_name: dict[str, list[str]] = defaultdict(list)
        for p, s in snap.scripts.items():
            if s["class"] == "ModuleScript":
                self.modules_by_name[idx.name(p)].append(p)
        self.requires: dict[str, list[dict]] = {}
        self.dependents: dict[str, list[dict]] = defaultdict(list)
        for sp, rec in snap.scripts.items():
            edges = [self._resolve_require(sp, r) for r in rec.get("requires", [])]
            self.requires[sp] = edges
            for e in edges:
                if e["target"]:
                    self.dependents[e["target"]].append({"script": sp, "matched_by": e["matched_by"], "expr": e["expr"]})
        self.remotes: dict[str, dict] = {}
        for p, inst in snap.by_path.items():
            if inst["class"] in REMOTE_CLASSES:
                self.remotes.setdefault(idx.name(p), {"instances": [], "fired_by": [], "handled_by": []})["instances"].append(p)
        for sp, rec in snap.scripts.items():
            for kind, key in (("remotes_fired", "fired_by"), ("remotes_handled", "handled_by")):
                for r in rec.get(kind, []):
                    entry = self.remotes.setdefault(r["name"], {"instances": [], "fired_by": [], "handled_by": []})
                    entry[key].append({"script": sp, "via": r["via"], **({"name_guess": True} if r.get("name_guess") else {})})
        self.datastore_users: dict[str, list[str]] = defaultdict(list)
        for sp, rec in snap.scripts.items():
            for k in rec.get("datastores", []) + rec.get("datastore_keys", []):
                self.datastore_users[k].append(sp)

    # resolution -------------------------------------------------------------------------------------------------------------------------
    def _walk(self, script_path: str, chain: list[str]) -> str | None:
        snap = self.idx.snap
        first = chain[0]
        if first == "script":
            cur = script_path
        elif first.startswith("@"):
            cur = first[1:]
            if cur not in snap.by_path:
                return None
        else:
            return None
        for tok in chain[1:]:
            if tok == "..":
                cur = parent_path(cur)
                continue
            nxt = next((c for c in snap.children_of(cur) if self.idx.name(c) == tok), None)
            if nxt is None:
                return None
            cur = nxt
        return cur if cur in snap.by_path else None

    def _resolve_require(self, script_path: str, req: dict) -> dict:
        base = {"expr": req["expr"], "target": None, "matched_by": None, "asset_id": req.get("asset_id")}
        if req.get("asset_id") is not None:
            return {**base, "unresolved": "required by asset id: the code is outside this place and cannot be mapped"}
        chain = req.get("chain")
        if not chain:
            return {**base, "unresolved": "the require argument is not a path expression this reader understands"}
        hit = self._walk(script_path, chain)
        if hit and self.idx.snap.by_path[hit]["class"] == "ModuleScript":
            return {**base, "target": hit, "matched_by": "path"}
        last = chain[-1].lstrip("@")
        cands = self.modules_by_name.get(last, [])
        if len(cands) == 1:
            return {**base, "target": cands[0], "matched_by": "name"}
        if len(cands) > 1:
            return {**base, "unresolved": f"path not found and {len(cands)} modules are named '{last}'", "candidates": cands[:5]}
        return {**base, "unresolved": f"no module at the path and none named '{last}' in this snapshot (stale snapshot or a module created at runtime?)"}

    # queries ----------------------------------------------------------------------------------------------------------------------------
    def transitive_dependents(self, path: str, depth: int = 3, cap: int = 200) -> list[dict]:
        """Scripts that break if ``path`` (a module) changes: direct requirers at level 1, their requirers at level 2 ... (breadth first, each script once)."""
        seen, out, frontier = {path}, [], [path]
        for level in range(1, depth + 1):
            nxt = []
            for t in frontier:
                for d in self.dependents.get(t, []):
                    s = d["script"]
                    if s not in seen:
                        seen.add(s)
                        out.append({"script": s, "level": level, "via": t, "matched_by": d["matched_by"]})
                        nxt.append(s)
            frontier = nxt
            if not frontier or len(out) >= cap:
                break
        return out[:cap]

    def dependencies(self, path: str, depth: int = 2, cap: int = 200) -> dict:
        snap = self.idx.snap
        rec = snap.scripts.get(path)
        if rec is None:
            raise ValueError(f"'{path}' is not a script in this snapshot")
        seen, mods, unresolved, frontier = {path}, [], [], [path]
        cycles = []
        for level in range(1, depth + 1):
            nxt = []
            for s in frontier:
                for e in self.requires.get(s, []):
                    if e["target"] is None:
                        unresolved.append({"from": s, "expr": e["expr"], "reason": e.get("unresolved")})
                    elif e["target"] == path and s != path:
                        cycles.append({"from": s, "to": path})
                    elif e["target"] not in seen:
                        seen.add(e["target"])
                        mods.append({"module": e["target"], "level": level, "from": s, "matched_by": e["matched_by"]})
                        nxt.append(e["target"])
            frontier = nxt
            if not frontier or len(mods) >= cap:
                break
        return {"modules": mods[:cap], "unresolved": unresolved[:20], "cycles": cycles,
                "remotes_fired": sorted({r["name"] for r in rec["remotes_fired"]}), "remotes_handled": sorted({r["name"] for r in rec["remotes_handled"]}),
                "datastores": rec["datastores"], "datastore_keys": rec["datastore_keys"]}

    def who_uses(self, path: str, depth: int = 2) -> dict:
        """Callers of whatever ``path`` is: requirers of a module, fire/handle sites of a remote, scripts that mention an instance by name (weaker evidence)."""
        snap = self.idx.snap
        inst = snap.by_path.get(path)
        if inst is None:
            raise ValueError(f"'{path}' is not in this snapshot")
        out: dict = {"class": inst["class"]}
        if path in snap.scripts:
            out["requirers"] = self.transitive_dependents(path, depth)
        if inst["class"] in REMOTE_CLASSES:
            r = self.remotes.get(self.idx.name(path), {})
            out["fired_by"] = r.get("fired_by", [])
            out["handled_by"] = r.get("handled_by", [])
        if path not in snap.scripts and inst["class"] not in REMOTE_CLASSES:
            out["mentioned_by"] = sorted(self.idx.mentions.get(self.idx.name(path), ())) if not self.idx.is_generic_name(path) else []
            out["inside"] = sorted(self.idx.inside.get(path, []))[:50]
        return out

    def remote_table(self) -> list[dict]:
        rows = []
        for name, e in sorted(self.remotes.items()):
            issues = []
            if not e["fired_by"] and not e["handled_by"]:
                issues.append("no script fires or handles it in this snapshot")
            if e["fired_by"] and not e["handled_by"]:
                issues.append("no script handles it")
            if e["handled_by"] and not e["fired_by"]:
                issues.append("no script fires it")
            if not e["instances"]:
                issues.append("not defined in the snapshot (created at runtime or outside the refreshed roots?)")
            rows.append({"name": name, "defined": bool(e["instances"]), "paths": e["instances"], "classes": sorted({self.idx.snap.by_path[p]["class"] for p in e["instances"]}),
                         "fired_by": e["fired_by"], "handled_by": e["handled_by"], "issues": issues})
        return rows
