"""Rig, naming and upload-risk checks."""

from __future__ import annotations

import re
from pathlib import Path

from .engine import Ctx, implements


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


# ------------------------------------------------------------------------------------------------ names
def sanitize_name(name: str, pattern: str = r"[^A-Za-z0-9_.-]+") -> str:
    out = re.sub(pattern, "_", name).strip("_") or "object"
    return out


def strip_blender_suffix(name: str) -> str:
    return re.sub(r"\.\d{3,}$", "", name) or name


def clean_name(name: str) -> str:
    """The name the safe fixes propose: no odd characters, no Blender .001 suffix."""
    return sanitize_name(strip_blender_suffix(name))


def sanitize_bone(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", strip_blender_suffix(name).lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "bone_" + s if s else "bone"
    return s


@implements("NAME_DUPLICATE", "NAME_DUPLICATE_MATERIAL", "NAME_INVALID_CHARS", "NAME_BLENDER_SUFFIX", "NAME_SCHEME", "NAME_UNNAMED", "NAME_TOO_LONG")
def names(ctx: Ctx):
    objs = ctx.objects
    seen: dict[str, list[dict]] = {}
    for o in objs:
        seen.setdefault(o["name"], []).append(o)
    for n, group in seen.items():
        if len(group) > 1 and not all(o.get("unnamed") for o in group):
            kinds = ",".join(sorted({o["kind"] for o in group}))
            ctx.add("NAME_DUPLICATE", n, len(group), 1, message=f"{len(group)} objects are named '{n}' ({kinds})",
                    fix_op={"op": "rename_duplicates", "name": n, "occurrences": [{"kind": o["kind"], "id": o["id"]} for o in group]})
    mats: dict[str, int] = {}
    for m in ctx.asset["materials"]:
        if m.get("name"):
            mats[m["name"]] = mats.get(m["name"], 0) + 1
    for n, c in mats.items():
        if c > 1:
            ctx.add("NAME_DUPLICATE_MATERIAL", n, c, 1, message=f"{c} materials are named '{n}'")
    allowed = re.compile(ctx.limit("NAME_INVALID_CHARS", "allowed_pattern"))
    suffix = re.compile(ctx.limit("NAME_BLENDER_SUFFIX", "suffix_pattern"))
    maxlen = ctx.limit("NAME_TOO_LONG", "max_length")
    for o in objs:
        n = o["name"]
        if o.get("unnamed"):
            ctx.add("NAME_UNNAMED", n, "no name", "a name", message="the node has no name (the exporter wrote none)")
            continue
        if len(n) > maxlen:
            ctx.add("NAME_TOO_LONG", n, len(n), maxlen, limit_key="max_length", message=f"{len(n)} characters")
        if o["kind"] == "bone":
            continue
        if not allowed.match(n):
            new = clean_name(n)
            ctx.add("NAME_INVALID_CHARS", n, n, new, message=f"'{n}' contains spaces or characters outside the allowed set",
                    fix_op={"op": "rename", "object": n, "kind": o["kind"]})
        if suffix.search(n):
            ctx.add("NAME_BLENDER_SUFFIX", n, n, strip_blender_suffix(n), message=f"'{n}' ends in a Blender duplicate suffix", fix_op={"op": "rename", "object": n, "kind": o["kind"]})
    scheme = (ctx.cfg.style.get("naming") or {})
    pat = (scheme.get("mesh_patterns") or {}).get(ctx.profile_name)
    proxy_pat = scheme.get("collision_pattern")
    if pat:
        rx = re.compile(pat)
        for o in ctx.visual:
            if not rx.match(o["name"]) and not o.get("unnamed"):
                ctx.add("NAME_SCHEME", o["name"], o["name"], pat, message=f"'{o['name']}' does not match the {ctx.profile_name} naming scheme")
    if proxy_pat:
        rx = re.compile(proxy_pat)
        for o in ctx.proxies:
            if not rx.match(o["name"]):
                ctx.add("NAME_SCHEME", o["name"], o["name"], proxy_pat, message=f"collision proxy '{o['name']}' does not match the proxy naming scheme")


# -------------------------------------------------------------------------------------------------- rig
def _skins(ctx: Ctx):
    return ctx.asset.get("skins") or []


@implements("RIG_UNEXPECTED", "RIG_REQUIRED", "RIG_ROOT_SINGLE", "RIG_ROOT_NAME", "RIG_BONE_NAMING", "RIG_BONE_COUNT", "RIG_SPIN_SETUP", "RIG_INFLUENCES", "RIG_WEIGHTS",
            "RIG_ANIM_NAMES", "RIG_ANIM_FPS")
def rig_checks(ctx: Ctx):
    skins = _skins(ctx)
    ctx.state["handled_bones"] = set()
    if ctx.enabled("RIG_UNEXPECTED") and skins:
        nb = sum(len(s["joint_names"]) for s in skins)
        ctx.add("RIG_UNEXPECTED", skins[0].get("name") or "armature", f"{len(skins)} skin(s), {nb} bone(s)", "no rig", message=f"a rig is present but the {ctx.profile_name} profile has no rig checks")
    if not ctx.profile.get("rig_checks"):
        return
    if ctx.limit("RIG_REQUIRED", "required") and not skins:
        ctx.add("RIG_REQUIRED", None, "no skin", "a skin", message="the profile requires a rig but the export has no skin", limit_key=None)
    root_name = ctx.limit("RIG_ROOT_NAME", "root_name")
    bone_pat = re.compile(ctx.limit("RIG_BONE_NAMING", "pattern"))
    maxb = ctx.limit("RIG_BONE_COUNT", "max_bones")
    for s in skins:
        roots = s["root_joints"]
        if len(roots) != 1:
            ctx.add("RIG_ROOT_SINGLE", s.get("name") or "armature", len(roots), "exactly 1", message=f"{len(roots)} root bones: {', '.join(roots) or 'none'}", fix_vars={"limit": ", ".join(roots) or "none"})
        if len(s["joint_names"]) > maxb:
            ctx.add("RIG_BONE_COUNT", s.get("name") or "armature", len(s["joint_names"]), maxb, limit_key="max_bones", message=f"{len(s['joint_names'])} bones")
    _spin(ctx, skins)
    handled = ctx.state["handled_bones"]
    for s in skins:
        roots = s["root_joints"]
        if len(roots) == 1 and roots[0] != root_name and roots[0] not in handled:
            ctx.add("RIG_ROOT_NAME", roots[0], roots[0], root_name, message=f"root bone is '{roots[0]}'", fix_op={"op": "rename_bone", "bone": roots[0], "new": root_name})
            handled.add(roots[0])
        for b in s["joint_names"]:
            if b in handled:
                continue
            if not bone_pat.match(b):
                ctx.add("RIG_BONE_NAMING", b, b, ctx.limit("RIG_BONE_NAMING", "pattern"), message=f"bone '{b}' breaks the pattern", fix_op={"op": "rename_bone", "bone": b, "new": sanitize_bone(b)})
    maxinf = ctx.limit("RIG_INFLUENCES", "max_influences")
    for o in ctx.visual:
        sk = ctx.metrics[o["id"]].get("skin")
        if not sk:
            continue
        if sk["max_influences"] > maxinf:
            ctx.add("RIG_INFLUENCES", o["name"], sk["max_influences"], maxinf, limit_key="max_influences", message=f"up to {sk['max_influences']} bone influences on one vertex")
        if sk["unweighted_vertices"] or sk["weight_sum_bad"]:
            ctx.add("RIG_WEIGHTS", o["name"], f"{sk['unweighted_vertices']} unweighted, {sk['weight_sum_bad']} not summing to 1", "0 / 0",
                    message="some vertices have no skin weights or weights that do not sum to 1", fix_vars={"measured": f"{sk['unweighted_vertices']} unweighted, {sk['weight_sum_bad']} unnormalised"})
    pat = re.compile(ctx.limit("RIG_ANIM_NAMES", "pattern"))
    forb = {x.lower() for x in ctx.limit("RIG_ANIM_NAMES", "forbidden")}
    fps_ok = ctx.limit("RIG_ANIM_FPS", "allowed_fps")
    tol = ctx.limit("RIG_ANIM_FPS", "tolerance")
    for a in ctx.asset.get("animations", []):
        n = a.get("name") or ""
        if not n or n.lower() in forb or not pat.match(n):
            ctx.add("RIG_ANIM_NAMES", n or f"clip_{a['index']}", n or "(no name)", ctx.limit("RIG_ANIM_NAMES", "pattern"), message=f"animation clip name '{n}' is a default or breaks the pattern")
        fps = a.get("frame_rate")
        if fps is not None and not any(abs(fps - x) <= tol for x in fps_ok):
            ctx.add("RIG_ANIM_FPS", n or f"clip_{a['index']}", _r(fps, 2), fps_ok, limit_key="allowed_fps", message=f"clip runs at about {_r(fps, 2)} fps ({a.get('frame_rate_note', '')})")


def _fuzzy_spin(name: str) -> bool:
    return "spin" in re.sub(r"[^a-z]", "", name.lower())


def _spin(ctx: Ctx, skins: list[dict]):
    spin = ctx.limit("RIG_SPIN_SETUP", "spin_name")
    hint = re.compile(ctx.limit("RIG_SPIN_SETUP", "hint_pattern"), re.I)
    max_spin = ctx.limit("RIG_SPIN_SETUP", "max_spin_bones")
    min_rigid = ctx.limit("RIG_SPIN_SETUP", "min_rigid_fraction")
    names_to_test = [ctx.asset.get("name", "")] + [o["name"] for o in ctx.visual]
    hinted = any(hint.search(n) for n in names_to_test if n)
    all_bones = [(s, b) for s in skins for b in s["joint_names"]]
    exact = [(s, b) for s, b in all_bones if b == spin]
    fuzzy = [(s, b) for s, b in all_bones if b != spin and _fuzzy_spin(b)]
    where = ctx.visual[0]["name"] if ctx.visual else None
    if not exact:
        if len(fuzzy) == 1:
            s, b = fuzzy[0]
            ctx.add("RIG_SPIN_SETUP", b, b, spin, message=f"bone '{b}' looks like the spin bone but is not named '{spin}'", fix_op={"op": "rename_bone", "bone": b, "new": spin},
                    fix_vars={"message": f"Rename bone '{b}' to '{spin}' (the one bone that drives the spinning part)."})
            ctx.state["handled_bones"].add(b)
        elif len(fuzzy) > 1:
            ctx.add("RIG_SPIN_SETUP", ", ".join(b for _, b in fuzzy), len(fuzzy), 1, message=f"{len(fuzzy)} bones look like spin bones and none is named '{spin}'",
                    fix_vars={"message": f"Keep exactly one spin bone and name it '{spin}'; candidates: {', '.join(b for _, b in fuzzy)}."})
        elif hinted:
            ctx.add("RIG_SPIN_SETUP", where, "no spin bone", f"one bone named '{spin}'", message=f"the asset name suggests a spinning part but there is no bone named '{spin}'" + ("" if skins else " (and no skin at all)"),
                    fix_vars={"message": f"Add one bone named '{spin}' under the root bone and weight the spinning part fully to it."})
        return
    if len(exact) > max_spin:
        ctx.add("RIG_SPIN_SETUP", spin, len(exact), max_spin, message=f"{len(exact)} bones are named '{spin}'", fix_vars={"message": f"Keep exactly {max_spin} bone named '{spin}'."})
        return
    s, b = exact[0]
    roots = s["root_joints"]
    parent = s["parent_of"].get(b)
    if len(roots) == 1 and parent != roots[0]:
        ctx.add("RIG_SPIN_SETUP", b, f"parent is {parent or 'none'}", f"direct child of '{roots[0]}'", message=f"'{b}' is not a direct child of the root bone",
                fix_vars={"message": f"Parent '{b}' directly to the root bone '{roots[0]}'."})
    kids = [c for c, p in s["parent_of"].items() if p == b]
    if kids:
        ctx.add("RIG_SPIN_SETUP", b, f"{len(kids)} child bone(s): {', '.join(kids)}", "no child bones", message=f"'{b}' has child bones",
                fix_vars={"message": f"Remove or re-parent the child bones of '{b}' so it is a single leaf bone."})
    # vertex ownership (only when weights were read from the file)
    idx = s["joint_names"].index(b)
    total, rigid, seen_weights = 0, 0, False
    for o in ctx.visual:
        if o.get("skin") != s["index"]:
            continue
        sk = ctx.metrics[o["id"]].get("skin")
        if not sk:
            continue
        seen_weights = True
        total += sk["joint_vertices"].get(idx, 0)
        rigid += sk["joint_rigid_vertices"].get(idx, 0)
    if seen_weights:
        if total == 0:
            ctx.add("RIG_SPIN_SETUP", b, "0 vertices", ">= 1 vertex", message=f"'{b}' drives no vertices", fix_vars={"message": f"Weight the spinning part fully to '{b}' (Weight Paint, Assign 1.0)."})
        elif rigid / total < min_rigid:
            ctx.add("RIG_SPIN_SETUP", b, f"{round(rigid / total * 100, 1)}% rigid", f">= {round(min_rigid * 100)}%", message=f"only {round(rigid / total * 100, 1)}% of '{b}''s vertices are fully weighted to it",
                    fix_vars={"message": f"Give the vertices of the spinning part a weight of 1.0 on '{b}' (no blending with other bones)."})
    else:
        ctx.asset["not_measurable"].append({"what": "spin bone vertex weights", "why": "no skin weights were read (summary input or unskinned mesh)"}) if not any(
            x["what"] == "spin bone vertex weights" for x in ctx.asset["not_measurable"]) else None


# ------------------------------------------------------------------------------------------------ upload
@implements("UPL_NOT_MEASURED", "UPL_REPORTED_ONLY", "UPL_FORMAT", "UPL_FILE_SIZE", "UPL_FILE_SIZE_LARGE", "UPL_TEXTURE_MEMORY", "UPL_UNSUPPORTED_FEATURE")
def upload_risk(ctx: Ctx):
    a = ctx.asset
    if a["format"] == "fbx" and not a["objects"]:
        ctx.add("UPL_NOT_MEASURED", Path(a["path"]).name, "FBX geometry not parsed", "measured geometry", message=f"FBX header read (version {a.get('fbx', {}).get('version')}) but nothing inside the file is parsed",
                fix_vars={"measured": "FBX header only"})
    if "summary" in a.get("reader", ""):
        ctx.add("UPL_REPORTED_ONLY", None, a.get("summary_source", "summary"), "measured from the file", message="geometry numbers were reported by the summary, not measured from the file", source="reported")
    fmts = [x.lower() for x in ctx.limit("UPL_FORMAT", "accepted_formats")]
    if a["format"] not in ("summary",) and a["format"] not in fmts:
        ctx.add("UPL_FORMAT", Path(a["path"]).name, a["format"], fmts, limit_key="accepted_formats", message=f"{a['format']} is not in the accepted formats")
    if a.get("file_size") is not None:
        ext = sum((im.get("byte_size") or 0) for im in a["images"] if not im.get("embedded"))
        mb = (a["file_size"] + ext) / (1024 * 1024)
        mx, wr = ctx.limit("UPL_FILE_SIZE", "max_mb"), ctx.limit("UPL_FILE_SIZE_LARGE", "warn_mb")
        if mb > mx:
            ctx.add("UPL_FILE_SIZE", Path(a["path"]).name, _r(mb, 3), mx, limit_key="max_mb", message=f"{_r(mb, 2)} MB including external textures")
        elif mb > wr:
            ctx.add("UPL_FILE_SIZE_LARGE", Path(a["path"]).name, _r(mb, 3), wr, limit_key="warn_mb", message=f"{_r(mb, 2)} MB including external textures")
    mem = sum((im["width"] * im["height"] * 4 * 1.33) for im in a["images"] if im.get("width") and im.get("height")) / (1024 * 1024)
    lim = ctx.limit("UPL_TEXTURE_MEMORY", "max_texture_megabytes")
    if mem > lim:
        ctx.add("UPL_TEXTURE_MEMORY", None, _r(mem, 1), lim, limit_key="max_texture_megabytes", message=f"about {_r(mem, 1)} MB of texture memory (RGBA8 with mips)")
    flagged_ext = set(ctx.limit("UPL_UNSUPPORTED_FEATURE", "flagged_extensions"))
    for e in a.get("extensions_used", []):
        if e in flagged_ext:
            ctx.add("UPL_UNSUPPORTED_FEATURE", e, f"glTF extension {e}", "not used", limit_key="flagged_extensions", message=f"the glTF extension {e} may be ignored on import")
    for o in ctx.objects:
        if o.get("has_camera"):
            ctx.add("UPL_UNSUPPORTED_FEATURE", o["name"], "camera node", "none", message="the export contains a camera")
        if o.get("has_light"):
            ctx.add("UPL_UNSUPPORTED_FEATURE", o["name"], "light node", "none", message="the export contains a light")
    maxuv = ctx.limit("UPL_UNSUPPORTED_FEATURE", "max_uv_sets")
    for o in ctx.meshes:
        m = o["mesh"]
        if m.get("morph_targets") and ctx.limit("UPL_UNSUPPORTED_FEATURE", "flag_morph_targets"):
            ctx.add("UPL_UNSUPPORTED_FEATURE", o["name"], "morph targets", "none", limit_key="flag_morph_targets", message="the mesh has morph targets (shape keys) that may be dropped")
        if (m.get("uv_sets") or 0) > maxuv:
            ctx.add("UPL_UNSUPPORTED_FEATURE", o["name"], f"{m['uv_sets']} UV sets", maxuv, limit_key="max_uv_sets", message=f"{m['uv_sets']} UV sets; only the first is checked")
