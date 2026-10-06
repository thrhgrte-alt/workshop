"""Build, render and compare preflight reports."""

from __future__ import annotations

from pathlib import Path

from . import checks_geometry, checks_rig_names, checks_surface  # noqa: F401  (importing registers the checks)
from .engine import Ctx, run_checks
from .rules import SEV_ORDER, Config, Overrides

TOOL_GROUPS = {
    "check_mesh": {"mesh", "transform", "collision"},
    "check_uvs": {"uv"},
    "check_textures": {"materials"},
    "check_rig": {"rig"},
    "check_names": {"names"},
    "preflight_report": None,  # everything, including upload risk
}
RULE_DOC_NOTE = ("Limits are DEFAULTS chosen by this tool (see rules/ and rules/profiles/), not official Roblox limits. Findings marked verify_against_current_docs rest on "
                 "numbers that must be checked against the current Roblox documentation.")


def facts(ctx: Ctx) -> dict:
    a = ctx.asset
    lo, hi, dims = ctx.visual_dims_world()
    imgs = [{"name": i.get("uri") or i.get("name"), "size": f"{i['width']}x{i['height']}" if i.get("width") else None, "format": i.get("format"), "exists": i.get("exists", True) or i.get("embedded")} for i in a["images"]]
    return {
        "triangles": ctx.visual_triangles,
        "visible_meshes": [o["name"] for o in ctx.visual],
        "collision_proxies": [o["name"] for o in ctx.proxies],
        "collision_status": ctx.state.get("collision_status"),
        "size_studs": None if dims is None else [round(d * ctx.studs, 4) for d in dims],
        "materials": len(a["materials"]),
        "textures": imgs,
        "bones": sum(len(s["joint_names"]) for s in a.get("skins", [])),
        "animations": [x.get("name") for x in a.get("animations", [])],
        "file_size_bytes": a.get("file_size"),
        "reader": a.get("reader"),
        "geometry_measured": a.get("geometry_measured", True),
    }


def build_report(asset: dict, cfg: Config, profile: str, *, overrides: Overrides | None = None, collision_intent: str = "auto", tool: str = "preflight_report",
                 project: dict | None = None, place_id=None) -> dict:
    cats = TOOL_GROUPS[tool]
    ctx = Ctx(asset, cfg, profile, overrides, collision_intent)
    findings = run_checks(ctx, cats)
    errs = sum(f["severity"] == "error" for f in findings)
    warns = sum(f["severity"] == "warn" for f in findings)
    infos = sum(f["severity"] == "info" for f in findings)
    full = cats is None
    ready = (errs == 0) if full else None
    skipped = []
    for rid, r in cfg.rules.items():
        if cats is not None and r["category"] not in cats:
            continue
        if not ctx.enabled(rid):
            why = "disabled in rules" if not r.get("enabled", True) or rid in (ctx.profile.get("disabled_rules") or []) else f"does not apply to the {profile} profile"
            skipped.append({"rule": rid, "reason": why})
    verify = sorted({f"{f['rule']}" for f in findings if f.get("verify_against_current_docs")})
    summary = {"result": "pass" if errs == 0 else "fail", "errors": errs, "warnings": warns, "infos": infos, "ready_to_upload": ready, "profile": profile,
               "asset": asset.get("name"), "format": asset["format"], "scope": "all" if full else sorted(cats), "tool": tool,
               "project": (project or {}).get("id"), "place_id": place_id if place_id is not None else (project or {}).get("place_id"),
               "place_registered": (project or {}).get("place_id")}
    for n, f in enumerate(findings):
        f["id"] = f"{f['rule']}:{f['object']}"
    report = {
        "summary": summary,
        "findings": findings,
        "facts": facts(ctx),
        "not_checked": asset.get("not_measurable", []) + [{"what": s["rule"], "why": s["reason"]} for s in skipped if "profile" in s["reason"] and False],
        "skipped_rules": skipped,
        "rules_run": len(ctx.checked_rules),
        "overrides_applied": ctx.used_overrides,
        "assumptions": {"limits_are_defaults": True, "note": RULE_DOC_NOTE, "rules_to_verify_against_current_docs": verify, "profile": profile,
                        "studs_per_unit": ctx.studs},
        "warnings": asset.get("warnings", []),
        "handoff": None,
    }
    if full:
        if ready:
            report["handoff"] = {"next_tool": "roblox_upload_plan", "via": "hub",
                                 "note": ("Preflight found no errors against this profile's DEFAULT limits. This is not a guarantee that Roblox will accept the upload. Hand the file to the "
                                          "hub's roblox_upload_plan; this server never uploads or publishes."),
                                 "inputs": {"file": asset.get("path"), "project_id": (project or {}).get("id"), "place_id": place_id if place_id is not None else (project or {}).get("place_id"), "profile": profile, "triangles": report["facts"]["triangles"], "file_size_bytes": asset.get("file_size")}}
        else:
            report["handoff"] = None
            report["blocked_by"] = [f"{f['rule']} ({f['object']})" for f in findings if f["severity"] == "error"]
    report["text"] = render_text(report)
    return report


def render_text(report: dict) -> str:
    s = report["summary"]
    lines = []
    head = "PASS" if s["result"] == "pass" else "FAIL"
    ready = "" if s["ready_to_upload"] is None else (" | ready_to_upload: YES" if s["ready_to_upload"] else " | ready_to_upload: NO")
    lines.append(f"{head}: {s['asset']} ({s['format']}, project {s.get('project')}, place {s.get('place_id')}, profile {s['profile']}) - {s['errors']} error(s), {s['warnings']} warning(s), {s['infos']} info{ready}")
    if s["scope"] != "all":
        lines.append(f"(partial run: {', '.join(s['scope'])} rules only; ready_to_upload needs preflight_report)")
    for sev in ("error", "warn", "info"):
        group = [f for f in report["findings"] if f["severity"] == sev]
        if not group:
            continue
        lines.append("")
        lines.append(f"{sev.upper()} ({len(group)})")
        for f in group:
            flag = " [override]" if f.get("overridden") else ""
            lines.append(f"- {f['rule']} | {f['object'] if f['object'] is not None else '(asset)'} | measured: {f['measured']} | limit: {f['limit']}{flag}")
            lines.append(f"    fix: {f['fix']}")
    if report["not_checked"]:
        lines.append("")
        lines.append("NOT CHECKED")
        for n in report["not_checked"]:
            lines.append(f"- {n['what']}: {n['why']}")
    if s["ready_to_upload"]:
        lines.append("")
        lines.append("Next: hand off to roblox_upload_plan (hub). This tool never uploads. Limits are defaults, verify them.")
    return "\n".join(lines)


def finding_key(f: dict) -> tuple:
    return (f["rule"], f["object"])


def compare_reports(before: dict, after: dict) -> dict:
    b = {finding_key(f): f for f in before["findings"]}
    a = {finding_key(f): f for f in after["findings"]}
    fixed = [b[k] for k in b if k not in a]
    new = [a[k] for k in a if k not in b]
    changed = [{"rule": k[0], "object": k[1], "before": {"measured": b[k]["measured"], "severity": b[k]["severity"]}, "after": {"measured": a[k]["measured"], "severity": a[k]["severity"]}}
               for k in a if k in b and (a[k]["measured"] != b[k]["measured"] or a[k]["severity"] != b[k]["severity"])]
    fb, fa = before["facts"], after["facts"]
    delta = {}
    for key in ("triangles", "materials", "bones", "file_size_bytes"):
        if fb.get(key) != fa.get(key):
            delta[key] = {"before": fb.get(key), "after": fa.get(key)}
    if fb.get("size_studs") != fa.get("size_studs"):
        delta["size_studs"] = {"before": fb.get("size_studs"), "after": fa.get("size_studs")}
    sb, sa = before["summary"], after["summary"]
    regress = [f"{f['rule']} ({f['object']})" for f in new if f["severity"] == "error"]
    return {"before": {"result": sb["result"], "errors": sb["errors"], "warnings": sb["warnings"], "ready_to_upload": sb["ready_to_upload"]},
            "after": {"result": sa["result"], "errors": sa["errors"], "warnings": sa["warnings"], "ready_to_upload": sa["ready_to_upload"]},
            "fixed": [{"rule": f["rule"], "object": f["object"], "severity": f["severity"]} for f in fixed],
            "new": [{"rule": f["rule"], "object": f["object"], "severity": f["severity"], "measured": f["measured"]} for f in new],
            "changed": changed, "unchanged": len(set(a) & set(b)) - len(changed), "fact_changes": delta, "new_errors": regress,
            "verdict": "regressed" if regress else ("improved" if fixed and not new else ("same" if not fixed and not new and not changed else "mixed"))}


LIMITS_NOTE = "Limits are DEFAULTS (not official Roblox limits); verify them."


def summary_line(rep: dict) -> str:
    s = rep["summary"]
    ready = "" if s["ready_to_upload"] is None else ("; ready_to_upload=YES" if s["ready_to_upload"] else "; ready_to_upload=NO")
    return (f"{'PASS' if s['result'] == 'pass' else 'FAIL'} {s['asset']} [{s['format']}, {s['profile']}, project {s['project']}, place {s['place_id']}]: "
            f"{s['errors']} error(s), {s['warnings']} warning(s), {s['infos']} info{ready}")


def compact(rep: dict, max_findings: int = 10, verbose: bool = False, include_text: bool = False) -> dict:
    """Short view: one-line summary first, ranked findings (id, rule, severity, object, measured, limit, fix), the rest on request."""
    if verbose:
        out = {"summary_line": summary_line(rep), **{k: v for k, v in rep.items() if k != "text"}}
        if include_text:
            out["text"] = rep["text"]
        return out
    s = rep["summary"]
    fs = []
    for f in rep["findings"][:max_findings]:
        row = {"id": f["id"], "rule": f["rule"], "severity": f["severity"], "object": f["object"], "measured": f["measured"], "limit": f["limit"], "fix": f["fix"]}
        if f.get("safe_fix"):
            row["safe_fix"] = True
        if f.get("overridden"):
            row["overridden"] = True
        if f["source"] == "reported":
            row["reported"] = True
        fs.append(row)
    out = {"summary_line": summary_line(rep), "result": s["result"], "ready_to_upload": s["ready_to_upload"], "project": s["project"], "place_id": s["place_id"], "profile": s["profile"],
           "errors": s["errors"], "warnings": s["warnings"], "infos": s["infos"], "findings": fs, "omitted": max(0, len(rep["findings"]) - max_findings)}
    if rep["not_checked"]:
        out["not_checked"] = [n["what"] for n in rep["not_checked"]]
    if rep["overrides_applied"]:
        out["overrides_applied"] = [f"{o['rule']}{'.' + o['limit_key'] if o.get('limit_key') else ''}" + (" (waived)" if o.get("waive") else f"={o.get('value')}") for o in rep["overrides_applied"]]
    if rep["summary"]["ready_to_upload"]:
        out["handoff"] = "roblox_upload_plan (via the hub); this server never uploads"
    elif rep.get("blocked_by"):
        out["blocked_by"] = rep["blocked_by"][:5]
    out["note"] = LIMITS_NOTE + " Use explain_finding for details."
    return out
