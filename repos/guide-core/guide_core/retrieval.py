"""Hybrid retrieval: exact filters + tags + BM25 text + palette + optional embeddings.

Every hit carries ``reasons`` so the agent (and the user) can see *why* an
example was retrieved. Positive and negative ("avoid") examples are returned
separately so an agent can follow one and steer away from the other.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable, Sequence

from .embeddings import Embedder, cosine, load_vectors
from .manifest import LibraryStore

DEFAULT_WEIGHTS = {"text": 0.45, "tags": 0.2, "palette": 0.15, "embedding": 0.35, "traits": 0.2}


def tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_strings(item)


def _doc_tokens(asset: dict) -> list[str]:
    style = asset.get("style", {})
    domain_text = " ".join(_flatten_strings(asset.get("domain", {})))
    weighted = [
        (asset.get("title", ""), 3),
        (" ".join(asset.get("tags", [])), 3),
        (" ".join(style.get("traits", [])), 2),
        (style.get("shape_language", ""), 2),
        (asset.get("description", ""), 1),
        (style.get("composition", ""), 1),
        (style.get("intended_use", ""), 1),
        (" ".join(asset.get("correction_notes", [])), 1),
        (domain_text, 1),
    ]
    toks: list[str] = []
    for text, w in weighted:
        toks.extend(tokens(text) * w)
    return toks


def bm25_scores(query: str, docs: list[list[str]], k1: float = 1.4, b: float = 0.75) -> list[float]:
    q = tokens(query)
    if not q or not docs:
        return [0.0] * len(docs)
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df = Counter()
    for d in docs:
        df.update(set(d))
    scores = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for term in set(q):
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


# --- colour ----------------------------------------------------------------
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_lab(rgb: Sequence[float]) -> tuple[float, float, float]:
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def color_distance(a: str, b: str) -> float:
    la, lb = rgb_to_lab(hex_to_rgb(a)), rgb_to_lab(hex_to_rgb(b))
    return math.dist(la, lb)


def palette_similarity(p1: Sequence[str], p2: Sequence[str], scale: float = 60.0) -> float:
    """1.0 for identical palettes, falling toward 0 as the Lab distance grows (chamfer)."""
    if not p1 or not p2:
        return 0.0

    def one_way(src, dst):
        return sum(min(color_distance(a, b) for b in dst) for a in src) / len(src)

    d = (one_way(p1, p2) + one_way(p2, p1)) / 2
    return max(0.0, 1.0 - d / scale)


# --- filters -----------------------------------------------------------------
def _dig(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def matches_filters(asset: dict, filters: dict | None) -> bool:
    for key, want in (filters or {}).items():
        if key == "rating_min":
            if (asset.get("rating") or 0) < want:
                return False
        elif key == "tags_all":
            if not set(want) <= set(asset.get("tags", [])):
                return False
        elif key == "tags_any":
            if not set(want) & set(asset.get("tags", [])):
                return False
        elif key == "application":
            if (asset.get("application") or {}).get("name") != want:
                return False
        else:
            have = _dig(asset, key)
            if isinstance(want, (list, tuple, set)):
                if have not in want:
                    return False
            elif isinstance(have, list):
                if want not in have:
                    return False
            elif have != want:
                return False
    return True


def _summary(asset: dict, score: float, reasons: list[str]) -> dict:
    return {
        "id": asset["id"],
        "title": asset["title"],
        "kind": asset["kind"],
        "score": round(score, 4),
        "reasons": reasons,
        "tags": asset.get("tags", []),
        "path": asset.get("path"),
        "preview": asset.get("preview"),
        "source_file": asset.get("source_file"),
        "rating": asset.get("rating"),
        "status": asset["status"],
        "style": asset.get("style", {}),
        "correction_notes": asset.get("correction_notes", []),
        "domain": asset.get("domain", {}),
    }


def search(
    store: LibraryStore,
    query: str = "",
    *,
    filters: dict | None = None,
    tags: Iterable[str] = (),
    traits: Iterable[str] = (),
    palette: Sequence[str] = (),
    k: int = 5,
    embedder: Embedder | None = None,
    include_candidates: bool = False,
    include_negative: bool = True,
    weights: dict | None = None,
    image_query: str | None = None,
) -> dict:
    """Return ``{"positive": [...], "negative": [...], "considered": n}``."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    allowed_status = {"curated"} | ({"candidate"} if include_candidates else set())
    pool = [a for a in store.load() if a["status"] in allowed_status and matches_filters(a, filters)]
    tag_set = {t.lower() for t in tags}
    trait_set = {t.lower() for t in traits}

    bm = bm25_scores(query, [_doc_tokens(a) for a in pool])
    top_bm = max(bm) if bm and max(bm) > 0 else 1.0

    emb_scores: dict[str, float] = {}
    if embedder is not None and (query or image_query):
        vectors = load_vectors(store.project, embedder.name)
        qvec = embedder.embed_texts([query])[0] if query else None
        ivec = embedder.embed_images([image_query])[0] if image_query and embedder.supports_images else None
        for a in pool:
            vecs = vectors.get(a["id"], {})
            best = None
            if qvec is not None:
                for kind in ("text", "image"):  # CLIP-style models share one space
                    if kind in vecs and (kind == "text" or embedder.supports_images):
                        s = cosine(qvec, vecs[kind])
                        best = s if best is None else max(best, s)
            if ivec is not None and "image" in vecs:
                s = cosine(ivec, vecs["image"])
                best = s if best is None else max(best, s)
            if best is not None:
                emb_scores[a["id"]] = max(0.0, best)

    scored = []
    for a, raw in zip(pool, bm):
        parts: dict[str, float] = {}
        reasons: list[str] = []
        if query:
            parts["text"] = raw / top_bm
            if raw > 0:
                reasons.append(f"text match ({parts['text']:.2f})")
        a_tags = {t.lower() for t in a.get("tags", [])}
        if tag_set:
            overlap = tag_set & a_tags
            parts["tags"] = len(overlap) / len(tag_set)
            if overlap:
                reasons.append("tags: " + ", ".join(sorted(overlap)))
        a_traits = {t.lower() for t in a.get("style", {}).get("traits", [])}
        if trait_set:
            overlap = trait_set & a_traits
            parts["traits"] = len(overlap) / len(trait_set)
            if overlap:
                reasons.append("style traits: " + ", ".join(sorted(overlap)))
        if palette and a.get("style", {}).get("palette"):
            parts["palette"] = palette_similarity(palette, a["style"]["palette"])
            reasons.append(f"palette similarity {parts['palette']:.2f}")
        if a["id"] in emb_scores:
            parts["embedding"] = emb_scores[a["id"]]
            reasons.append(f"embedding similarity {emb_scores[a['id']]:.2f}")
        if parts:
            total_w = sum(w[p] for p in parts)
            score = sum(w[p] * v for p, v in parts.items()) / total_w
        else:  # filter-only query: order by rating
            score = (a.get("rating") or 0) / 5
            reasons.append("filter match")
        if a.get("rating"):
            score *= 0.9 + 0.02 * a["rating"]  # gentle preference for liked examples
        if score > 0 or not (query or tag_set or trait_set or palette):
            scored.append((score, a, reasons))

    scored.sort(key=lambda t: (-t[0], t[1]["id"]))
    positive = [_summary(a, s, r) for s, a, r in scored if a.get("polarity", "positive") == "positive"][:k]
    negative = (
        [_summary(a, s, r) for s, a, r in scored if a.get("polarity") == "negative"][:k] if include_negative else []
    )
    return {"positive": positive, "negative": negative, "considered": len(pool)}


# --- generic records (corrections, knowledge items) ----------------------------------------------------------
def search_items(items: Sequence[dict], query: str = "", *, tags: Iterable[str] = (), k: int = 5, text_fields: Sequence[str] = ("title", "text"),
                 tag_field: str = "tags", embedder: Embedder | None = None, vectors: dict[str, list[float]] | None = None,
                 weights: dict | None = None) -> list[dict]:
    """Hybrid ranking over plain dict records: BM25 text + tag overlap (+ embedding similarity when ``embedder`` and ``vectors`` are given).

    ``vectors`` maps ``item["id"]`` to a precomputed vector from the same ``embedder``. Without an embedder (the default and the only
    thing the test suite needs) ranking is text and tags only; every hit lists ``reasons``. Items scoring 0 are dropped.
    """
    w = {"text": 0.55, "tags": 0.25, "embedding": 0.35, **(weights or {})}
    docs = [tokens(" ".join(str(it.get(f, "")) for f in text_fields) + " " + " ".join(str(t) for t in it.get(tag_field, [])) * 2) for it in items]
    bm = bm25_scores(query, docs) if query else [0.0] * len(items)
    top = max(bm) if bm and max(bm) > 0 else 1.0
    want = {t.lower() for t in tags}
    qvec = embedder.embed_texts([query])[0] if (embedder is not None and vectors and query) else None
    out = []
    for it, raw in zip(items, bm):
        parts: dict[str, float] = {}
        reasons: list[str] = []
        if query:
            parts["text"] = raw / top
            if raw > 0:
                reasons.append(f"text match ({parts['text']:.2f})")
        if want:
            have = {str(t).lower() for t in it.get(tag_field, [])}
            parts["tags"] = len(want & have) / len(want)
            if want & have:
                reasons.append("tags: " + ", ".join(sorted(want & have)))
        if qvec is not None and it.get("id") in (vectors or {}):
            parts["embedding"] = max(0.0, cosine(qvec, vectors[it["id"]]))
            reasons.append(f"embedding similarity {parts['embedding']:.2f}")
        if not parts:
            continue
        score = sum(w[p] * v for p, v in parts.items()) / sum(w[p] for p in parts)
        if score > 0 and (not query or raw > 0 or parts.get("tags") or parts.get("embedding")):
            out.append({**it, "score": round(score, 4), "reasons": reasons})
    out.sort(key=lambda r: (-r["score"], str(r.get("id", ""))))
    return out[:k]
