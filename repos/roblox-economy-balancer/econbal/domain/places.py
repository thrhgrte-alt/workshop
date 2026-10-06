"""Projects and places: the registry, scope resolution, per-place bands and the open-Studio check.

``projects.yaml`` (or the file named by ``$ECONBAL_PROJECTS``) lists projects and their places::

    projects:
      - project_id: demo_mine            # [A-Za-z][A-Za-z0-9_]*
        alias: Demo Mine (SYNTHETIC)
        universe_id: 0                   # 0 = unknown or unpublished
        places:
          - place_id: main
            alias: Main mine
            studio_name: "Demo Mine Main (SYNTHETIC)"   # what Studio shows for the open place
            roblox_place_id: 0                           # 0 = unpublished; then the Studio name is matched instead
            spec: projects/demo_mine/main/economy.yaml   # relative to the registry file
            bands: projects/demo_mine/main/bands.yaml    # optional per-place target pacing, layered over style/style.yaml

Nothing is guessed: a tool without a resolvable (project_id, place_id) refuses and lists what is known.

Open-Studio input shape (the output of the hub's ``list_roblox_studios``, passed in as DATA; this package never calls the hub)::

    studios: [{"name": "Demo Mine Main (SYNTHETIC)", "place_id": 0, "studio_id": "abc123"}, ...]
    # accepted aliases: name|placeName|title, place_id|placeId|PlaceId, studio_id|studioId|id
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

from ..guide_adapter import config

ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
STUDIO_NAME_RE = re.compile(r"^[A-Za-z0-9 _.()\-]{1,60}$")


def registry_path(project) -> Path:
    explicit = project.env("PROJECTS")
    return Path(explicit).expanduser() if explicit else project.root / "projects.yaml"


def load_registry(project) -> dict[tuple[str, str], dict]:
    path = registry_path(project)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[tuple[str, str], dict] = {}
    for proj in data.get("projects") or []:
        pid = proj.get("project_id")
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise ValueError(f"{path}: project_id {pid!r} must match {ID_RE.pattern}")
        for pl in proj.get("places") or []:
            plid = pl.get("place_id")
            if not isinstance(plid, str) or not ID_RE.match(plid):
                raise ValueError(f"{path}: place_id {plid!r} in project '{pid}' must match {ID_RE.pattern}")
            if (pid, plid) in out:
                raise ValueError(f"{path}: duplicate place '{pid}/{plid}'")
            base = path.parent
            spec = Path(pl.get("spec") or f"projects/{pid}/{plid}/economy.yaml")
            bands = Path(pl.get("bands") or f"projects/{pid}/{plid}/bands.yaml")
            out[(pid, plid)] = {"project_id": pid, "place_id": plid, "project_alias": proj.get("alias", pid), "place_alias": pl.get("alias", plid),
                                "universe_id": proj.get("universe_id", 0), "roblox_place_id": pl.get("roblox_place_id", 0), "studio_name": pl.get("studio_name"),
                                "synthetic": bool(proj.get("synthetic") or pl.get("synthetic")), "spec_path": spec if spec.is_absolute() else base / spec,
                                "bands_path": bands if bands.is_absolute() else base / bands}
    return out


def known(reg: dict) -> list[str]:
    return sorted(f"{p}/{pl}" for p, pl in reg)


def resolve(project, project_id: str | None, place_id: str | None) -> dict:
    """The registry entry for exactly this project and place, or a refusal that says what is missing."""
    reg = load_registry(project)
    missing = [n for n, v in (("project_id", project_id), ("place_id", place_id)) if not v]
    if missing:
        raise ValueError(f"missing {' and '.join(missing)}: every tool needs both so that specs, bands and feedback are never mixed between places. "
                         f"Known places: {known(reg) or 'none (create projects.yaml; the bundled one lists synthetic demo places)'}")
    if not isinstance(project_id, str) or not isinstance(place_id, str):
        raise ValueError("project_id and place_id must be strings")
    if (project_id, place_id) not in reg:
        projects = sorted({p for p, _ in reg})
        hint = f"project '{project_id}' has places {sorted(pl for p, pl in reg if p == project_id)}" if project_id in projects else f"known projects: {projects}"
        raise ValueError(f"unknown place '{project_id}/{place_id}' ({hint}). Known places: {known(reg)}. Add it to {registry_path(project)} first; the tool will not guess.")
    return reg[(project_id, place_id)]


def scope_info(entry: dict) -> dict:
    return {"project_id": entry["project_id"], "place_id": entry["place_id"], "place": entry["place_alias"], "synthetic": entry["synthetic"]}


def layered_style(project, entry: dict) -> tuple[dict, dict]:
    """Global style/style.yaml, with the place's bands.yaml layered on top. Returns (style, layers_info)."""
    style = copy.deepcopy(config.load_style(project))
    info = {"global": str(project.style_file), "place_bands": None}
    bp = Path(entry["bands_path"])
    if bp.exists():
        over = yaml.safe_load(bp.read_text(encoding="utf-8")) or {}
        if "target_bands" in over:
            style["target_bands"] = over["target_bands"]
        if "reference_archetypes" in over:
            style["reference_archetypes"] = over["reference_archetypes"]
        for k in ("ranges", "learning"):
            style.setdefault(k, {})
            style[k] = {**style[k], **(over.get(k) or {})}
        style["placeholder"] = bool(over.get("placeholder", False)) if "target_bands" in over else style.get("placeholder", False)
        if "target_bands" in over:
            style["name"] = over.get("name", f"Pacing bands for {entry['project_id']}/{entry['place_id']}")
        info["place_bands"] = str(bp)
    return style, info


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def match_studio(entry: dict, studios: Any) -> dict:
    """Pick the open Studio instance that IS this place; refuse if none (or more than one) matches."""
    if not isinstance(studios, list) or not studios:
        raise ValueError(f"cannot confirm the open Studio place: pass 'studios', the output of the hub's list_roblox_studios as a list of "
                         f"{{name, place_id, studio_id}}, and open place '{entry['project_id']}/{entry['place_id']}' ({entry['studio_name']}) in Studio. Nothing was generated.")
    rows = []
    for s in studios:
        if not isinstance(s, dict):
            raise ValueError("each studios entry must be a mapping like {name, place_id, studio_id}")
        rows.append({"name": s.get("name") or s.get("placeName") or s.get("title"), "place_id": s.get("place_id", s.get("placeId", s.get("PlaceId"))),
                     "studio_id": s.get("studio_id") or s.get("studioId") or s.get("id")})
    hits = []
    for r in rows:
        by_id = entry["roblox_place_id"] and str(r["place_id"]) == str(entry["roblox_place_id"])
        by_name = entry["studio_name"] and _norm(r["name"]) == _norm(entry["studio_name"])
        if by_id or by_name:
            hits.append({**r, "matched_by": "place_id" if by_id else "name"})
    if not hits:
        open_ = [f"{r['name']!r} (place_id {r['place_id']})" for r in rows]
        raise ValueError(f"refusing: the open Studio instance(s) {open_} are not place '{entry['project_id']}/{entry['place_id']}' "
                         f"(expected name {entry['studio_name']!r} or place id {entry['roblox_place_id']}). Open the right place; nothing was generated.")
    if len(hits) > 1:
        raise ValueError(f"refusing: {len(hits)} open Studio instances match place '{entry['project_id']}/{entry['place_id']}'; close the extra ones so the target is unambiguous.")
    return hits[0]


def embeddable_identity(entry: dict) -> tuple[int, str]:
    name = entry["studio_name"] or ""
    if not STUDIO_NAME_RE.match(name):
        raise ValueError(f"place '{entry['project_id']}/{entry['place_id']}' has a studio_name {name!r} that cannot be embedded safely in Luau "
                         f"(allowed: letters, digits, space, _ . ( ) -; 1-60 chars). Fix projects.yaml.")
    pid = entry["roblox_place_id"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 0:
        raise ValueError("roblox_place_id must be a non-negative integer (0 = unpublished)")
    return pid, name
