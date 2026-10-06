"""Cross-place tools: search several places of ONE project, compare two, and find code shared between places (and where the copies have drifted).

These never run by accident: they are separate tools, they only read stored snapshots (never Studio), they stay inside one project, and every hit carries its place prefix, snapshot id and
age. A place without a snapshot is reported, never guessed at.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import search as SE
from .graph import Graph
from .index import index_for
from .places import PlaceCtx
from .snapshot import SnapshotStore, staleness
from .util import prefixed


def _load(ctx: PlaceCtx):
    try:
        return SnapshotStore(ctx.wproject).load("latest")
    except FileNotFoundError:
        return None


def across(ctxs: list[PlaceCtx], query: str, k: int, stale_hours: float) -> dict:
    hits, missing, snaps = [], [], []
    for ctx in ctxs:
        snap = _load(ctx)
        if snap is None:
            missing.append(ctx.place_id)
            continue
        idx = index_for(snap)
        st = staleness(snap.taken_at, stale_hours)
        snaps.append({"place": ctx.place_id, "snapshot": snap.id, "taken_at": snap.taken_at, "stale": st["stale"]})
        exact = SE.exact_name_matches(idx, query)
        found = [(p, 1.0, "exact name") for p in exact[:k]] + [(h["path"], h["score"], "text match") for h in SE.hybrid(idx, query, k=k) if h["path"] not in exact]
        for p, score, why in found[:k]:
            hits.append({"place": ctx.place_id, "path": prefixed(ctx.place_id, p), "class": snap.by_path[p]["class"], "score": round(score, 3), "why": why})
    hits.sort(key=lambda h: (-h["score"], h["place"], h["path"]))
    return {"hits": hits[:k * max(1, len(ctxs))], "snapshots": snaps, "places_without_snapshot": missing, "places_searched": [c.place_id for c in ctxs if c.place_id not in missing]}


def _structure(snap) -> dict:
    roots = Counter(p.split("/")[0] for p in snap.by_path)
    classes = Counter(i["class"] for i in snap.instances)
    return {"roots": dict(roots), "classes": dict(classes)}


def compare(ca: PlaceCtx, cb: PlaceCtx) -> dict:
    sa, sb = _load(ca), _load(cb)
    if sa is None or sb is None:
        raise FileNotFoundError(f"both places need a snapshot: {ca.place_id}={'ok' if sa else 'none'}, {cb.place_id}={'ok' if sb else 'none'}")
    ga, gb = Graph(index_for(sa)), Graph(index_for(sb))
    ra = {r["name"]: r for r in ga.remote_table()}
    rb = {r["name"]: r for r in gb.remote_table()}
    ma = {p: s for p, s in sa.scripts.items() if s["class"] == "ModuleScript"}
    mb = {p: s for p, s in sb.scripts.items() if s["class"] == "ModuleScript"}
    drifted = [{"path": p, "hash_a": (ma[p]["hash"] or "unread")[:12], "hash_b": (mb[p]["hash"] or "unread")[:12]} for p in sorted(set(ma) & set(mb))
               if ma[p]["hash"] != mb[p]["hash"]]
    sta, stb = _structure(sa), _structure(sb)
    roots = sorted(set(sta["roots"]) | set(stb["roots"]))
    classes = sorted(set(sta["classes"]) | set(stb["classes"]))
    return {
        "a": {"place": ca.place_id, "snapshot": sa.id, "taken_at": sa.taken_at}, "b": {"place": cb.place_id, "snapshot": sb.id, "taken_at": sb.taken_at},
        "remotes": {"only_a": sorted(set(ra) - set(rb)), "only_b": sorted(set(rb) - set(ra)), "both": len(set(ra) & set(rb)),
                    "class_differs": sorted(n for n in set(ra) & set(rb) if ra[n]["classes"] != rb[n]["classes"])},
        "modules": {"only_a": [prefixed(ca.place_id, p) for p in sorted(set(ma) - set(mb))], "only_b": [prefixed(cb.place_id, p) for p in sorted(set(mb) - set(ma))],
                    "same_path_identical": sum(1 for p in set(ma) & set(mb) if ma[p]["hash"] and ma[p]["hash"] == mb[p]["hash"]), "same_path_drifted": drifted},
        "structure": {"roots_differ": {r: [sta["roots"].get(r, 0), stb["roots"].get(r, 0)] for r in roots if sta["roots"].get(r, 0) != stb["roots"].get(r, 0)},
                      "class_counts_differ": {c: [sta["classes"].get(c, 0), stb["classes"].get(c, 0)] for c in classes
                                              if sta["classes"].get(c, 0) != stb["classes"].get(c, 0) and c not in ("Folder",)}},
    }


def shared_code(ctxs: list[PlaceCtx], min_places: int = 2) -> dict:
    """Scripts that exist in several places. ``identical``: same content hash. ``drifted``: same module name (and class) in several places with more than one content hash."""
    by_hash: dict[str, list[tuple[str, str]]] = defaultdict(list)
    by_name: dict[tuple, list[tuple[str, str, str | None, dict]]] = defaultdict(list)
    skipped, searched, snaps = [], [], []
    for ctx in ctxs:
        snap = _load(ctx)
        if snap is None:
            skipped.append(ctx.place_id)
            continue
        searched.append(ctx.place_id)
        snaps.append({"place": ctx.place_id, "snapshot": snap.id, "taken_at": snap.taken_at})
        idx = index_for(snap)
        for p, s in snap.scripts.items():
            if s["hash"] is None or s.get("source_cut"):
                continue  # unread or cut sources cannot be compared by content
            by_hash[s["hash"]].append((ctx.place_id, p))
            by_name[(idx.name(p), s["class"])].append((ctx.place_id, p, s["hash"], s))
    identical = [{"hash": h[:12], "places": sorted({pl for pl, _ in locs}), "copies": [prefixed(pl, p) for pl, p in sorted(locs)]} for h, locs in by_hash.items()
                 if len({pl for pl, _ in locs}) >= min_places]
    drifted = []
    for (name, cls), copies in by_name.items():
        places = {pl for pl, *_ in copies}
        hashes = {h for _, _, h, _ in copies}
        if len(places) >= min_places and len(hashes) > 1:
            variants = []
            for h in sorted(hashes):
                members = [(pl, p, s) for pl, p, hh, s in copies if hh == h]
                variants.append({"hash": h[:12], "places": sorted({pl for pl, _, _ in members}), "paths": [prefixed(pl, p) for pl, p, _ in sorted(members, key=lambda m: (m[0], m[1]))],
                                 "length": members[0][2]["length"], "functions": members[0][2].get("functions", [])[:8]})
            first = copies[0][3]
            differs = []
            for h in sorted(hashes)[1:]:
                other = next(s for _, _, hh, s in copies if hh == h)
                fa, fb = set(first.get("functions", [])), set(other.get("functions", []))
                if fa != fb:
                    differs.append({"functions_only_in_first": sorted(fa - fb)[:6], "functions_only_in_other": sorted(fb - fa)[:6]})
            drifted.append({"name": name, "class": cls, "variants": variants, "structural_difference": differs[:3]})
    identical.sort(key=lambda x: (-len(x["places"]), x["hash"]))
    drifted.sort(key=lambda x: (-len(x["variants"]), x["name"]))
    return {"identical": identical, "drifted": drifted, "places_searched": searched, "places_without_snapshot": skipped, "snapshots": snaps}
