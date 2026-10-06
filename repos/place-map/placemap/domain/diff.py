"""What changed between two snapshots of the same place: instances, scripts (by content hash) and remotes. Renames are not detected (a rename shows as one removal and one addition)."""

from __future__ import annotations

from .graph import Graph
from .index import index_for
from .snapshot import Snapshot

POS_EPS = 0.5


def _attrs_changed(a: dict, b: dict) -> list[str]:
    keys = set(a) | set(b)
    return sorted(k for k in keys if a.get(k) != b.get(k))


def _set_diff(x: list, y: list) -> dict | None:
    sx, sy = set(x), set(y)
    if sx == sy:
        return None
    return {"added": sorted(sy - sx), "removed": sorted(sx - sy)}


def diff(a: Snapshot, b: Snapshot, *, only_under: str | None = None) -> dict:
    """``a`` is the older snapshot, ``b`` the newer. ``only_under`` limits the comparison to one subtree."""
    def keep(p: str) -> bool:
        return only_under is None or p == only_under or p.startswith(only_under + "/")

    ia = {p: i for p, i in a.by_path.items() if keep(p)}
    ib = {p: i for p, i in b.by_path.items() if keep(p)}
    added = sorted(set(ib) - set(ia))
    removed = sorted(set(ia) - set(ib))
    changed = []
    for p in sorted(set(ia) & set(ib)):
        x, y = ia[p], ib[p]
        what = []
        if x["class"] != y["class"]:
            what.append(f"class {x['class']} -> {y['class']}")
        ak = _attrs_changed(x.get("attrs", {}), y.get("attrs", {}))
        if ak:
            what.append("attributes: " + ", ".join(ak[:6]))
        if x.get("pos") and y.get("pos") and max(abs(i - j) for i, j in zip(x["pos"], y["pos"])) > POS_EPS:
            what.append("moved")
        if sorted(x.get("tags", [])) != sorted(y.get("tags", [])):
            what.append("tags")
        if what:
            changed.append({"path": p, "what": what})
    sa = {p: s for p, s in a.scripts.items() if keep(p)}
    sb = {p: s for p, s in b.scripts.items() if keep(p)}
    s_added, s_removed = sorted(set(sb) - set(sa)), sorted(set(sa) - set(sb))
    s_changed = []
    for p in sorted(set(sa) & set(sb)):
        x, y = sa[p], sb[p]
        if x.get("hash") is None or y.get("hash") is None:
            if x.get("fp") != y.get("fp") and x.get("fp") and y.get("fp"):
                s_changed.append({"path": p, "what": ["content changed (fingerprint; source not read)"]})
            continue
        if x["hash"] == y["hash"]:
            continue
        what = ["content changed"]
        rq = _set_diff([str(r["chain"][-1] if r.get("chain") else r["expr"]) for r in x["requires"]], [str(r["chain"][-1] if r.get("chain") else r["expr"]) for r in y["requires"]])
        if rq:
            what.append(f"requires +{rq['added']} -{rq['removed']}")
        for kind in ("remotes_fired", "remotes_handled"):
            d = _set_diff([r["name"] for r in x[kind]], [r["name"] for r in y[kind]])
            if d:
                what.append(f"{kind.replace('remotes_', 'remotes ')} +{d['added']} -{d['removed']}")
        fd = _set_diff(x.get("functions", []), y.get("functions", []))
        if fd:
            what.append(f"functions +{fd['added'][:4]} -{fd['removed'][:4]}")
        s_changed.append({"path": p, "what": what})
    ra = {r["name"] for r in Graph(index_for(a)).remote_table()}
    rb = {r["name"] for r in Graph(index_for(b)).remote_table()}
    res = {"from": {"id": a.id, "taken_at": a.taken_at}, "to": {"id": b.id, "taken_at": b.taken_at},
           "counts": {"instances_added": len(added), "instances_removed": len(removed), "instances_changed": len(changed), "scripts_added": len(s_added),
                      "scripts_removed": len(s_removed), "scripts_changed": len(s_changed), "remotes_added": len(rb - ra), "remotes_removed": len(ra - rb)},
           "instances_added": added, "instances_removed": removed, "instances_changed": changed, "scripts_added": s_added, "scripts_removed": s_removed,
           "scripts_changed": s_changed, "remotes_added": sorted(rb - ra), "remotes_removed": sorted(ra - rb)}
    res["identical"] = not any(res["counts"].values())
    if a.data.get("roots") != b.data.get("roots") or not (a.data.get("full", True) and b.data.get("full", True)):
        res["note"] = "the snapshots cover different roots or one is partial: removals may only mean 'not collected'"
    return res
