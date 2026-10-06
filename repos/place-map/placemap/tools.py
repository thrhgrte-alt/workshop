"""MCP tools for the place map.

Rules every tool follows: it needs ``project_id`` and ``place_id`` (or, for reads, the hub's ``studios`` output so the place that is open in Studio can be used) and refuses without them,
never guesses; every answer carries the place, the snapshot id and time and a stale flag; output is a one-line ``summary`` first, ranked results capped (default 10), details only with
``detail=true``; write tools default to ``dry_run=true``. The server never talks to Studio: ``plan_refresh`` EMITS Luau for the hub's ``roblox_studio_execute_luau`` (after matching the open
Studio place to the named one), and ``ingest_snapshot`` reads what the hub returned. Tools whose description starts with ``[rare]`` can be left disabled (``PLACEMAP_DISABLE_GROUPS=rare``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import learning_params as LP
from .domain import cache as CA, collector, diff as DF, ingest as IN, labels as LB, multi as MU, places, search as SE
from .domain import roles as R
from .domain.snapshot import SnapshotStore, staleness
from .domain.util import PLACE_SEP, SEP, clean_path_input, now_iso, parse_iso, prefixed, unescape_segment
from .domain.view import PlaceView
from .guide_adapter import ToolSpec, config, dryrun, feedback, observe, scope as S

RARE = ("list_snapshots", "list_roles", "prune_snapshots", "undo_label", "compare_places", "find_shared_code")
MAX_FILE_BYTES = 60_000_000
HUB = {"execute": "roblox_studio_execute_luau", "tree": "roblox_studio_search_game_tree", "script": "roblox_studio_script_read", "studios": "list_roblox_studios"}


def make_tools(project) -> list[ToolSpec]:
    def ctx_of(project_id, place_id, studios=None):
        return places.resolve(project, project_id, place_id, studios=studios)

    def view_of(project_id, place_id, studios=None, snapshot="latest") -> PlaceView:
        ctx = ctx_of(project_id, place_id, studios)
        try:
            return PlaceView.load(ctx, snapshot)
        except FileNotFoundError as exc:
            raise ValueError(f"{exc} (place {ctx.project_id}/{ctx.place_id})") from exc

    def global_proj():
        return S.scoped_project(project, S.GLOBAL, S.GLOBAL)

    def ps_for(ctx):
        return LP.store(project, ctx.project_id)

    def obs_log() -> Any:
        return observe.RunLog(project.workspace / "learning" / "observations.jsonl")

    def head(summary: str, v: PlaceView | places.PlaceCtx, **rest: Any) -> dict[str, Any]:
        return {"summary": summary, **v.head(), **rest}

    def short(s: str, n: int = 160) -> str:
        return s if len(s) <= n else s[: n - 3] + "..."

    # --- reads ----------------------------------------------------------------------------------------------------------------------
    def find_in_place(query: str, project_id: str | None = None, place_id: str | None = None, studios: list | None = None, role: str | None = None, kind: str | None = None,
                      under: str | None = None, k: int | None = None, detail: bool = False) -> dict[str, Any]:
        """Find things in ONE place: a path, a landmark alias, a name, a role ('vendor') or text. Returns exact matches only plus 'not selected' near matches; asks when unsure."""
        v = view_of(project_id, place_id, studios)
        res = v.resolve(query, role=role, kind=kind, under=under, k=k)
        mode = res.pop("mode")
        sel, not_sel = res.get("selected", []), res.get("not_selected", [])
        if mode == "role":
            line = f"{res['role']}: {len(sel)} selected, {len(not_sel)} borderline not selected"
        elif mode == "search":
            line = f"search: {len(sel)} selected, {len(not_sel)} ranked match(es) not selected"
        else:
            line = f"{mode}: {len(sel)} selected, {len(not_sel)} near match(es) not selected"
        if res.get("ask"):
            line += " - asking"
        h = v.head()["snapshot"]
        line += f" (snapshot {h['age_hours']}h old{', STALE' if h['stale'] else ''})"
        if not detail and mode != "path":
            for e in sel + not_sel:
                e.pop("script", None)
        return head(line, v, mode=mode, **res)

    def get_path_info(path: str, project_id: str | None = None, place_id: str | None = None, studios: list | None = None, detail: bool = False) -> dict[str, Any]:
        """Exact facts about one path: class, attributes, children, script summary (remotes, requires, DataStore keys), the roles it plays with scores."""
        v = view_of(project_id, place_id, studios)
        if PLACE_SEP in path and path.split(PLACE_SEP, 1)[0] != v.ctx.place_id:
            raise ValueError(f"the path names place '{path.split(PLACE_SEP, 1)[0]}' but the active place is '{v.ctx.place_id}'; get_path_info reads one place")
        p = clean_path_input(path)
        inst = v.snap.by_path.get(p)
        if inst is None:
            near = [x["path"] for x in SE.near_matches(v.idx, p.replace(SEP, " "), set(), 3)]
            raise ValueError(f"no instance at '{p}' in snapshot {v.snap.id} of {v.ctx.place_id}" + (f"; similar: {near}" if near else "") + " (the snapshot may be stale: plan_refresh)")
        kids = v.snap.children_of(p)
        attrs = inst.get("attrs", {})
        out: dict[str, Any] = {"path": v.pp(p), "class": inst["class"], "name": inst["name"], "children": len(kids) if len(kids) > 0 else inst.get("n_children", 0),
                               "child_names": [v.idx.name(c) for c in kids[: (60 if detail else 10)]], "attributes": dict(list(attrs.items())[: (60 if detail else 8)])}
        if inst.get("pos"):
            out["position"] = inst["pos"]
        if inst.get("tags"):
            out["tags"] = inst["tags"]
        rec = v.snap.scripts.get(p)
        if rec:
            lim = 60 if detail else 6
            out["script"] = {"kind": rec["kind"], "hash": (rec["hash"] or "unread")[:12], "bytes": rec["length"], "summary": rec["summary"],
                             "remotes_fired": sorted({r["name"] for r in rec["remotes_fired"]})[:lim], "remotes_handled": sorted({r["name"] for r in rec["remotes_handled"]})[:lim],
                             "requires": [{"module": e["target"] and v.pp(e["target"]), "expr": e["expr"], **({"matched_by": e["matched_by"]} if e["matched_by"] == "name" else {}),
                                           **({"unresolved": e["unresolved"]} if e.get("unresolved") else {})} for e in v.graph.requires.get(p, [])][:lim],
                             "datastores": rec["datastores"], "datastore_keys": rec["datastore_keys"][:lim], "needs_read": rec["needs_read"]}
            if detail:
                out["script"]["functions"] = rec["functions"]
                out["script"]["services"] = rec["services"]
        roles = v.path_roles(p, include_below=detail)
        out["roles"] = roles[: (8 if detail else 3)]
        if detail:
            out["role_features"] = {r["role"]: v.scorer.score_one(r["role"], p) for r in roles[:3]} if inst["class"] not in (v.limits.get("scoring") or {}).get("skip_classes", []) else {}
        n_roles = [r for r in roles if r["band"] != "below"]
        line = f"{v.pp(p)} is a {inst['class']}" + (f" ({rec['kind']} script)" if rec else "") + (f"; plays {n_roles[0]['role']} ({n_roles[0]['band']}, {n_roles[0]['score']})" if n_roles else "; plays no role")
        return head(line, v, **out)

    def who_uses(path: str, project_id: str | None = None, place_id: str | None = None, studios: list | None = None, depth: int = 2, detail: bool = False) -> dict[str, Any]:
        """Callers and requirers of a path: scripts that require a module (transitively: what breaks if it changes), fire/handle a remote, or mention an instance by name."""
        v = view_of(project_id, place_id, studios)
        p = clean_path_input(path)
        res = v.graph.who_uses(p, max(1, min(depth, 6)))
        cap = 200 if detail else v.cap
        out: dict[str, Any] = {"path": v.pp(p), "class": res["class"]}
        n = 0
        if "requirers" in res:
            rq = res["requirers"]
            n += len(rq)
            out["requirers"] = [{"script": v.pp(r["script"]), "level": r["level"], **({"matched_by": "name"} if r["matched_by"] == "name" else {})} for r in rq[:cap]]
            out["breaks_if_changed"] = len(rq)
            if len(rq) > cap:
                out["more_requirers"] = len(rq) - cap
        for key in ("fired_by", "handled_by"):
            if key in res:
                n += len(res[key])
                out[key] = [{"script": v.pp(r["script"]), "via": r["via"]} for r in res[key][:cap]]
        for key in ("mentioned_by", "inside"):
            if res.get(key):
                n += len(res[key])
                out[key] = [v.pp(x) for x in res[key][:cap]]
                if len(res[key]) > cap:
                    out["more_" + key] = len(res[key]) - cap
        unresolved = [e for e in v.graph.requires.get(p, []) if e["target"] is None] if p in v.snap.scripts else []
        line = f"{n} use(s) of {v.pp(p)}" + (f"; changing it breaks {out['breaks_if_changed']} script(s) (depth {depth})" if "breaks_if_changed" in out else "")
        return head(line, v, **out, **({"note": "requires by name are weaker evidence than by path"} if any(r.get("matched_by") for r in out.get("requirers", [])) else {}),
                    **({"unresolved_requires_in_this_script": len(unresolved)} if unresolved else {}))

    def list_remotes(project_id: str | None = None, place_id: str | None = None, studios: list | None = None, name: str | None = None, only_issues: bool = False,
                     detail: bool = False) -> dict[str, Any]:
        """Remotes in the place: where defined, which scripts fire and which handle each. Flags remotes nobody handles or fires, or that are not defined in the snapshot."""
        v = view_of(project_id, place_id, studios)
        rows = v.graph.remote_table()
        if name:
            rows = [r for r in rows if r["name"].lower() == name.lower()]
            if not rows:
                raise ValueError(f"no remote named '{name}' in {v.snap.id}; known: {[r['name'] for r in v.graph.remote_table()][:12]}")
        if only_issues:
            rows = [r for r in rows if r["issues"]]
        cap = len(rows) if detail else v.cap
        out = []
        for r in rows[:cap]:
            e = {"name": r["name"], "class": r["classes"], "defined_at": [v.pp(p) for p in r["paths"]][:3], "fired_by": [v.pp(x["script"]) for x in r["fired_by"]][: (50 if detail else 3)],
                 "handled_by": [v.pp(x["script"]) for x in r["handled_by"]][: (50 if detail else 3)], "n_fired_by": len(r["fired_by"]), "n_handled_by": len(r["handled_by"])}
            if r["issues"]:
                e["issues"] = r["issues"]
            out.append(e)
        n_issue = sum(1 for r in v.graph.remote_table() if r["issues"])
        line = f"{len(rows)} remote(s)" + (f", {n_issue} with issues" if n_issue else "")
        return head(line, v, remotes=out, **({"more": len(rows) - cap} if len(rows) > cap else {}))

    def show_dependencies(path: str, project_id: str | None = None, place_id: str | None = None, studios: list | None = None, depth: int = 2, detail: bool = False) -> dict[str, Any]:
        """What a script depends on: modules it requires (transitively), remotes it fires or handles, DataStore keys it touches, unresolved requires and cycles."""
        v = view_of(project_id, place_id, studios)
        p = clean_path_input(path)
        d = v.graph.dependencies(p, max(1, min(depth, 6)))
        cap = 200 if detail else v.cap
        out = {"path": v.pp(p), "modules": [{"module": v.pp(m["module"]), "level": m["level"], "from": v.pp(m["from"]), **({"matched_by": "name"} if m["matched_by"] == "name" else {})} for m in d["modules"][:cap]],
               "remotes_fired": d["remotes_fired"], "remotes_handled": d["remotes_handled"], "datastores": d["datastores"], "datastore_keys": d["datastore_keys"],
               "unresolved": d["unresolved"][:cap], "cycles": [{"from": v.pp(c["from"]), "to": v.pp(c["to"])} for c in d["cycles"]]}
        if len(d["modules"]) > cap:
            out["more_modules"] = len(d["modules"]) - cap
        line = f"{v.pp(p)} requires {len(d['modules'])} module(s) (depth {depth}), fires {len(d['remotes_fired'])} and handles {len(d['remotes_handled'])} remote(s)" + \
               (f", {len(d['unresolved'])} unresolved require(s)" if d["unresolved"] else "") + (", CYCLE" if d["cycles"] else "")
        return head(line, v, **out)

    def summarize_area(path: str, project_id: str | None = None, place_id: str | None = None, studios: list | None = None, detail: bool = False) -> dict[str, Any]:
        """Summarise one area (a folder, model or service): counts, scripts with their cached summaries, remotes defined there, landmarks inside. Summaries are cached by script hash."""
        v = view_of(project_id, place_id, studios)
        p = clean_path_input(path)
        if p not in v.snap.by_path:
            raise ValueError(f"no instance at '{p}' in snapshot {v.snap.id}")
        under = [p] + list(v.snap.descendants(p))
        classes: dict[str, int] = {}
        for q in under:
            c = v.snap.by_path[q]["class"]
            classes[c] = classes.get(c, 0) + 1
        scripts = [v.snap.scripts[q] for q in under if q in v.snap.scripts]
        scripts.sort(key=lambda s: (-len(v.graph.dependents.get(s["path"], [])), s["path"]))
        cap = len(scripts) if detail else v.cap
        remotes = [v.pp(q) for q in under if v.snap.by_path[q]["class"] in ("RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent", "BindableEvent", "BindableFunction")]
        lm = []
        for rid in v.roles:
            for r in v.scorer.run()[rid]:
                if r["band"] == "confident" and (r["path"] == p or r["path"].startswith(p + SEP)) and len(lm) < (40 if detail else 6):
                    lm.append({"path": v.pp(r["path"]), "role": rid, "score": r["score"]})
        out = {"path": v.pp(p), "instances": len(under), "classes": dict(sorted(classes.items(), key=lambda kv: -kv[1])[: (30 if detail else 6)]),
               "scripts": [{"path": v.pp(s["path"]), "kind": s["kind"], "summary": s["summary"], "used_by": len(v.graph.dependents.get(s["path"], []))} for s in scripts[:cap]],
               "remotes_defined": remotes[: (60 if detail else 8)], "landmarks": lm, "scripts_needing_read": sum(1 for s in scripts if s["needs_read"])}
        if len(scripts) > cap:
            out["more_scripts"] = len(scripts) - cap
        return head(f"{v.pp(p)}: {len(under)} instance(s), {len(scripts)} script(s), {len(remotes)} remote(s), {len(lm)} landmark(s)", v, **out)

    def diff_snapshots(project_id: str | None = None, place_id: str | None = None, a: str = "previous", b: str = "latest", under: str | None = None, detail: bool = False) -> dict[str, Any]:
        """What changed between two snapshots of this place (default previous -> latest): instances, scripts by content hash, remotes. a/b = snapshot id, 'latest' or 'previous'."""
        ctx = ctx_of(project_id, place_id)
        store = SnapshotStore(ctx.wproject)
        try:
            sa, sb = store.load(a), store.load(b)
        except FileNotFoundError as exc:
            raise ValueError(f"{exc} (place {ctx.project_id}/{ctx.place_id})") from exc
        if sa.id == sb.id:
            raise ValueError(f"'{a}' and '{b}' are the same snapshot ({sa.id}); name two different snapshots (list_snapshots)")
        res = DF.diff(sa, sb, only_under=clean_path_input(under) if under else None)
        cap = 500 if detail else 10
        pp = lambda p: prefixed(ctx.place_id, p)  # noqa: E731
        c = res["counts"]
        out: dict[str, Any] = {"from": res["from"], "to": res["to"], "counts": c,
                               "instances_added": [pp(p) for p in res["instances_added"][:cap]], "instances_removed": [pp(p) for p in res["instances_removed"][:cap]],
                               "instances_changed": [{"path": pp(x["path"]), "what": x["what"]} for x in res["instances_changed"][:cap]],
                               "scripts_added": [pp(p) for p in res["scripts_added"][:cap]], "scripts_removed": [pp(p) for p in res["scripts_removed"][:cap]],
                               "scripts_changed": [{"path": pp(x["path"]), "what": x["what"]} for x in res["scripts_changed"][:cap]],
                               "remotes_added": res["remotes_added"][:cap], "remotes_removed": res["remotes_removed"][:cap]}
        hidden = sum(max(0, len(res[k]) - cap) for k in ("instances_added", "instances_removed", "instances_changed", "scripts_added", "scripts_removed", "scripts_changed"))
        if hidden:
            out["more"] = f"{hidden} more entries; pass detail=true"
        if res.get("note"):
            out["note"] = res["note"]
        st = staleness(sb.taken_at, float(LP.values_for(project, ctx.project_id, ctx.place_id)("snapshots.stale_hours")))
        st = {"taken_at": st["taken_at"], "age_hours": st["age_hours"], "stale": st["stale"]}
        line = "no differences" if res["identical"] else (f"{c['scripts_changed']} script(s) changed, {c['scripts_added']} added, {c['scripts_removed']} removed; {c['instances_added']} instance(s) added, "
                                                        f"{c['instances_removed']} removed, {c['instances_changed']} changed; remotes +{c['remotes_added']}/-{c['remotes_removed']}")
        return {"summary": line + f" ({sa.id} -> {sb.id})", **ctx.head(), "snapshot": {"id": sb.id, **st}, **out}

    def find_past_corrections(request: str, project_id: str | None = None, place_id: str | None = None, k: int = 3, include_global: bool = True) -> dict[str, Any]:
        """Past corrections for THIS place (plus ones marked global) relevant to a request. Read before planning."""
        ctx = ctx_of(project_id, place_id)
        rows = [{**r, "scope": "place"} for r in feedback.find_past_corrections(ctx.wproject, request, k, scope=ctx.scope)]
        if include_global:
            rows += [{**r, "scope": "global"} for r in feedback.find_past_corrections(global_proj(), request, k, scope=S.Scope.make_global())]
        rows = sorted(rows, key=lambda r: -r["score"])[:k]
        return {"summary": f"{len(rows)} correction(s) for {ctx.project_id}/{ctx.place_id}", **ctx.head(), "corrections": rows}

    def get_style_brief(project_id: str | None = None, place_id: str | None = None, focus: list[str] | None = None, max_chars: int = 1500) -> dict[str, Any]:
        """How to phrase answers: global style plus this project's overrides (projects/<id>/style.yaml). Short."""
        reg = places.load_registry(project)
        sc = S.require_scope(project_id, place_id, registry=reg)
        style = S.load_layered_style(project, sc)
        return {"summary": f"style brief for {sc.label}", "project_id": sc.project_id, "place_id": sc.place_id, "brief": config.style_brief(style, focus or (), max_chars),
                "correction_dimensions": style.get("correction_dimensions", []), "layers": sorted({v for v in style.get("_layers", {}).values()})}

    def list_places(project_id: str | None = None) -> dict[str, Any]:
        """The registry (projects.yaml): project, place alias, Roblox place id, Studio name, whether a snapshot exists. Reads no place data."""
        rows = places.known(project)
        if project_id:
            rows = [r for r in rows if r["project_id"] == project_id]
            if not rows:
                raise S.ScopeError(f"unknown project_id '{project_id}'. Known: {sorted({r['project_id'] for r in places.known(project)})}")
        out = []
        for r in rows:
            ctx = places.resolve(project, r["project_id"], r["place_id"])
            snaps = SnapshotStore(ctx.wproject).list()
            out.append({**r, "snapshots": len(snaps), "latest": snaps[0]["id"] if snaps else None, "latest_taken_at": snaps[0]["taken_at"] if snaps else None})
        return {"summary": f"{len(out)} place(s) registered", "places": out}

    def list_candidates(project_id: str | None = None, place_id: str | None = None, studios: list | None = None, role: str | None = None, k: int | None = None) -> dict[str, Any]:
        """Borderline role candidates (between the candidate and confident bands) that the user should confirm or reject with label_landmark."""
        v = view_of(project_id, place_id, studios)
        k = k or v.cap
        roles = [role] if role else sorted(v.roles)
        if role and role not in v.roles:
            raise ValueError(f"unknown role '{role}'. Known roles: {sorted(v.roles)}")
        act = v.labels.active()
        out = []
        for rid in roles:
            for r in v.scorer.run()[rid]:
                if r["band"] == "candidate" and (r["path"], rid) not in act:
                    row = v.scorer.score_one(rid, r["path"])
                    out.append({"role": rid, "path": v.pp(r["path"]), "score": r["score"], "why": R.explain(row), "against": R.negatives(row)})
        out.sort(key=lambda e: (-e["score"], e["path"]))
        return head(f"{len(out)} borderline candidate(s) to confirm or reject" + (f" (showing {k})" if len(out) > k else ""), v, candidates=out[:k], **({"more": len(out) - k} if len(out) > k else {}))

    def list_snapshots(project_id: str | None = None, place_id: str | None = None) -> dict[str, Any]:
        """[rare] Stored snapshots of this place (latest first) and how many a prune would remove."""
        ctx = ctx_of(project_id, place_id)
        rows = SnapshotStore(ctx.wproject).list()
        keep = int(LP.values_for(project, ctx.project_id, ctx.place_id)("snapshots.keep_older"))
        return {"summary": f"{len(rows)} snapshot(s); {max(0, len(rows) - 1 - keep)} over the keep limit ({keep} older)", **ctx.head(),
                "snapshots": [{k_: r[k_] for k_ in ("id", "taken_at", "full", "instances", "scripts")} for r in rows[:20]]}

    def list_roles(project_id: str | None = None, place_id: str | None = None, role: str | None = None) -> dict[str, Any]:
        """[rare] Roles, their features and the weights and bands IN FORCE for this place (default, learned project value, or global) with where each came from."""
        ctx = ctx_of(project_id, place_id)
        roles = R.load_roles(project, ctx.project_id)
        pv = LP.values_for(project, ctx.project_id, ctx.place_id)
        out = {}
        for rid, r in roles.items():
            if role and rid != role:
                continue
            out[rid] = {"description": r.description, "origin": r.source,
                        "features": {fid: {"fn": f.fn, "weight": pv(f"weight.{rid}.{fid}"), "default": f.w, "source": pv.snapshot[f"weight.{rid}.{fid}"]["source"]} for fid, f in r.features.items()},
                        "bands": {b: pv(f"band.{rid}.{b}") for b in ("confident", "candidate")}}
        if role and role not in out:
            raise ValueError(f"unknown role '{role}'. Known roles: {sorted(roles)}")
        return {"summary": f"{len(out)} role(s); weights and bands are placeholders until you confirm landmarks", **ctx.head(), "roles": out}

    # --- cross place reads ----------------------------------------------------------------------------------------------------------
    def find_across_places(query: str, project_id: str | None = None, place_ids: list | None = None, k: int = 5) -> dict[str, Any]:
        """Search several places of ONE project (default all) and label every hit with its place. Use find_in_place for the active place."""
        if not project_id:
            raise S.ScopeError("project_id is required (cross-place tools stay inside one project)")
        base = places.resolve(project, project_id, _first_place(project_id))
        ctxs = _select(base, place_ids)
        res = MU.across(ctxs, query, max(1, min(k, 20)), float(LP.values_for(project, project_id, None)("snapshots.stale_hours")))
        stale = [x["place"] for x in res["snapshots"] if x["stale"]]
        return {"summary": f"{len(res['hits'])} hit(s) across {len(res['places_searched'])} place(s)" + (f"; no snapshot for {res['places_without_snapshot']}" if res["places_without_snapshot"] else "")
                + (f"; STALE snapshot for {stale}: plan_refresh them" if stale else ""),
                "project_id": project_id, "cross_place": True, **res}

    def _first_place(project_id: str) -> str:
        reg = places.load_registry(project)
        pe = reg.project(project_id)
        if not pe.places:
            raise S.ScopeError(f"project '{project_id}' has no places")
        return pe.places[0].place_id

    def _select(base: places.PlaceCtx, wanted: list | None) -> list[places.PlaceCtx]:
        sib = places.sibling_places(base)
        if not wanted:
            return sib
        by_id = {c.place_id: c for c in sib}
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            raise S.ScopeError(f"place(s) {unknown} are not in project '{base.project_id}' (places: {sorted(by_id)})")
        return [by_id[w] for w in wanted]

    def compare_places(project_id: str | None = None, place_a: str | None = None, place_b: str | None = None, detail: bool = False) -> dict[str, Any]:
        """[rare] Differences between two places of one project: remotes, modules (same path, different content), structure. Read-only."""
        if not project_id or not place_a or not place_b:
            raise S.ScopeError("compare_places needs project_id, place_a and place_b (it will not guess)")
        if place_a == place_b:
            raise ValueError("place_a and place_b are the same place")
        ca, cb = places.resolve(project, project_id, place_a), places.resolve(project, project_id, place_b)
        try:
            res = MU.compare(ca, cb)
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        cap = 100 if detail else 8
        cut = lambda lst: lst[:cap]  # noqa: E731
        out = {"a": res["a"], "b": res["b"],
               "remotes": {**{k_: (cut(v_) if isinstance(v_, list) else v_) for k_, v_ in res["remotes"].items()}},
               "modules": {"only_a": cut(res["modules"]["only_a"]), "only_b": cut(res["modules"]["only_b"]), "same_path_identical": res["modules"]["same_path_identical"],
                           "same_path_drifted": cut(res["modules"]["same_path_drifted"])},
               "structure": {"roots_differ": dict(list(res["structure"]["roots_differ"].items())[:cap]), "class_counts_differ": dict(list(res["structure"]["class_counts_differ"].items())[:cap])}}
        line = (f"{place_a} vs {place_b}: remotes only in a {len(res['remotes']['only_a'])}/only in b {len(res['remotes']['only_b'])}, modules drifted {len(res['modules']['same_path_drifted'])}, "
                f"modules only in a {len(res['modules']['only_a'])}/only in b {len(res['modules']['only_b'])}")
        return {"summary": line, "project_id": project_id, "cross_place": True, **out}

    def find_shared_code(project_id: str | None = None, place_ids: list | None = None, min_places: int = 2, detail: bool = False) -> dict[str, Any]:
        """[rare] Scripts present in several places of one project, matched by content hash; reports where copies of the same module have drifted (different hashes)."""
        if not project_id:
            raise S.ScopeError("project_id is required (cross-place tools stay inside one project)")
        base = places.resolve(project, project_id, _first_place(project_id))
        res = MU.shared_code(_select(base, place_ids), max(2, min_places))
        cap = 100 if detail else 8
        return {"summary": f"{len(res['identical'])} shared (identical) script group(s), {len(res['drifted'])} drifted module(s) across {len(res['places_searched'])} place(s)",
                "project_id": project_id, "cross_place": True, "identical": res["identical"][:cap], "drifted": res["drifted"][:cap],
                "places_searched": res["places_searched"], "places_without_snapshot": res["places_without_snapshot"], "snapshots": res["snapshots"],
                **({"more": len(res["identical"]) + len(res["drifted"]) - 2 * cap} if len(res["identical"]) > cap or len(res["drifted"]) > cap else {})}

    # --- writes ---------------------------------------------------------------------------------------------------------------------
    def plan_refresh(project_id: str | None = None, place_id: str | None = None, studios: list | None = None, roots: list | None = None, scripts: list | None = None,
                     emit: str = "return", incremental: bool = True, dry_run: bool = True) -> dict[str, Any]:
        """Luau for the hub's execute_luau that dumps the tree and script sources as JSON. Needs studios (list_roblox_studios output); refuses if the open place is not the named one. roots/scripts = partial refresh."""
        ctx = ctx_of(project_id, place_id)
        match = places.match_open(ctx, studios)
        pv = LP.values_for(project, ctx.project_id, ctx.place_id)
        lim = LP.limits(project)
        full = not roots and not scripts
        use_roots = [clean_path_input(r) for r in (roots or [])] + [clean_path_input(s) for s in (scripts or [])] or list(lim["collector"]["default_roots"])
        known: dict[str, str] = {}
        warnings: list[str] = []
        store = SnapshotStore(ctx.wproject)
        base = store.list()
        if incremental and base:
            snap = store.load("latest")
            for p, s in snap.scripts.items():
                if s.get("fp") and not s.get("needs_read") and any(p == r or p.startswith(r + SEP) for r in use_roots):
                    known[p] = s["fp"]
            if len(known) > collector.MAX_KNOWN:
                warnings.append(f"{len(known)} known scripts exceed the {collector.MAX_KNOWN} fingerprints a script can carry: incremental mode off, all sources are re-read")
                known = {}
        elif incremental:
            warnings.append("no earlier snapshot: all script sources are read")
        limits = {k_: pv(f"collector.{k_}") for k_ in ("max_instances", "max_source_chars", "max_total_source_chars", "plain_leaf_cap", "attr_value_max_chars", "print_chunk_chars")}
        code = collector.build_collector(place_number=ctx.place_entry.roblox_place_id or 0, place_name=ctx.place_entry.studio_name, label=f"{ctx.project_id}/{ctx.place_id}", limits=limits,
                                         roots=use_roots, full=full, known_fps=known, emit=emit, skip_classes=lim["collector"].get("skip_classes", []), flt=IN.default_filter())
        plan = dryrun.Plan(f"collect {'all default roots' if full else ', '.join(use_roots[:3])} of {ctx.project_id}/{ctx.place_id} (read-only Luau, {len(code)} chars)")
        plan.add("execute_luau", HUB["execute"], f"run the returned `luau` in Studio edit mode ({match['name']!r}, matched by {match['matched_by']})")
        plan.add("ingest", "ingest_snapshot", "pass the result as data (or save it to a file and pass file=)")
        plan.warnings.extend(warnings)
        plan.warnings.append("parser_status schema_unverified: no real hub capture has been checked against this collector's output yet (see samples/README.md)")

        def applier(pl: dryrun.Plan) -> list[str]:
            rec = {"at": now_iso(), "project_id": ctx.project_id, "place_id": ctx.place_id, "roots": use_roots, "full": full, "emit": emit, "known_fingerprints": len(known), "luau_chars": len(code),
                   "studio": match, "fingerprint": pl.fingerprint()}
            f = ctx.wproject.workspace / "refresh_plans.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
            return [str(f)]

        res = dryrun.run_plan(plan, applier, apply=not dry_run, journal=None)
        return {"summary": plan.summary + ("; PLAN ONLY, nothing recorded" if dry_run else "; plan recorded"), **ctx.head(), "dry_run": dry_run, "plan": res.get("steps"), "warnings": plan.warnings,
                "studio": {k_: match[k_] for k_ in ("name", "studio_id", "matched_by") if k_ in match}, "roots": use_roots, "full": full, "incremental_known_scripts": len(known),
                "luau": code, "hub_calls": [{"tool": HUB["execute"], "args": {"code": "<the luau field>"}, "note": "run in edit mode; returns JSON text (emit=return) or prints PLACEMAP_CHUNK lines (emit=print)"}],
                "alternatives": _alternatives([clean_path_input(r) for r in roots] if roots else (use_roots if full else []), [clean_path_input(x) for x in scripts or []]), "next": "ingest_snapshot with the result (dry_run first)",
                "input_schema": "schema_unverified"}

    def _alternatives(roots: list[str], scripts: list[str]) -> list[dict]:
        """Cheaper hub calls that read less than the collector (tree only, or one script). Their argument and result shapes are UNVERIFIED: no real capture exists."""
        dotted = lambda p: ".".join(unescape_segment(x) for x in p.split(SEP))  # noqa: E731
        alts = [{"tool": HUB["tree"], "args": {"path": dotted(r)}, "reads": "tree only, no script text", "args_schema": "schema_unverified", "then": "ingest_snapshot source=search_game_tree"} for r in roots[:5]]
        alts += [{"tool": HUB["script"], "args": {"path": dotted(sp)}, "reads": "one script's source", "args_schema": "schema_unverified", "then": "ingest_snapshot source=script_read"} for sp in scripts[:20]]
        return alts

    def _allowed_file(path: str) -> Path:
        roots = [*project.allowed_roots, project.root / "samples", project.root / "examples"]
        extra = project.env("INGEST_PATHS")
        if extra:
            import os

            roots += [Path(x).expanduser().resolve() for x in extra.split(os.pathsep) if x]
        p = Path(path).expanduser()
        return S.resolve_inside(p if p.is_absolute() else project.root / p, roots)

    def ingest_snapshot(project_id: str | None = None, place_id: str | None = None, data: Any = None, file: str | None = None, source: str = "execute_luau", taken_at: str | None = None,
                        dry_run: bool = True) -> dict[str, Any]:
        """Parse JSON/text the hub returned (collector output, a tree, a text outline, script_read) into a snapshot for THIS place. dry_run shows what would be stored; refuses data collected in another place."""
        ctx = ctx_of(project_id, place_id)
        if taken_at is not None:
            try:
                parse_iso(taken_at)
            except ValueError as exc:
                raise ValueError(f"taken_at must be an ISO timestamp such as 2026-10-06T11:00:00+00:00 (got {taken_at!r})") from exc
        if (data is None) == (file is None):
            raise ValueError("pass exactly one of data (the hub's result) or file (a path to a saved result under workspace/, samples/ or examples/)")
        if file is not None:
            f = _allowed_file(file)
            if f.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"{f.name} is larger than {MAX_FILE_BYTES} bytes")
            data = f.read_text(encoding="utf-8")
        if source not in ("execute_luau", "search_game_tree", "script_read", "file", "other"):
            raise ValueError("source must be execute_luau, search_game_tree, script_read, file or other")
        parsed = IN.parse_input(data)
        if parsed.kind == "tree" and not parsed.instances:
            raise ValueError("nothing to store: no instances were recognised in this input (" + "; ".join(parsed.warnings[:2] or ["unknown format"]) + "). See samples/README.md for the accepted shapes")
        _check_same_place(ctx, parsed)
        store, sc_cache = SnapshotStore(ctx.wproject), CA.SummaryCache(project)
        base = None
        if store.list():
            base = store.load("latest")
        placeinfo = {"project_id": ctx.project_id, "place_id": ctx.place_id, "stable_id": ctx.stable_id, "roblox_place_id": ctx.place_entry.roblox_place_id, "studio_name": ctx.place_entry.studio_name,
                     "name_in_studio": parsed.meta.get("place_name")}
        built = IN.build_snapshot(parsed, placeinfo, base, sc_cache, taken_at=taken_at, source_kind=source)
        rep = built.report
        keep = int(LP.values_for(project, ctx.project_id, ctx.place_id)("snapshots.keep_older"))
        plan = dryrun.Plan(f"store snapshot {rep['snapshot_id']} for {ctx.project_id}/{ctx.place_id}: {rep['stats']['instances']} instances, {rep['stats']['scripts']} scripts "
                           f"({rep['summaries_new']} new analyses, {rep['summaries_reused']} reused from the content-hash cache)")
        plan.add("save", f"snapshots/{rep['snapshot_id']}.json", f"{rep['stats']['instances']} instances")
        for h, an in list(built.cache_puts.items())[:3]:
            plan.add("cache", f"summary_cache/{h[:12]}", an["summary"][:60])
        if len(built.cache_puts) > 3:
            plan.add("cache", "summary_cache", f"... and {len(built.cache_puts) - 3} more new analyses")
        plan.warnings.extend(rep["warnings"][:8])
        if parsed.status != "own_format":
            plan.warnings.append(f"parser status: {parsed.status} (see samples/README.md)")

        def applier(pl: dryrun.Plan) -> list[str]:
            for h, an in built.cache_puts.items():
                sc_cache.put(h, an)
            path, existed = store.save(built.data)
            return [str(path)] + (["(already stored: unchanged)"] if existed else [])

        res = dryrun.run_plan(plan, applier, apply=not dry_run, journal=ctx.wproject.workspace / "ingest_journal.jsonl" if not dry_run else None)
        n_over = max(0, len(store.list()) + (0 if (not dry_run) else 1) - 1 - keep)
        out = {"summary": plan.summary + (" [DRY RUN: nothing written]" if dry_run else " [stored]"), **ctx.head(), "dry_run": dry_run, "snapshot_id": rep["snapshot_id"], "taken_at": rep["taken_at"],
               "summaries": {"new": rep["summaries_new"], "reused": rep["summaries_reused"]},
               "taken_at_source": rep["taken_at_source"], "format": rep["format"], "parser_status": parsed.status, "stats": rep["stats"], "script_changes": {k_: rep[k_] for k_ in
               ("scripts_new", "scripts_changed", "scripts_removed", "scripts_carried_over_unread")}, "secrets_skipped": rep["secrets_skipped"], "warnings": plan.warnings,
               "scripts_needing_read": rep["scripts_needing_read"], "plan": res.get("steps")}
        if n_over:
            out["prune_hint"] = f"{n_over} snapshot(s) are over the keep limit ({keep} older): prune_snapshots"
        if rep["scripts_needing_read"]:
            out["next"] = "some script sources were not received: plan_refresh with scripts=[...] reads them"
        return out

    def _check_same_place(ctx: places.PlaceCtx, parsed: IN.Parsed) -> None:
        rid, name = parsed.meta.get("roblox_place_id"), parsed.meta.get("place_name")
        want_id, want_name = ctx.place_entry.roblox_place_id, ctx.place_entry.studio_name
        if rid and want_id and int(rid) != int(want_id):
            raise ValueError(f"refusing: the data was collected in Roblox place {rid} but place '{ctx.place_id}' is {want_id}. Ingest it into the place it came from (nothing was stored).")
        if not (rid and want_id) and name and want_name and " ".join(str(name).lower().split()) != " ".join(str(want_name).lower().split()):
            raise ValueError(f"refusing: the data was collected in the Studio place '{name}' but place '{ctx.place_id}' is '{want_name}'. Ingest it into the place it came from (nothing was stored).")
        if not ((rid and want_id) or (name and want_name)):
            parsed.warnings.append("could not verify that the data came from this place (no place id or name on one side); check before relying on it")

    def label_landmark(path: str, role: str, project_id: str | None = None, place_id: str | None = None, label: str = "confirm", confirmed_by: str = "user", alias: str | None = None,
                       note: str = "", learn: bool = True, mark_global: bool = False, dry_run: bool = True) -> dict[str, Any]:
        """Record the USER's confirm or reject of a role for a path, with its feature values, and nudge this project's role weights (w += eta*(label-score)*f, bounded, undoable). Only with the user's explicit word."""
        v = view_of(project_id, place_id)
        ctx = v.ctx
        lab = {"confirm": 1, "reject": 0, 1: 1, 0: 0, "1": 1, "0": 0, True: 1, False: 0}.get(label)
        if lab is None:
            raise ValueError("label must be 'confirm' or 'reject'")
        if role not in v.roles:
            raise ValueError(f"unknown role '{role}'. Known roles: {sorted(v.roles)}")
        if PLACE_SEP in path and path.split(PLACE_SEP, 1)[0] != ctx.place_id:
            raise ValueError(f"the path names place '{path.split(PLACE_SEP, 1)[0]}' but this label is for '{ctx.place_id}': labels belong to one place")
        p = clean_path_input(path)
        if p not in v.snap.by_path:
            raise ValueError(f"no instance at '{p}' in snapshot {v.snap.id}: refresh first, or fix the path")
        if not confirmed_by or confirmed_by.strip().lower() in ("auto", "automatic", "system", "model", "claude", "llm", "ai", "bot", "none"):
            raise ValueError("confirmed_by must name the person who gave the label (it is the approval recorded with the weight change); automatic labels are not allowed")
        row = v.scorer.score_one(role, p)
        ps = v.ps
        learn_scope = S.Scope.make_global() if mark_global else S.Scope(ctx.project_id)
        eta = float(v.pv("learning.eta"))
        steps = LB.plan_learning(row, lab, eta, ps, learn_scope) if learn else []
        plan = dryrun.Plan(f"{'confirm' if lab else 'reject'} {role} for {v.pp(p)} (score {row['score']}, {row['band']})" + (f"; {len(steps)} weight change(s) at {learn_scope.label} scope" if steps else "; no weight change"))
        plan.add("label", v.pp(p), f"{role} = {lab}")
        for s in steps:
            plan.add("weight", s["param"], f"{s['before']} -> {s['after']} (f={s['f']})")
        if mark_global:
            plan.warnings.append("mark_global: the adjustment applies to EVERY project that has no value of its own (you marked it global)")
        label_id = LB.new_label_id()

        def applier(pl: dryrun.Plan) -> list[str]:
            learned = LB.apply_learning(ps, learn_scope, steps, approved_by=confirmed_by, reason=f"user {'confirmed' if lab else 'rejected'} {role} at {p}", evidence=[label_id]) if steps else []
            v.labels.append({"schema": LB.SCHEMA, "kind": "label", "label_id": label_id, "at": now_iso(), "project_id": ctx.project_id, "place_id": ctx.place_id, "path": p, "role": role,
                             "label": lab, "alias": alias, "note": note, "score_before": row["score"], "band_before": row["band"], "features": row["features"], "weights_before": row["weights"],
                             "confirmed_by": confirmed_by, "snapshot_id": v.snap.id, "learned": learned, "learn_scope": learn_scope.key if learned else None, "source": "user"})
            sig = []
            if lab == 0 and row["band"] == "confident":
                sig.append({"kind": "false_positive", "target": f"param:band.{role}.confident", "direction": "up"})
            if lab == 1 and row["band"] == "candidate":
                sig.append({"kind": "miss", "target": f"param:band.{role}.confident", "direction": "down"})
            obs_log().log_run(request=f"label {role} {'confirm' if lab else 'reject'}", scope=ctx.scope, tools=["label_landmark"], signals=sig, considered=[f"param:band.{role}.confident"])
            return [str(v.labels.file)]

        res = dryrun.run_plan(plan, applier, apply=not dry_run)
        return {"summary": plan.summary + (" [DRY RUN]" if dry_run else f" [saved as {label_id}]"), **ctx.head(), "dry_run": dry_run, "label_id": None if dry_run else label_id, "role": role, "path": v.pp(p),
                "label": lab, "score_before": row["score"], "band_before": row["band"], "weight_changes": steps[:10], "eta": eta, "precedence": "explicit user label > learned project weights > default weights",
                "warnings": plan.warnings, "undo": None if dry_run else f"undo_label label_id={label_id}"}

    def undo_label(label_id: str, project_id: str | None = None, place_id: str | None = None, confirmed_by: str = "user", dry_run: bool = True) -> dict[str, Any]:
        """[rare] Undo a label: it stops counting and the weights it changed go back one version each (only if nothing newer changed them)."""
        ctx = ctx_of(project_id, place_id)
        ls = LB.LabelStore(ctx)
        row = ls.get(label_id)
        if row is None:
            raise ValueError(f"no label '{label_id}' in place {ctx.project_id}/{ctx.place_id}")
        if ls.undone(label_id):
            raise ValueError(f"label '{label_id}' was already undone")
        ps = ps_for(ctx)
        learned = row.get("learned") or []
        scope_ = S.scope_from_dict({"project_id": row["learn_scope"].split("/")[0], "place_id": None, "global": row["learn_scope"] == "_global"}) if row.get("learn_scope") else None
        plan = dryrun.Plan(f"undo label {label_id} ({row['role']} at {row['path']}): restore {len(learned)} weight(s)")
        for a in learned:
            plan.add("restore", a["param"], f"{a['after']} -> {a['before']}")

        def applier(pl: dryrun.Plan) -> dict:
            res = LB.undo_learning(ps, scope_, learned, approved_by=confirmed_by, reason=f"undo {label_id}") if learned else {"undone": [], "blocked": []}
            ls.append({"schema": LB.SCHEMA, "kind": "undo", "label_id": label_id, "at": now_iso(), "by": confirmed_by, "result": res})
            return res

        if dry_run:
            return {"summary": plan.summary + " [DRY RUN]", **ctx.head(), "dry_run": True, "steps": plan.steps}
        res = dryrun.run_plan(plan, applier, apply=True, journal=ctx.wproject.workspace / "undo_journal.jsonl")
        return {"summary": plan.summary + " [done]", **ctx.head(), "dry_run": False, "result": res["changed"]}

    def prune_snapshots(project_id: str | None = None, place_id: str | None = None, keep_older: int | None = None, dry_run: bool = True) -> dict[str, Any]:
        """[rare] Delete old snapshots of this place beyond the latest plus keep_older (default from limits). Dry run lists them. Summaries and labels are not touched."""
        ctx = ctx_of(project_id, place_id)
        store = SnapshotStore(ctx.wproject)
        keep = int(LP.values_for(project, ctx.project_id, ctx.place_id)("snapshots.keep_older")) if keep_older is None else int(keep_older)
        if keep < 0:
            raise ValueError("keep_older must be 0 or more")
        victims = store.prune_candidates(keep)
        plan = dryrun.Plan(f"prune {len(victims)} snapshot(s) of {ctx.project_id}/{ctx.place_id}, keeping the latest and {keep} older")
        for r in victims:
            plan.add("delete", f"snapshots/{r['id']}.json", r["taken_at"])
        res = dryrun.run_plan(plan, lambda pl: [store.delete(r["id"]) or r["id"] for r in victims], apply=not dry_run, journal=ctx.wproject.workspace / "prune_journal.jsonl" if not dry_run else None)
        return {"summary": plan.summary + (" [DRY RUN]" if dry_run else " [done]"), **ctx.head(), "dry_run": dry_run, "would_delete" if dry_run else "deleted": [r["id"] for r in victims]}

    def record_run(request: str, project_id: str | None = None, place_id: str | None = None, constraints: dict | None = None, retrieved: list[str] | None = None, tools: list[str] | None = None,
                   recipes: list[str] | None = None, outputs: list[str] | None = None, preview: str | None = None, model: str | None = None, is_global: bool = False) -> dict[str, Any]:
        """Save a request and its results for this place; returns run_id. is_global=true stores it for every place."""
        ctx = ctx_of(project_id, place_id)
        target, sc = (global_proj(), S.Scope.make_global()) if is_global else (ctx.wproject, ctx.scope)
        cons = {**(constraints or {}), "project_id": ctx.project_id, "place_id": ctx.place_id, "global": bool(is_global)}
        rid = feedback.record_run(target, request=request, constraints=cons, retrieved=retrieved, tools=tools, recipes=recipes, outputs=outputs, preview=preview, model=model, scope=sc)
        return {"summary": f"saved run {rid} for {ctx.project_id}/{ctx.place_id}{' (GLOBAL)' if is_global else ''}", **ctx.head(), "run_id": rid}

    def record_decision(run_id: str, decision: str, project_id: str | None = None, place_id: str | None = None, reason: str = "", corrections: list[dict] | None = None, rating: int | None = None,
                        promote_requested: bool = False, is_global: bool = False) -> dict[str, Any]:
        """Save the user's verdict (accept/reject/revise) on a run, with corrections [{dimension, note}] in the user's words (dimensions: role, landmark, naming, search, summary, scope)."""
        ctx = ctx_of(project_id, place_id)
        target = global_proj() if is_global else ctx.wproject
        dims = config.load_style(project).get("correction_dimensions")
        row = feedback.record_decision(target, run_id, decision, reason=reason, corrections=corrections, rating=rating, promote_requested=promote_requested, allowed_dimensions=dims,
                                       scope=S.Scope.make_global() if is_global else ctx.scope, mark_global=bool(is_global))
        return {"summary": f"saved {decision} for {run_id} ({ctx.project_id}/{ctx.place_id}{', GLOBAL' if is_global else ''})", **ctx.head(), "decision": row["decision"]}

    read = [get_style_brief, find_in_place, get_path_info, who_uses, list_remotes, show_dependencies, summarize_area, diff_snapshots, find_past_corrections, find_across_places, list_places, list_candidates,
            list_snapshots, list_roles, compare_places, find_shared_code]
    write = [plan_refresh, ingest_snapshot, label_landmark, undo_label, prune_snapshots, record_run, record_decision]
    specs = [ToolSpec(f.__name__, f, (f.__doc__ or "").strip().replace("\n", " ")) for f in read]
    specs += [ToolSpec(f.__name__, f, (f.__doc__ or "").strip().replace("\n", " "), read_only=False, idempotent=f.__name__ in ("plan_refresh", "ingest_snapshot", "prune_snapshots"))
              for f in write]
    for s in specs:
        if s.name in RARE:
            s.group = "rare"
    return specs
