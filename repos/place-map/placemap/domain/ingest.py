"""Parse what the hub sends back and turn it into a snapshot.

Formats accepted by :func:`parse_input` (every one of them is ``schema_unverified``: no real hub capture exists yet, see samples/README.md):

* ``placemap-collect/1``: the JSON the Luau from ``plan_refresh`` returns (or prints between PLACEMAP_BEGIN / PLACEMAP_CHUNK / PLACEMAP_END markers). OUR format, so documented here and
  proven on the mock DataModel, but the wrapper the hub puts around an execute_luau result is unknown, so a wrapper mapping (``result``/``output``/``content``/``stdout``/``text`` ...) is unwrapped heuristically.
* ``placemap-snapshot/1``: an already normalised snapshot (round trips).
* a generic instance tree (``Name``/``ClassName``/``Children``/``Attributes`` in any of the usual spellings) or a flat list of such nodes with ``path`` or ``FullName``: the shape
  of the hub's ``search_game_tree`` result is UNKNOWN, so this is a best-effort reader that lists exactly what it understood and what it ignored.
* a text outline: ``path | Class | key=value; key=value`` per line, or an indented tree ``Name [Class] key=value``.
* ``script_read`` results: ``{"path"|"FullName", "source"|"Source"}``, a list of them, or text blocks ``=== path ===`` followed by the source.

Nothing here connects to Studio. Secrets (attribute keys or values that look like keys or tokens) are dropped before anything is stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import scan
from .secrets import SecretFilter, default_filter
from .snapshot import REMOTE_CLASSES, SCHEMA, SCRIPT_CLASSES, Snapshot, content_digest, new_id
from .util import SEP, content_hash, escape_segment, fingerprint, is_under, iso_from_unix, now_iso, parent_path, unescape_segment

COLLECT_FORMAT = "placemap-collect/1"
WRAPPER_KEYS = ("result", "output", "content", "stdout", "text", "data", "return", "returned", "value")
MARK_CHUNK = re.compile(r"PLACEMAP_CHUNK\s+(\d+)\s+(\S.*)")
BLOCK_RE = re.compile(r"^=== (.+?) ===\s*$", re.M)
MAX_ATTR_KEYS = 60


@dataclass
class Parsed:
    kind: str                                   # tree | scripts
    format: str
    instances: list[dict] = field(default_factory=list)   # normalised (path, name, class, attrs, tags, pos, n_children)
    script_src: dict[str, dict] = field(default_factory=dict)  # path -> {class, rc, fp, length, src|None, cut}
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    status: str = "schema_unverified"
    skipped_secrets: int = 0


def _warn(p: Parsed, msg: str) -> None:
    if len(p.warnings) < 30:
        p.warnings.append(msg)


# --- input unwrapping -----------------------------------------------------------------------------------------------------------------
def _unwrap(data: Any, depth: int = 0) -> tuple[Any, str | None]:
    if depth > 3:
        return data, None
    if isinstance(data, dict):
        if data.get("format") == COLLECT_FORMAT or data.get("schema") == SCHEMA:
            return data, None
        for k in WRAPPER_KEYS:
            v = data.get(k)
            if isinstance(v, (str, dict)) and v:
                inner, _ = _unwrap(_from_text(v) if isinstance(v, str) else v, depth + 1)
                return inner, k
            if isinstance(v, list) and v and all(isinstance(x, dict) and ("text" in x or "content" in x) for x in v):
                return _from_text("\n".join(str(x.get("text") or x.get("content")) for x in v)), k
    if isinstance(data, list) and data and all(isinstance(x, dict) and set(x) & {"text", "content"} and not set(x) & {"path", "FullName", "Name", "name"} for x in data):
        return _from_text("\n".join(str(x.get("text") or x.get("content")) for x in data)), "content[]"
    return data, None


def _from_text(text: str) -> Any:
    s = text.strip()
    chunks = MARK_CHUNK.findall(text)
    if chunks:
        parts = {}
        for i, body in chunks:
            parts[int(i)] = body.strip()
        try:
            return json.loads("".join(parts[k] for k in sorted(parts)))
        except json.JSONDecodeError:
            return s
    if s[:1] in "{[":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{\s*\"format\"\s*:\s*\"placemap-collect/1\"", s)
    if m:
        try:
            return json.JSONDecoder().raw_decode(s[m.start():])[0]
        except json.JSONDecodeError:
            pass
    return s


def parse_input(data: Any, flt: SecretFilter | None = None) -> Parsed:
    flt = flt or default_filter()
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", "replace")
    if isinstance(data, str):
        data = _from_text(data)
    data, wrapper = _unwrap(data)
    if isinstance(data, str):
        p = parse_script_blocks(data) if BLOCK_RE.search(data) else parse_text_tree(data, flt)
    elif isinstance(data, dict) and data.get("format") == COLLECT_FORMAT:
        p = parse_collect(data, flt)
    elif isinstance(data, dict) and data.get("schema") == SCHEMA:
        p = parse_snapshot_json(data)
    elif isinstance(data, dict) and (set(data) & {"path", "FullName", "fullName"}) and (set(data) & {"source", "Source", "content"}):
        p = parse_script_read([data])
    elif isinstance(data, list) and data and all(isinstance(x, dict) and (set(x) & {"source", "Source"}) for x in data):
        p = parse_script_read(data)
    elif isinstance(data, dict) and isinstance(data.get("scripts"), list) and data["scripts"] and all(isinstance(x, dict) and (set(x) & {"source", "Source"}) for x in data["scripts"]):
        p = parse_script_read(data["scripts"])
    elif isinstance(data, (dict, list)):
        p = parse_generic_tree(data, flt)
    else:
        raise ValueError(f"cannot read input of type {type(data).__name__}: expected JSON from the collector Luau, a tree or a text outline (see samples/README.md)")
    if wrapper:
        p.meta["wrapper_key"] = wrapper
        _warn(p, f"the input was wrapped in '{wrapper}' (the hub's real wrapper shape is unverified; unwrapped heuristically)")
    return p


# --- node normalisation -----------------------------------------------------------------------------------------------------------------
def _seg_ok(seg: str) -> bool:
    return bool(seg) and not re.search(r"[\x00-\x1f]", seg)


def _clean_attrs(raw: Any, flt: SecretFilter, p: Parsed, limit: int = MAX_ATTR_KEYS) -> dict:
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for k, v in list(raw.items())[: limit * 2]:
        if len(out) >= limit:
            break
        if flt.attr_is_secret(str(k), v):
            p.skipped_secrets += 1
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[str(k)] = v
        elif isinstance(v, str):
            out[str(k)] = v[:200]
        else:
            out[str(k)] = str(v)[:80]
    return out


def _norm_node(path: str, cls: str, raw_attrs: Any, tags: Any, pos: Any, n_children: int | None, flt: SecretFilter, p: Parsed, name: str | None = None) -> dict | None:
    if not _seg_ok(path.replace(SEP, "")) or not isinstance(cls, str) or not cls:
        _warn(p, f"skipped a node with an unusable path or class: {str(path)[:60]!r}")
        return None
    if any(not s for s in path.split(SEP)):
        _warn(p, f"skipped a node with an empty path segment: {path[:60]!r}")
        return None
    position = None
    if isinstance(pos, (list, tuple)) and len(pos) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pos):
        position = [round(float(x), 1) for x in pos]
    return {"path": path, "name": name if name is not None else unescape_segment(re.sub(r"#\d+$", "", path.rsplit(SEP, 1)[-1])), "class": cls,
            "attrs": _clean_attrs(raw_attrs, flt, p), "tags": [str(t)[:40] for t in (tags or [])][:20] if isinstance(tags, list) else [],
            "pos": position, "n_children": int(n_children) if isinstance(n_children, int) else 0}


def parse_collect(d: dict, flt: SecretFilter) -> Parsed:
    p = Parsed("tree", COLLECT_FORMAT, status="own_format_proven_on_mock_only (hub wrapper schema_unverified)")
    place = d.get("place") or {}
    p.meta = {"collector_version": d.get("collector_version"), "place_name": place.get("name"), "roblox_place_id": place.get("place_id"), "game_id": place.get("game_id"),
              "full": bool(d.get("full", True)), "roots": [str(r) for r in d.get("roots", [])], "truncated": bool(d.get("truncated")), "skipped": d.get("skipped") or {},
              "taken_unix": d.get("taken_unix")}
    for n in d.get("nodes", []):
        if not isinstance(n, dict) or "p" not in n:
            _warn(p, "skipped a malformed node")
            continue
        node = _norm_node(str(n["p"]), n.get("c"), n.get("a"), n.get("t"), n.get("v"), n.get("n"), flt, p)
        if node is None:
            continue
        p.instances.append(node)
        if node["class"] in SCRIPT_CLASSES:
            s = n.get("s") or {}
            src = s.get("src")
            p.script_src[node["path"]] = {"class": node["class"], "rc": n.get("rc"), "fp": s.get("fp"), "length": s.get("len"),
                                          "src": src if isinstance(src, str) else None, "cut": bool(s.get("cut"))}
    if p.meta["truncated"]:
        _warn(p, "the collector stopped at its instance or source cap: this snapshot is incomplete (see skipped); refresh smaller roots")
    return p


def parse_snapshot_json(d: dict) -> Parsed:
    p = Parsed("tree", SCHEMA, status="own_format")
    p.instances = [dict(i) for i in d.get("instances", [])]
    p.meta = {"prebuilt": d, "full": bool(d.get("full", True)), "roots": d.get("roots", []), "truncated": bool(d.get("truncated"))}
    return p


def _first(d: dict, keys: tuple[str, ...], default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def parse_generic_tree(data: Any, flt: SecretFilter) -> Parsed:
    p = Parsed("tree", "generic-tree", status="schema_unverified")
    ignored: set[str] = set()
    nodes: list[tuple[str, dict]] = []

    def visit(n: Any, parent: str, depth: int) -> None:
        if not isinstance(n, dict) or depth > 60:
            return
        name = _first(n, ("Name", "name", "InstanceName"))
        full = _first(n, ("path", "Path", "FullName", "fullName"))
        cls = _first(n, ("ClassName", "className", "class", "Class", "type", "Type"), "Instance")
        if full and not name:
            segs = [s for s in re.split(r"[./]", str(full)) if s]
            name = segs[-1] if segs else None
        if not isinstance(name, str) or not name:
            _warn(p, "skipped a node without a name")
            return
        path = _full_to_path(str(full)) if (full and depth == 0) else (parent + SEP if parent else "") + escape_segment(name)
        attrs = _first(n, ("Attributes", "attributes", "attrs"), {})
        pos = _first(n, ("Position", "position", "pos"))
        if isinstance(pos, dict):
            pos = [pos.get("X"), pos.get("Y"), pos.get("Z")]
        node = _norm_node(path, str(cls), attrs, _first(n, ("Tags", "tags")), pos, None, flt, p, name=name)
        if node:
            nodes.append((node["path"], node))
            src = _first(n, ("Source", "source"))
            if node["class"] in SCRIPT_CLASSES:
                p.script_src[node["path"]] = {"class": node["class"], "rc": _first(n, ("RunContext", "runContext")), "fp": None, "length": None,
                                              "src": src if isinstance(src, str) else None, "cut": False}
            kids = _first(n, ("Children", "children", "Instances", "instances"), [])
            if isinstance(kids, list):
                node["n_children"] = len(kids)
                for k in kids:
                    visit(k, node["path"], depth + 1)
        ignored.update(k for k in n if k not in {"Name", "name", "InstanceName", "path", "Path", "FullName", "fullName", "ClassName", "className", "class", "Class", "type", "Type", "Attributes",
                                                  "attributes", "attrs", "Position", "position", "pos", "Tags", "tags", "Children", "children", "Instances", "instances", "Source", "source",
                                                  "RunContext", "runContext"})

    roots = data if isinstance(data, list) else (data.get("nodes") or data.get("results") or data.get("instances") or data.get("tree") or [data]) if isinstance(data, dict) else []
    if isinstance(roots, dict):
        roots = [roots]
    for r in roots:
        visit(r, "", 0)
    p.instances = [n for _, n in nodes]
    _dedupe_instances(p)
    p.meta = {"full": False, "roots": sorted({i["path"].split(SEP)[0] for i in p.instances}), "ignored_keys": sorted(ignored)[:20]}
    if not p.instances:
        _warn(p, "no instances were recognised in this input")
    if ignored:
        _warn(p, f"ignored unknown keys: {sorted(ignored)[:10]}")
    return p


def _full_to_path(full: str) -> str:
    f = full[5:] if full.startswith("game.") else full
    if SEP in f:
        return SEP.join(escape_segment(s) if "%" in s else s for s in f.split(SEP) if s)
    return SEP.join(s for s in f.split(".") if s)


def _dedupe_instances(p: Parsed) -> None:
    seen: set[str] = set()
    for node in p.instances:
        base, n = node["path"], 1
        while node["path"] in seen:
            n += 1
            node["path"] = f"{base}#{n}"
        seen.add(node["path"])


_ATTR_PAIR = re.compile(r"([A-Za-z_][\w.-]*)=([^;]*)")


def _scalar(v: str) -> Any:
    v = v.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v.strip("\"'")


def parse_text_tree(text: str, flt: SecretFilter) -> Parsed:
    """``path | Class | k=v; k=v`` lines, or an indented outline ``Name [Class] k=v``."""
    p = Parsed("tree", "text-outline", status="schema_unverified")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    nodes: list[tuple[str, dict]] = []
    if any(" | " in ln for ln in lines):
        for ln in lines:
            parts = [x.strip() for x in ln.split(" | ")]
            if len(parts) < 2:
                _warn(p, f"skipped a line without a class: {ln[:50]!r}")
                continue
            attrs = {k: _scalar(v) for k, v in _ATTR_PAIR.findall(parts[2])} if len(parts) > 2 else {}
            node = _norm_node(_full_to_path(parts[0]), parts[1], attrs, None, None, None, flt, p)
            if node:
                nodes.append((node["path"], node))
    else:
        stack: list[tuple[int, str]] = []
        for ln in lines:
            indent = (len(ln) - len(ln.lstrip(" "))) // 2
            m = re.match(r"\s*(.+?)\s*\[(\w+)\]\s*(.*)$", ln)
            if not m:
                _warn(p, f"skipped a line without [Class]: {ln.strip()[:50]!r}")
                continue
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = (stack[-1][1] + SEP if stack else "") + escape_segment(m.group(1))
            node = _norm_node(path, m.group(2), {k: _scalar(v) for k, v in _ATTR_PAIR.findall(m.group(3))}, None, None, None, flt, p, name=m.group(1))
            if node:
                nodes.append((path, node))
                stack.append((indent, path))
    p.instances = [n for _, n in nodes]
    _dedupe_instances(p)
    for n in p.instances:
        n["n_children"] = sum(1 for m in p.instances if parent_path(m["path"]) == n["path"])
    p.meta = {"full": False, "roots": sorted({i["path"].split(SEP)[0] for i in p.instances})}
    if not p.instances:
        _warn(p, "no instances were recognised in this text")
    return p


def parse_script_read(items: list[dict]) -> Parsed:
    p = Parsed("scripts", "script_read", status="schema_unverified")
    for it in items:
        full = _first(it, ("path", "FullName", "fullName", "name"))
        src = _first(it, ("source", "Source", "content", "text"))
        if not isinstance(full, str) or not isinstance(src, str):
            _warn(p, "skipped a script_read item without path and source")
            continue
        path = _full_to_path(full)
        p.script_src[path] = {"class": _first(it, ("ClassName", "class", "className"), "ModuleScript"), "rc": None, "fp": None, "length": None, "src": src, "cut": False}
    return p


def parse_script_blocks(text: str) -> Parsed:
    p = Parsed("scripts", "script_read-text", status="schema_unverified")
    marks = list(BLOCK_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        p.script_src[_full_to_path(m.group(1))] = {"class": "ModuleScript", "rc": None, "fp": None, "length": None, "src": text[m.end():end].lstrip("\n"), "cut": False}
    return p


# --- building a snapshot -----------------------------------------------------------------------------------------------------------------
@dataclass
class Built:
    data: dict
    report: dict
    cache_puts: dict[str, dict]


def build_snapshot(p: Parsed, place: dict, base: Snapshot | None, cache, *, taken_at: str | None = None, source_kind: str = "execute_luau") -> Built:
    """Merge a parse result into a snapshot. ``cache`` has ``get(hash)`` (summary cache). Nothing is written here."""
    report: dict[str, Any] = {"format": p.format, "parser_status": p.status, "warnings": list(p.warnings), "secrets_skipped": p.skipped_secrets}
    if p.kind == "scripts":
        if base is None:
            raise ValueError("script_read results update an existing snapshot, but this place has none yet: ingest a tree first")
        instances = {i["path"]: dict(i) for i in base.instances}
        scripts_old = dict(base.scripts)
        roots, full, truncated = list(base.data.get("roots", [])), base.data.get("full", True), base.data.get("truncated", False)
        skipped = dict(base.data.get("skipped", {}))
        taken = base.taken_at
        taken_src = base.data.get("taken_at_source", "collector")
        meta_taken = None
    elif p.meta.get("prebuilt"):
        pre = p.meta["prebuilt"]
        instances = {i["path"]: i for i in pre["instances"]}
        scripts_old = {s["path"]: s for s in pre.get("scripts", [])}
        roots, full, truncated, skipped = pre.get("roots", []), pre.get("full", True), pre.get("truncated", False), pre.get("skipped", {})
        taken, taken_src, meta_taken = pre["taken_at"], pre.get("taken_at_source", "collector"), pre["taken_at"]
    else:
        full = bool(p.meta.get("full", False))
        roots = list(p.meta.get("roots", []))
        truncated = bool(p.meta.get("truncated"))
        skipped = dict(p.meta.get("skipped", {}))
        unix = p.meta.get("taken_unix")
        meta_taken = iso_from_unix(unix) if isinstance(unix, (int, float)) and unix > 0 else None
        taken = taken_at or meta_taken or now_iso()
        taken_src = "collector" if (meta_taken and not taken_at) else ("given" if taken_at else "ingest")
        scripts_old = dict(base.scripts) if base else {}
        if full or base is None:
            instances = {}
            if not full and base is None:
                report["warnings"].append("this input is a partial tree and the place had no snapshot yet: the snapshot only covers the listed roots")
        else:
            instances = {i["path"]: dict(i) for i in base.instances}
            if not roots:
                raise ValueError("a partial tree without roots cannot be merged safely; ingest a full snapshot or name the roots")
            for path in [q for q in instances if any(is_under(q, r) for r in roots)]:
                del instances[path]
            scripts_old = {q: s for q, s in scripts_old.items() if not any(is_under(q, r) for r in roots)}
            roots = sorted(set(base.data.get("roots", [])) | set(roots))
            full = base.data.get("full", True)
            truncated = truncated or bool(base.data.get("truncated"))
        for n in p.instances:
            instances[n["path"]] = n
    scripts: dict[str, dict] = {}
    cache_puts: dict[str, dict] = {}
    new_h, reused_h, carried, needs_read, mismatches = set(), 0, 0, [], []
    old_scripts = dict(base.scripts) if base else {}
    base_ref = old_scripts if p.kind == "tree" else scripts_old
    for path, node in instances.items():
        if node["class"] not in SCRIPT_CLASSES:
            continue
        info = p.script_src.get(path)
        if info is None:
            prev = scripts_old.get(path) or base_ref.get(path)
            if prev:
                scripts[path] = prev
            else:
                scripts[path] = _unread(path, node["class"], None)
                needs_read.append(path)
            continue
        src = info["src"]
        if src is None:
            prev = base_ref.get(path)
            if prev and info.get("fp") and prev.get("fp") == info["fp"]:
                scripts[path] = {**prev, "class": node["class"]}
                carried += 1
            else:
                scripts[path] = _unread(path, node["class"], info.get("fp"), info.get("length"), info.get("rc"))
                needs_read.append(path)
            continue
        h = content_hash(src)
        fp = fingerprint(src)
        if info.get("fp") and info["fp"] != fp:
            mismatches.append(path)
        analysis = cache_puts.get(h) or cache.get(h)
        if analysis is None or analysis.get("analysis_version") != scan.ANALYSIS_VERSION:
            analysis = scan.analyze(src, node["class"])
            cache_puts[h] = analysis
            new_h.add(h)
        else:
            reused_h += 1
        rec = {"path": path, "class": node["class"], "kind": scan.kind_of(node["class"], info.get("rc")), "hash": h, "fp": fp, "length": len(src.encode("utf-8")),
               "source_cut": bool(info.get("cut")), "needs_read": False}
        rec.update({k: analysis[k] for k in analysis if k not in ("analysis_version",)})
        scripts[path] = rec
    changed = [q for q, s in scripts.items() if (old_scripts.get(q) or {}).get("hash") not in (None, s.get("hash")) and s.get("hash")]
    added = [q for q in scripts if q not in old_scripts]
    removed = [q for q in old_scripts if q not in scripts] if (base and (p.kind == "scripts" or full)) else []
    if mismatches:
        report["warnings"].append(f"{len(mismatches)} script(s) had a fingerprint that does not match the received source (cut or re-encoded?): {mismatches[:3]}")
    inst_list = sorted(instances.values(), key=lambda i: i["path"])
    scr_list = sorted(scripts.values(), key=lambda s: s["path"])
    digest = content_digest(inst_list, scr_list)
    sid = new_id(taken, digest)
    stats = {"instances": len(inst_list), "scripts": len(scr_list), "remotes": sum(1 for i in inst_list if i["class"] in REMOTE_CLASSES),
             "scripts_needing_read": len(needs_read)}
    data = {"schema": SCHEMA, "snapshot_id": sid, "place": dict(place), "taken_at": taken, "taken_at_source": taken_src, "ingested_at": now_iso(),
            "source": {"kind": source_kind, "format": p.format, "parser_status": p.status, "collector_version": p.meta.get("collector_version"), "wrapper_key": p.meta.get("wrapper_key")},
            "full": full, "roots": roots, "truncated": truncated, "skipped": skipped, "stats": stats, "instances": inst_list, "scripts": scr_list}
    report.update({"snapshot_id": sid, "taken_at": taken, "taken_at_source": taken_src, "stats": stats,
                   "scripts_new": len(added), "scripts_changed": len(changed), "scripts_removed": len(removed), "scripts_carried_over_unread": carried,
                   "summaries_new": len(new_h), "summaries_reused": reused_h + carried, "scripts_needing_read": needs_read[:20]})
    return Built(data, report, cache_puts)


def _unread(path: str, cls: str, fp: str | None, length: int | None = None, rc: str | None = None) -> dict:
    return {"path": path, "class": cls, "kind": scan.kind_of(cls, rc), "hash": None, "fp": fp, "length": length, "source_cut": False, "needs_read": True, "requires": [], "remotes_fired": [],
            "remotes_handled": [], "datastores": [], "datastore_keys": [], "services": [], "functions": [], "name_refs": [], "string_tokens": [], "first_comment": "",
            "secret_like_strings_skipped": 0, "summary": "(source not read yet: run plan_refresh with scripts= to read it)", "lines": 0}
