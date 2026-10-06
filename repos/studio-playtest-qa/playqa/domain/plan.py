"""The run plan: what the hub should do, in what order, how often.

The plan names HUB TOOLS and the order to call them; it never calls them. Argument names of the hub tools are NOT given (``schema_unverified``: no real capture exists here), only
their intent. A play session starts with ``roblox_studio_start_stop_play`` and ends with a stop; checks that may share a session (``session: shared`` in rules/*.yaml and the
same ``context``) do; a check with ``session: own`` gets a fresh session per repeat, because it changes state (currency, memory). A flaky check is planned N times; its failure
is called only after the N runs (``aggregate`` in evaluate.py). Nothing here runs against a live server.
"""

from __future__ import annotations

from typing import Any

from . import gen
from .checks import configured, is_flaky, repeats_for, setting
from .config import SECTION_OF

HUB_CALLS = {
    "roblox_studio_start_stop_play": "start play at the top of a session and stop it at the end; argument names are schema_unverified, read the hub's tool schema",
    "execute_luau": "run the luau from generate_check_script(dry_run=false); save the raw answer to the file named in the step",
    "get_console_output": "read the console once per session, after its checks; save the text to the file named in the step",
    "screen_capture": "only when the step says so (an assertion failed or the console shows an error); save the image path and pass it to explain_failure as screenshots",
}


def select(lib: dict, cfg: dict, check_ids: list[str] | None) -> tuple[list[dict], list[dict]]:
    """``(selected checks, skipped [{check, why}])``. Explicitly named checks that cannot run raise; the default selection skips them with the reason."""
    skipped: list[dict] = []
    if check_ids:
        unknown = [c for c in check_ids if c not in lib]
        if unknown:
            raise ValueError(f"unknown check id(s) {unknown}. Known: {sorted(lib)}")
        picked = [lib[c] for c in check_ids]
        for c in picked:
            if not configured(c, cfg):
                raise ValueError(f"check '{c['id']}' is not configured for this place: add a '{SECTION_OF[c['kind']]}' section to its playtest.yaml. Nothing was planned.")
            if c.get("requires_test_mode"):
                gen.require_data_config(cfg)
        return picked, skipped
    picked = []
    for c in lib.values():
        if not configured(c, cfg):
            skipped.append({"check": c["id"], "why": f"no '{SECTION_OF[c['kind']]}' section in playtest.yaml"})
            continue
        if c.get("requires_test_mode"):
            try:
                gen.require_data_config(cfg)
            except ValueError as exc:
                skipped.append({"check": c["id"], "why": str(exc).replace("refusing to generate a data check: ", "refused: ")[:200]})
                continue
        picked.append(c)
    return picked, skipped


def _fname(cid: str, rep: int, phase: str | None = None) -> str:
    return f"results/{cid}-{rep}" + (f"-{phase}" if phase else "") + ".json"


def build(selected: list[dict], cfg: dict, style: dict, *, repeats: int | None, minutes: float | None, hashes: dict[tuple, str]) -> dict:
    sessions: list[dict] = []
    del hashes  # kept in the signature: the caller reports them under detail
    shared_groups: dict[str, list[dict]] = {}
    for c in selected:
        if c["session"] == "shared":
            shared_groups.setdefault(c["context"], []).append(c)
    # server first, boot first inside its group
    for ctx_name in ("server", "client"):
        group = sorted(shared_groups.get(ctx_name, []), key=lambda c: (c["kind"] != "boot", 0))
        if not group:
            continue
        n = len(sessions) + 1
        steps = ["start_play"]
        for c in group:
            for r in range(1, repeats_for(c, cfg, style, repeats) + 1):
                steps.append(f"execute_luau {c['id']}#{r} -> {_fname(c['id'], r)}")
        steps += [f"get_console_output -> console/s{n}.txt", f"screen_capture only on failure -> shots/s{n}.png", "stop_play"]
        sessions.append({"session": n, "context": ctx_name, "checks": [c["id"] for c in group], "steps": steps})
    for c in [c for c in selected if c["session"] == "own"]:
        for r in range(1, repeats_for(c, cfg, style, repeats) + 1):
            n = len(sessions) + 1
            steps = ["start_play"]
            if c["kind"] == "perf":
                mins = minutes if minutes is not None else cfg.get("performance", {}).get("minutes", setting(style, "perf_default_minutes"))
                steps += [f"execute_luau {c['id']}#{r} phase=start -> {_fname(c['id'], r, 'start')}", f"wait {mins:g} min of play",
                          f"execute_luau {c['id']}#{r} phase=end -> {_fname(c['id'], r, 'end')}"]
            else:
                steps.append(f"execute_luau {c['id']}#{r} -> {_fname(c['id'], r)}")
            steps += [f"get_console_output -> console/s{n}.txt", f"screen_capture only on failure -> shots/s{n}.png", "stop_play"]
            sessions.append({"session": n, "context": c["context"], "checks": [c["id"]], "steps": steps})
    return {"sessions": sessions}


def hash_keys(selected: list[dict], cfg: dict, style: dict, repeats: int | None) -> list[tuple]:
    keys = []
    for c in selected:
        for r in range(1, repeats_for(c, cfg, style, repeats) + 1):
            if c["kind"] == "perf":
                keys += [(c["id"], r, "start"), (c["id"], r, "end")]
            else:
                keys.append((c["id"], r, None))
    return keys
