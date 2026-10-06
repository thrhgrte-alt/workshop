"""MCP server end-to-end (SDK in-memory client), CLI, doctor, imaging."""

import asyncio
import json
from typing import Any

import pytest

from rbxlevel.core import imaging
from rbxlevel.core.cli import DomainHooks, run
from rbxlevel.core.commontools import common_tools
from rbxlevel.core.mcpkit import ToolSpec, build_server, call_local, serve
from mcp.shared.memory import create_connected_server_and_client_session


def _hooks():
    def extra(project):
        def echo(text: str, dry_run: bool = True) -> dict[str, Any]:
            """Echo back (a write tool that defaults to dry run)."""
            return {"text": text, "dry_run": dry_run}

        return [ToolSpec("echo_write", echo, echo.__doc__, read_only=False, destructive=False)]

    return DomainHooks(tools=extra, instructions="toy instructions")


async def _session(server, fn):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await fn(client)


def test_mcp_lists_typed_annotated_tools_and_calls_them(project):
    specs = common_tools(project, ["edge_wear"]) + _hooks().tools(project)
    server = build_server(project, specs, "toy instructions")

    async def go(client):
        tools = (await client.list_tools()).tools
        found = await client.call_tool("search_library", {"query": "chunky stone wall", "k": 2})
        write = await client.call_tool("echo_write", {"text": "hi"})
        bad = await client.call_tool("record_decision", {"run_id": "run-nope", "decision": "accept"})
        return tools, found, write, bad

    tools, found, write, bad = asyncio.run(_session(server, go))
    by = {t.name: t for t in tools}
    assert {"search_library", "get_style_brief", "record_run", "record_decision", "promote_run", "echo_write"} <= set(by)
    assert by["search_library"].annotations.readOnlyHint is True
    assert by["echo_write"].annotations.readOnlyHint is False
    assert by["search_library"].inputSchema["properties"]["query"]["type"] == "string"
    assert not found.isError and found.structuredContent["positive"][0]["id"] == "stone-wall-a"
    assert write.structuredContent == {"text": "hi", "dry_run": True}
    assert bad.isError and "unknown run_id" in bad.content[0].text


def test_mcp_feedback_round_trip(project):
    server = build_server(project, common_tools(project, ["edge_wear"]), "x")

    async def go(client):
        run_ = await client.call_tool("record_run", {"request": "stone wall", "retrieved": ["stone-wall-a"]})
        rid = run_.structuredContent["run_id"]
        dec = await client.call_tool("record_decision", {
            "run_id": rid, "decision": "revise", "reason": "too sharp",
            "corrections": [{"dimension": "edge_wear", "note": "round the bevels"}]})
        past = await client.call_tool("find_past_corrections", {"request": "stone wall again"})
        unknown_dim = await client.call_tool("record_decision", {
            "run_id": rid, "decision": "revise", "corrections": [{"dimension": "nope", "note": "x"}]})
        return dec, past, unknown_dim

    dec, past, unknown_dim = asyncio.run(_session(server, go))
    assert not dec.isError and past.structuredContent["corrections"][0]["reason"] == "too sharp"
    assert unknown_dim.isError


def test_duplicate_tool_names_rejected(project):
    spec = common_tools(project)[0]
    with pytest.raises(ValueError, match="duplicate"):
        build_server(project, [spec, spec], "x")


def test_bare_dict_return_annotation_is_rejected_with_a_clear_message(project):
    def bad() -> dict:
        return {}

    with pytest.raises(ValueError, match="dict\\[str, Any\\]"):
        build_server(project, [ToolSpec("bad", bad, "x")], "x")


def test_call_local_validates_arguments(project):
    specs = common_tools(project)
    assert call_local(specs, "search_library", {"query": "wood"})["positive"]
    with pytest.raises(ValueError, match="unknown argument"):
        call_local(specs, "search_library", {"bogus": 1})
    with pytest.raises(ValueError, match="unknown tool"):
        call_local(specs, "nope")


def test_http_transport_refuses_public_bind(project):
    with pytest.raises(ValueError, match="refusing to bind"):
        serve(project, common_tools(project), "x", transport="streamable-http", host="0.0.0.0")


def test_cli_doctor_search_and_validate(project, capsys):
    hooks = _hooks()
    assert run(project, hooks, ["validate-library"]) == 0
    capsys.readouterr()
    assert run(project, hooks, ["search", "wood planks"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["positive"][0]["id"] == "wood-planks-b"
    assert run(project, hooks, ["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["library"]["curated"] == 3 and report["library"]["candidates_awaiting_curation"] == 1
    assert report["skills"]["valid"] and report["style"]["valid"]


def test_cli_errors_are_clean(project, capsys):
    code = run(project, _hooks(), ["feedback", "decide", "run-missing", "--decision", "accept"])
    assert code == 2 and "unknown run_id" in capsys.readouterr().err


def test_cli_sync_check_exit_code(project, capsys):
    assert run(project, _hooks(), ["sync-agent-files", "--check"]) == 1
    assert run(project, _hooks(), ["sync-agent-files"]) == 0
    assert run(project, _hooks(), ["sync-agent-files", "--check"]) == 0


def test_cli_ingest_needs_apply_to_write(project, tmp_path, capsys):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.sbs").write_text("x")
    hooks = DomainHooks(tools=lambda p: [], instructions="", ingest_extensions={"material": [".sbs"]})
    assert run(project, hooks, ["ingest", str(src), "--kind", "material"]) == 0
    assert not project.library_file.exists()
    assert run(project, hooks, ["ingest", str(src), "--kind", "material", "--apply"]) == 0
    assert project.library_file.exists()


# --- imaging (skipped when numpy/Pillow are absent) ----------------------------------------
np = pytest.importorskip("numpy")
pytest.importorskip("PIL")


def _tileable(n=64):
    y, x = np.mgrid[0:n, 0:n] / n
    lum = 0.5 + 0.3 * np.sin(2 * np.pi * x) * np.cos(2 * np.pi * y)
    return np.stack([lum, lum * 0.9, lum * 0.8], -1).astype(np.float32)


def test_seam_score_detects_non_tileable():
    ok = _tileable()
    bad = np.tile(np.linspace(0, 1, 64)[None, :, None], (64, 1, 3)).astype(np.float32)  # hard wrap seam
    assert imaging.seam_score(ok) < 1.5
    assert imaging.seam_score(bad) > 5


def test_palette_and_adherence():
    arr = np.zeros((40, 40, 3), np.float32)
    arr[:, :20] = (0.9, 0.1, 0.1)
    arr[:, 20:] = (0.1, 0.1, 0.9)
    pal = imaging.palette(arr, 2)
    from rbxlevel.core.retrieval import color_distance

    assert len(pal) == 2
    for want in ("#e61a1a", "#1a1ae6"):
        assert min(color_distance(p["hex"], want) for p in pal) < 2.0
    assert imaging.palette_adherence(arr, ["#e61a1a", "#1a1ae6"], k=2) > 0.9
    assert imaging.palette_adherence(arr, ["#22cc22"], k=2) < 0.5


def test_silhouette_components_and_region_contrast():
    arr = np.ones((96, 96, 3), np.float32)
    arr[10:40, 10:40] = 0.05
    arr[60:90, 60:90] = 0.05
    stats = imaging.silhouette_stats(arr)
    assert stats["components"] == 2 and 0.1 < stats["fill"] < 0.3
    assert imaging.region_contrast(arr, (0.1, 0.1, 0.4, 0.4)) > 0.5
    single = np.ones((96, 96, 3), np.float32)
    single[20:70, 20:70] = 0.0
    assert imaging.silhouette_stats(single)["components"] == 1


def test_detail_frequency_orders_smooth_vs_noise():
    rng = np.random.default_rng(0)
    smooth = _tileable()
    noise = rng.random((64, 64, 3)).astype(np.float32)
    assert imaging.detail_frequency(noise) > imaging.detail_frequency(smooth) + 0.3
    assert imaging.edge_density(noise) > imaging.edge_density(smooth)


def test_measure_all_on_a_real_file(tmp_path):
    from PIL import Image

    img = (np.clip(_tileable(), 0, 1) * 255).astype(np.uint8)
    path = tmp_path / "t.png"
    Image.fromarray(img).save(path)
    m = imaging.measure_all(path, target_palette=["#808080"])
    assert m["size"] == [64, 64] and "palette_adherence" in m and m["seam_score"] < 1.5
