"""Search over one snapshot: literal naming first, then a hybrid (BM25 text, tags, hashing-embedding similarity) ranking, with near matches reported as 'not selected'.

Literal naming: when the request names a thing ("Bob", "the vendor NPC", "Thing1") and exactly ONE instance has that name, that instance is the answer and nothing broader is returned;
other near matches are listed separately so the caller can ask. Several instances with the same name are never resolved by guessing: they are listed and the caller is asked.
"""

from __future__ import annotations

import difflib
import re

from ..guide_adapter import embeddings as EMB, retrieval as RT
from .index import PlaceIndex
from .snapshot import SCRIPT_CLASSES
from .util import clean_path_input, split_tokens

STOPWORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "where", "is", "are", "was", "find", "show", "me", "get", "all", "my", "our", "this", "that", "named", "called",
             "what", "which", "who", "please", "locate", "give", "list"}
CLASS_HINTS = {"script": {"Script", "LocalScript"}, "module": {"ModuleScript"}, "modulescript": {"ModuleScript"}, "model": {"Model"}, "folder": {"Folder"}, "part": {"Part", "MeshPart"},
               "remote": {"RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent"}, "event": {"RemoteEvent", "BindableEvent"}, "prompt": {"ProximityPrompt"}, "gui": {"ScreenGui"}}
_EMBEDDER = None


def query_tokens(query: str) -> list[str]:
    return [t for t in split_tokens(query) if t not in STOPWORDS]


def looks_like_path(query: str) -> bool:
    q = query.strip()
    return "::" in q or "/" in q or (bool(re.fullmatch(r"[A-Za-z_][\w ]*(\.[\w ]+)+", q)) and not q.endswith("."))


def exact_path(idx: PlaceIndex, query: str) -> str | None:
    p = clean_path_input(query)
    if p in idx.snap.by_path:
        return p
    return None


def exact_name_matches(idx: PlaceIndex, query: str) -> list[str]:
    qt = query_tokens(query)
    if not qt:
        return []
    raw = query.strip().strip("\"'")
    out = []
    hint = CLASS_HINTS.get(qt[-1]) if len(qt) > 1 else None
    for p, inst in idx.snap.by_path.items():
        name = idx.name(p)
        toks = split_tokens(name)
        if name.lower() == raw.lower() or toks == qt:
            out.append(p)
        elif hint and inst["class"] in hint and toks == qt[:-1]:
            out.append(p)
    return sorted(out)


def _doc(idx: PlaceIndex, p: str) -> dict:
    inst = idx.snap.by_path[p]
    name_toks = idx.name_tokens(p)
    parts = [inst["class"], " ".join(inst.get("attrs", {}).keys()), " ".join(str(v) for v in inst.get("attrs", {}).values() if isinstance(v, str)), " ".join(inst.get("tags", []))]
    rec = idx.snap.scripts.get(p)
    if rec:
        parts += [rec.get("summary", ""), " ".join(r["name"] for r in rec.get("remotes_fired", []) + rec.get("remotes_handled", [])),
                  " ".join(str(r["chain"][-1]) for r in rec.get("requires", []) if r.get("chain")), " ".join(rec.get("functions", []))]
    parent = p.rsplit("/", 1)[0] if "/" in p else ""
    parts.append(" ".join(split_tokens(parent)))
    title = " ".join(name_toks) + " "
    return {"id": p, "title": title * 3, "text": " ".join(x for x in parts if x), "tags": [inst["class"].lower(), *(["script"] if inst["class"] in SCRIPT_CLASSES else [])]}


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = EMB.HashingEmbedder(256)
    return _EMBEDDER


def _vocab(idx: PlaceIndex) -> set[str]:
    v = getattr(idx, "_vocab", None)
    if v is None:
        v = {t for p in idx.snap.by_path for t in idx.name_tokens(p)}
        idx._vocab = v  # type: ignore[attr-defined]
    return v


def expand_misspellings(idx: PlaceIndex, query: str) -> str:
    """Query words that no instance name contains are extended with their close spellings from the place's own vocabulary (difflib ratio >= 0.8), so 'purchsae' still finds 'purchase'."""
    vocab = _vocab(idx)
    out = split_tokens(query)
    for t in list(out):
        if t not in vocab and len(t) >= 4:
            out += difflib.get_close_matches(t, sorted(vocab), n=2, cutoff=0.8)
    return " ".join(out)


def hybrid(idx: PlaceIndex, query: str, *, k: int = 10, kind: str | None = None, under: str | None = None, min_score: float = 0.0, embed_limit: int = 4000) -> list[dict]:
    """Ranked hits ``[{path, score, reasons}]``. BM25 + tags always; embedding similarity (dependency-free hashing embedder) re-ranks the BM25 pool, and covers misspellings
    over the whole place when it is small enough (``embed_limit`` instances)."""
    paths = [p for p in idx.snap.by_path if (under is None or p == under or p.startswith(under + "/"))]
    if kind:
        kinds = CLASS_HINTS.get(kind.lower(), {kind})
        paths = [p for p in paths if idx.snap.by_path[p]["class"] in kinds]
    docs = [_doc(idx, p) for p in paths]
    q = expand_misspellings(idx, query)
    pool = RT.search_items(docs, q, k=max(k * 5, 50), text_fields=("title", "text"), weights={"text": 0.65, "tags": 0.0})
    if len(pool) < k and len(docs) <= embed_limit:
        emb = _embedder()
        vecs = dict(zip([d["id"] for d in docs], emb.embed_texts([d["title"] + " " + d["text"] for d in docs])))
        pool = RT.search_items(docs, q, k=max(k * 5, 50), text_fields=("title", "text"), embedder=emb, vectors=vecs, weights={"text": 0.65, "tags": 0.0, "embedding": 0.35})
    elif pool:
        emb = _embedder()
        vecs = dict(zip([d["id"] for d in pool], emb.embed_texts([d["title"] + " " + d["text"] for d in pool])))
        pool = RT.search_items(pool, q, k=max(k * 5, 50), text_fields=("title", "text"), embedder=emb, vectors=vecs, weights={"text": 0.65, "tags": 0.0, "embedding": 0.35})
    out = [{"path": h["id"], "score": h["score"], "reasons": h["reasons"]} for h in pool if h["score"] >= min_score]
    return out[:k]


def near_matches(idx: PlaceIndex, query: str, exclude: set[str], n: int, min_score: float = 0.0) -> list[dict]:
    hits = [h for h in hybrid(idx, query, k=n + len(exclude) + 5, min_score=min_score) if h["path"] not in exclude]
    return hits[:n]


def literal_near(idx: PlaceIndex, selected: str, query: str, n: int) -> list[dict]:
    """Items near an exact answer, for the 'not selected' list: instances sharing name words with the request, and siblings with the same structure as the selected one."""
    if n <= 0:
        return []
    qt = set(query_tokens(query)) | set(split_tokens(idx.name(selected)))
    sig = idx.signature(selected)
    par = selected.rsplit("/", 1)[0] if "/" in selected else ""
    scored = []
    for p in idx.snap.by_path:
        if p == selected:
            continue
        nt = set(idx.name_tokens(p))
        shared = qt & nt
        sibling = p.rsplit("/", 1)[0] == par if "/" in p else par == ""
        same_struct = sibling and idx.signature(p) == sig
        if not shared and not same_struct:
            continue
        s = (len(shared) / max(1, len(qt | nt))) + (0.5 if same_struct else 0.0)
        why = []
        if same_struct:
            why.append("sibling with the same structure")
        if shared:
            why.append("shares name words: " + ", ".join(sorted(shared)))
        scored.append((s, p, "; ".join(why)))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [{"path": p, "reason": f"not selected: {why}"} for _, p, why in scored[:n]]
