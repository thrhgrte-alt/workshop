"""An in-memory index over one snapshot: the lookups the generic feature functions, the search and the dependency graph all share (built once per snapshot)."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .snapshot import SCRIPT_CLASSES, Snapshot
from .util import SEP, jaccard, leaf_name, median, parent_path, split_tokens, token_set

GENERIC_NAME = re.compile(r"(?i)(part|model|folder|meshpart|union|unionoperation|script|localscript|modulescript|handle|prompt|proximityprompt|clickdetector|wedge|sphere|cylinder|"
                          r"attachment|decal|texture|value|main|root|base|baseplate|spawnlocation|humanoid|configuration)\d*")


class PlaceIndex:
    def __init__(self, snap: Snapshot):
        self.snap = snap
        self._tok: dict[str, list[str]] = {}
        self._attr_tok: dict[str, set[str]] = {}
        self._rep: dict[tuple, float] = {}
        self._pos: dict[str, tuple | None] = {}
        self.inside: dict[str, list[str]] = defaultdict(list)  # instance path -> scripts strictly below it
        self.mentions: dict[str, set[str]] = defaultdict(set)  # name -> scripts whose references name it
        for sp, rec in snap.scripts.items():
            par = parent_path(sp)
            while par:
                self.inside[par].append(sp)
                par = parent_path(par)
            for ref in rec.get("name_refs", []):
                self.mentions[ref].add(sp)
        self._sig_groups: dict[tuple, Counter] = {}
        counts = [self.ref_count(p) for p, i in snap.by_path.items() if i["class"] not in SCRIPT_CLASSES]
        self.median_ref = median([c for c in counts if c > 0])

    # names and tokens
    def name(self, path: str) -> str:
        return self.snap.by_path[path].get("name") or leaf_name(path)

    def is_generic_name(self, path: str) -> bool:
        i = self.snap.by_path[path]
        n = self.name(path)
        return n == i["class"] or GENERIC_NAME.fullmatch(n) is not None

    def name_tokens(self, path: str) -> list[str]:
        t = self._tok.get(path)
        if t is None:
            i = self.snap.by_path[path]
            t = split_tokens(self.name(path)) + [x for tag in i.get("tags", []) for x in split_tokens(tag)]
            self._tok[path] = t
        return t

    def attr_key_tokens(self, path: str) -> set[str]:
        t = self._attr_tok.get(path)
        if t is None:
            t = {x for k in self.snap.by_path[path].get("attrs", {}) for x in split_tokens(k)}
            self._attr_tok[path] = t
        return t

    # references
    def referencing_scripts(self, path: str) -> set[str]:
        """Scripts that reference the instance: scripts attached to it (up to 3 levels below it; none for a plain Folder, which is only a container), scripts whose references name it,
        and scripts whose references name one of its ancestors (a script that loops over a folder references what is in it). Generic names such as 'Part' are not references."""
        snap = self.snap
        out: set[str] = set()
        if snap.by_path[path]["class"] not in ("Folder", "Configuration"):
            depth = path.count(SEP)
            out |= {sp for sp in self.inside.get(path, ()) if sp.count(SEP) - depth <= 3}
        cur = path
        while cur:
            if cur in snap.by_path and not self.is_generic_name(cur):
                out |= self.mentions.get(self.name(cur), set())
            cur = parent_path(cur)
        out.discard(path)
        return out

    def ref_count(self, path: str) -> int:
        if path not in self.snap.by_path or self.is_generic_name(path):
            return 0
        return len(self.mentions.get(self.name(path), ()))

    # repetition
    def signature(self, path: str) -> tuple:
        i = self.snap.by_path[path]
        return (i["class"], tuple(sorted(self.snap.by_path[c]["class"] for c in self.snap.children_of(path))))

    def n_similar(self, path: str, threshold: float) -> int:
        """Siblings with the same class signature and name-token Jaccard similarity above ``threshold`` (numbers in names ignored; two numbered names such as '1' and '2' are similar)."""
        key = (path, round(threshold, 6))
        hit = self._rep.get(key)
        if hit is not None:
            return int(hit)
        par = parent_path(path)
        sibs = self.snap.children_of(par)
        sig = self.signature(path)
        gk = (par, sig, round(threshold, 6))
        buckets = self._sig_groups.get(gk)
        if buckets is None:
            buckets = Counter(frozenset(token_set(self.name(s), drop_numbers=True)) for s in sibs if self.signature(s) == sig)
            self._sig_groups[gk] = buckets
        mine = frozenset(token_set(self.name(path), drop_numbers=True))
        n = sum(c for b, c in buckets.items() if jaccard(set(mine), set(b)) > threshold) - 1
        self._rep[key] = max(0, n)
        return max(0, n)

    # positions
    def pos(self, path: str) -> tuple | None:
        if path in self._pos:
            return self._pos[path]
        p = self.snap.by_path[path].get("pos")
        if p is None:
            for d in self.snap.descendants(path):
                q = self.snap.by_path[d].get("pos")
                if q is not None:
                    p = q
                    break
        self._pos[path] = tuple(p) if p else None
        return self._pos[path]

    def distance(self, a: str, b: str) -> float | None:
        pa, pb = self.pos(a), self.pos(b)
        if pa is None or pb is None:
            return None
        return math.dist(pa, pb)


_INDEX_CACHE: dict[int, PlaceIndex] = {}


def index_for(snap: Snapshot) -> PlaceIndex:
    """One PlaceIndex per loaded Snapshot object (the store hands out a cached Snapshot until the file changes)."""
    key = id(snap)
    idx = _INDEX_CACHE.get(key)
    if idx is None or idx.snap is not snap:
        if len(_INDEX_CACHE) > 8:
            _INDEX_CACHE.clear()
        idx = PlaceIndex(snap)
        _INDEX_CACHE[key] = idx
    return idx
