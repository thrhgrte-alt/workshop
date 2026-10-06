"""One place that turns a level spec into measurements, findings and rubric criteria."""

from __future__ import annotations

from . import build, graphs, sight, spec as S, walkcheck
from ..core.rubric import load_rubric, score_rubric
from ..core.style import check_ranges

PROFILE_ROUTE_METRICS = {"loops", "alternates_min", "independent_routes_min", "chokepoints_max"}


def evaluate(spec: dict, style: dict, rubric_path, *, baseline_lock: dict | None = None, manual: dict | None = None,
             with_geometry: bool = True) -> dict:
    norm, spec_findings = S.prepare(spec, style)
    spec_findings += build.check_ids(norm)
    out: dict = {"spec": norm, "spec_findings": spec_findings, "metrics": {}, "findings": [], "rubric": None}
    valid = not S.has_errors(spec_findings)
    if not valid:
        out["findings"] = [f for f in spec_findings if f["severity"] != "info"]
        out["rubric"] = score_rubric(load_rubric(rubric_path), auto={"valid_spec": False}, manual=manual)
        return out
    g = graphs.analyze(norm, style)
    p = graphs.pacing(norm, style)
    s = sight.analyze(norm, style)
    metrics = {**g["metrics"], **p["metrics"], **s["metrics"]}
    sep = graphs.spawn_separation(norm)
    if sep is not None:
        metrics["spawn_separation"] = sep
    findings = [f for f in spec_findings if f["severity"] != "info"] + g["findings"] + p["findings"] + s["findings"]
    parts = build.build_parts(norm, style)
    metrics["part_count"] = len(parts)
    limit = style.get("limits", {}).get("max_blockout_parts", 800)
    walk = None
    if with_geometry:
        walk = walkcheck.check(norm, parts)
        findings += walk["findings"]
    ranges = dict(style.get("ranges", {}))
    profile = style.get("profiles", {}).get(norm.get("profile", ""), {})
    ranges.update(profile.get("ranges", {}))
    range_findings = check_ranges(metrics, ranges)
    for f in range_findings:
        if f["severity"] != "info":
            findings.append(S.finding(f["severity"], "range_" + f["metric"], f["message"], f["metric"]))
    lock_findings = build.check_locks(norm, baseline_lock) if baseline_lock else []
    findings += lock_findings
    codes = {f["metric"] for f in range_findings if f["severity"] in ("warning", "error")}
    bad_metrics = codes
    has_locks = any(norm.get("locked", {}).get(k) for k in ("rooms", "connections", "spawns", "objectives", "landmarks", "bounds"))
    auto = {
        "valid_spec": True,
        "all_rooms_reachable": not g["unreachable"] and not (walk and (walk["unreachable_rooms"] or not walk["ok"])),
        "locks_respected": (not lock_findings) if (baseline_lock or not has_locks) else None,
        "route_choice": not (bad_metrics & PROFILE_ROUTE_METRICS),
        "fair_routes": "fairness_ratio" not in bad_metrics,
        "spawns_safe": not ({"spawn_exposure_count", "spawn_separation"} & bad_metrics),
        "landmarks_readable": bool(norm["landmarks"]) and "landmark_visibility" not in bad_metrics,
        "sightlines_ok": "longest_sightline" not in bad_metrics,
        "pacing_ok": not ({"rest_stretch_max", "first_encounter_distance"} & bad_metrics),
        "no_empty_dead_ends": "dead_ends" not in bad_metrics,
        "within_part_budget": len(parts) <= limit,
    }
    out.update({"metrics": metrics, "findings": findings, "routes": g["routes"], "pacing": p, "sight": s, "geometry": walk,
                "lock_findings": lock_findings, "parts": len(parts),
                "rubric": score_rubric(load_rubric(rubric_path), auto=auto, manual=manual)})
    return out


def assumptions(spec: dict, style: dict) -> list[str]:
    """Everything the result depends on that the spec did not state explicitly."""
    notes = []
    player = style.get("player", {})
    given = spec.get("player_given", {})
    for k, v in player.items():
        if k not in given:
            notes.append(f"character {k} = {v} (default from style.yaml; verify against your game)")
    b = style.get("build", {})
    notes.append(f"door height {b.get('door_height', 10)}, wall {spec['wall']}, floor thickness {b.get('floor_thickness', 2)}, "
                 f"step height {b.get('step_height', 1.0)} (blockout conventions)")
    if not spec["spawns"]:
        notes.append("no spawns defined: route, fairness and spawn-safety checks were skipped")
    if not spec["objectives"]:
        notes.append("no objectives defined: route and pacing checks were skipped")
    if not spec["landmarks"]:
        notes.append("no landmarks defined: landmark readability cannot be assessed")
    if not spec["encounters"]:
        notes.append("no encounters defined: pacing cannot be assessed")
    for q in spec.get("open_questions", []):
        notes.append(f"open question: {q}")
    return notes
