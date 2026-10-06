"""Replaceable embedding providers.

* ``HashingEmbedder`` is dependency-free and local-only. It captures lexical
  similarity (word and character n-grams), not visual similarity.
* ``SentenceTransformersEmbedder`` loads a model from the optional
  ``sentence-transformers`` package. A CLIP-style model such as
  ``clip-ViT-B-32`` can embed both text and images, which enables
  image-to-image and text-to-image search over previews. This adapter is
  untested in CI (heavy download); see README for how to try it.

Vectors are stored per provider name, so switching providers never mixes
incompatible vectors.
"""

from __future__ import annotations

import math
import re
import zlib
from pathlib import Path
from typing import Protocol, Sequence

from .manifest import LibraryStore, read_jsonl, write_jsonl_atomic
from .project import Project


class Embedder(Protocol):
    name: str
    supports_images: bool

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_images(self, paths: Sequence[str]) -> list[list[float]]: ...


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are stored L2-normalized


class HashingEmbedder:
    supports_images = False

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str):
        words = re.findall(r"[a-z0-9]+", text.lower())
        for w in words:
            yield "w:" + w, 1.0
            padded = f"^{w}$"
            for i in range(len(padded) - 2):
                yield "c:" + padded[i : i + 3], 0.35

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat, weight in self._features(text):
                h = zlib.crc32(feat.encode("utf-8"))
                vec[h % self.dim] += weight if (h >> 16) & 1 else -weight
            out.append(_normalize(vec))
        return out

    def embed_images(self, paths: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("HashingEmbedder does not embed images; use a CLIP-style provider")


class SentenceTransformersEmbedder:
    def __init__(self, model: str = "clip-ViT-B-32"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the 'embeddings' extra: pip install '.[embeddings]'") from exc
        self._model = SentenceTransformer(model)
        self.name = f"st-{model}"
        self.supports_images = "clip" in model.lower()

    def embed_texts(self, texts):  # pragma: no cover - optional dependency
        return [_normalize(list(map(float, v))) for v in self._model.encode(list(texts))]

    def embed_images(self, paths):  # pragma: no cover - optional dependency
        from PIL import Image

        imgs = [Image.open(p).convert("RGB") for p in paths]
        return [_normalize(list(map(float, v))) for v in self._model.encode(imgs)]


def load_embedder(name: str | None) -> Embedder | None:
    if not name or name == "none":
        return None
    if name.startswith("hashing"):
        return HashingEmbedder(int(name.split("-")[1]) if "-" in name else 512)
    if name.startswith("st:"):
        return SentenceTransformersEmbedder(name[3:])
    raise ValueError(f"unknown embedder '{name}'. Use 'none', 'hashing[-DIM]' or 'st:<model>'")


def asset_text(asset: dict) -> str:
    style = asset.get("style", {})
    parts = [
        asset.get("title", ""),
        asset.get("description", ""),
        " ".join(asset.get("tags", [])),
        " ".join(style.get("traits", [])),
        style.get("shape_language", ""),
        style.get("composition", ""),
        style.get("intended_use", ""),
        " ".join(asset.get("correction_notes", [])),
    ]
    return " . ".join(p for p in parts if p)


def build_index(project: Project, embedder: Embedder, store: LibraryStore | None = None) -> dict:
    """(Re)build vectors for every asset under this provider name."""
    store = store or LibraryStore(project)
    assets = store.load()
    rows = [r for r in read_jsonl(project.embeddings_file) if r.get("provider") != embedder.name]
    text_vecs = embedder.embed_texts([asset_text(a) for a in assets]) if assets else []
    image_count = 0
    for asset, tvec in zip(assets, text_vecs):
        rows.append({"id": asset["id"], "provider": embedder.name, "kind": "text", "vector": tvec})
        if embedder.supports_images:
            img = store.resolve_path(asset.get("preview"))
            if img and Path(img).exists():
                rows.append({"id": asset["id"], "provider": embedder.name, "kind": "image",
                             "vector": embedder.embed_images([str(img)])[0]})
                image_count += 1
    write_jsonl_atomic(project.embeddings_file, rows)
    return {"provider": embedder.name, "assets": len(assets), "image_vectors": image_count,
            "file": str(project.embeddings_file)}


def load_vectors(project: Project, provider: str) -> dict[str, dict[str, list[float]]]:
    """{asset_id: {"text": vec, "image": vec}} for one provider."""
    out: dict[str, dict[str, list[float]]] = {}
    for row in read_jsonl(project.embeddings_file):
        if row.get("provider") == provider:
            out.setdefault(row["id"], {})[row["kind"]] = row["vector"]
    return out
