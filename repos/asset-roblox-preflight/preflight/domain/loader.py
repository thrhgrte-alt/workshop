"""Load an export file (GLB, glTF, OBJ, FBX header, or a hub summary JSON) into the normalised asset dict."""

from __future__ import annotations

import urllib.parse
from pathlib import Path

from . import gltf, objio, summary

EXPORT_EXTENSIONS = (".glb", ".gltf", ".obj", ".fbx", ".json")


def make_resolver(base_dir: Path, texture_dir: Path | None):
    """Map a relative uri to a file inside the asset folder or the texture folder (never outside them)."""
    roots = [r.resolve() for r in (base_dir, texture_dir) if r is not None]

    def resolve(uri: str) -> Path | None:
        u = urllib.parse.unquote(uri).replace("\\", "/")
        if gltf._is_unsafe_uri(u):
            return None
        first = None
        for root in roots:
            cand = root / u
            first = first or cand
            try:
                cand.resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            if cand.exists():
                return cand
        return first

    return resolve


def load_export(path: Path, *, texture_dir: Path | None = None, summary_doc: dict | None = None) -> dict:
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"'{path}' does not exist or is not a file")
    ext = path.suffix.lower()
    if ext not in EXPORT_EXTENSIONS:
        raise ValueError(f"unsupported export type '{ext}'. Supported: .glb, .gltf, .obj, .fbx (header only, needs a summary), or a hub summary .json")
    resolve = make_resolver(path.parent, texture_dir)
    if ext in (".glb", ".gltf"):
        asset = gltf.read_glb_or_gltf(path, resolve)
    elif ext == ".obj":
        asset = objio.read_obj(path, resolve)
    elif ext == ".fbx":
        asset = summary.load_fbx(path)
    else:
        doc = summary.read_summary_file(path)
        asset = summary.summary_only_asset(doc, str(path), resolve)
        summary_doc = None
    if summary_doc is not None:
        if ext in (".glb", ".gltf", ".obj"):
            asset["warnings"].append("a summary was supplied together with a readable export; the export itself was measured and the summary was ignored")
        else:
            asset = summary.merge_summary(asset, summary_doc, resolve)
    asset["search_dirs"] = [str(path.parent)] + ([str(texture_dir)] if texture_dir else [])
    asset.setdefault("geometry_measured", ext != ".fbx" and ext != ".json")
    asset["name"] = path.stem
    return asset
