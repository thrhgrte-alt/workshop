"""The screenshot parser (schema_unverified), the synthetic samples, registry and scope rules, folder isolation and the allowed-paths override."""
import base64
import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from visualverify import evalgen as G
from visualverify import hooks as hooks_mod
from visualverify.domain import hubshot as H
from visualverify.domain import projects as PJ
from visualverify.guide_adapter import all_tools, mcpkit, scope as S

ROOT = Path(__file__).resolve().parents[1]
MAIN = {"project_id": "demo-stylized-obby", "place_id": "stage-1"}


def call(project, tool, **kw):
    return mcpkit.call_local(all_tools(project, hooks_mod.HOOKS), tool, kw)


def png_b64(w=6, h=4, color=(10, 200, 10)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.parametrize("name,size", [("screen_capture.mcp_image_block.synthetic.json", (24, 16)), ("screen_capture.json_field.synthetic.json", (24, 16)), ("screen_capture.raw.synthetic.png", (24, 16))])
def test_synthetic_samples_parse_and_say_schema_unverified(name, size):
    img, info = H.decode((ROOT / "samples" / "synthetic" / name).read_bytes(), 1 << 20, 4_000_000)
    assert img.size == size and info["input_schema"] == "schema_unverified"


def test_samples_readme_names_the_hub_call_and_where_to_save_and_hub_folder_is_empty():
    text = (ROOT / "samples" / "README.md").read_text()
    for needle in ("screen_capture", "samples/hub/", "schema_unverified", "ingest_capture", "SYNTHETIC", "not captured"):
        assert needle.lower() in text.lower(), needle
    assert [f.name for f in (ROOT / "samples" / "hub").iterdir()] == [".gitkeep"]
    assert all("synthetic" in f.name for f in (ROOT / "samples" / "synthetic").iterdir())


def test_the_readme_marks_the_parser_schema_unverified():
    readme = (ROOT / "README.md").read_text()
    assert "schema_unverified" in readme and "`ingest_capture`" in readme


def test_parser_shapes():
    b = png_b64()
    ok = lambda obj: H.decode(json.dumps(obj).encode(), 1 << 20, 10**6)[0].size  # noqa: E731
    assert ok({"content": [{"type": "image", "data": b, "mimeType": "image/png"}]}) == (6, 4)
    assert ok([{"type": "image", "data": b}]) == (6, 4)
    assert ok({"result": {"content": [{"type": "text", "text": "x"}, {"type": "image", "data": b}]}}) == (6, 4)
    assert ok({"png": "data:image/png;base64," + b}) == (6, 4)
    assert ok({"base64": b}) == (6, 4) and ok({"data": b}) == (6, 4)
    with pytest.raises(H.CaptureRefused, match="Top-level keys: \\['frames', 'ok'\\]"):
        H.decode(json.dumps({"ok": True, "frames": 2}).encode(), 1 << 20, 10**6)
    with pytest.raises(H.CaptureRefused, match="neither an image nor JSON"):
        H.decode(b"{not json", 1 << 20, 10**6)
    with pytest.raises(H.CaptureRefused, match="above the limit"):
        H.decode(json.dumps({"image": b}).encode(), 40, 10**6)
    with pytest.raises(H.CaptureRefused, match="could not be decoded"):
        H.decode(b"GIF89a-not-really", 1 << 20, 10**6)


def test_ingest_capture_round_trips_the_pixels_and_gives_a_measurable_path(project):
    src = ROOT / "samples" / "synthetic" / "screen_capture.mcp_image_block.synthetic.json"
    plan = call(project, "ingest_capture", path=str(src), name="shot", **MAIN)
    assert plan["dry_run"] is True and plan["input_schema"] == "schema_unverified" and not (project.workspace).exists()
    done = call(project, "ingest_capture", path=str(src), name="shot", dry_run=False, **MAIN)
    saved = np.asarray(Image.open(done["image_path"]).convert("RGB"))
    want = np.asarray(Image.open(ROOT / "samples" / "synthetic" / "screen_capture.raw.synthetic.png").convert("RGB"))
    assert np.array_equal(saved, want)
    res = call(project, "measure_image", path=done["image_path"], **MAIN)
    assert res["measured"]["file_size"] == [24, 16]
    with pytest.raises(ValueError, match="profile name"):
        call(project, "ingest_capture", path=str(src), name="../x", **MAIN)
    with pytest.raises(ValueError, match="saved capture file"):
        call(project, "ingest_capture", path=str(ROOT / "samples" / "nope.json"), name="x", **MAIN)
    with pytest.raises(PermissionError, match="outside the allowed"):
        call(project, "ingest_capture", path="/etc/hostname", name="x", **MAIN)


def test_registry_rules(tmp_path, monkeypatch, project):
    reg = tmp_path / "p.yaml"
    reg.write_text("version: 1\nprojects:\n  - {project_id: a, places: [{place_id: main, roblox_place_id: 99, studio_name: A}]}\n  - {project_id: a}\n")
    monkeypatch.setenv("VISUALVERIFY_PROJECTS", str(reg))
    with pytest.raises(S.ScopeError, match="duplicate project_id"):
        PJ.load_registry(project)
    reg.write_text("version: 1\nprojects:\n  - {project_id: a, places: [{place_id: main, roblox_place_id: 99, studio_name: A}]}\n")
    assert PJ.resolve(project, "a", "99").place_id == "main"  # a Roblox place id resolves to the registry id
    assert PJ.resolve(project, "a").place_id is None
    monkeypatch.setenv("VISUALVERIFY_PROJECTS", str(tmp_path / "missing.yaml"))
    with pytest.raises(S.ScopeError, match="is missing"):
        PJ.resolve(project, "a")
    for bad in ("", None, "../a", "a b"):
        monkeypatch.setenv("VISUALVERIFY_PROJECTS", str(reg))
        with pytest.raises(S.ScopeError):
            PJ.resolve(project, bad)


def test_one_projects_image_folder_is_not_readable_by_another(project, tmp_path, monkeypatch):
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    reg = tmp_path / "projects.yaml"
    reg.write_text(f"version: 1\nprojects:\n  - {{project_id: pa, image_roots: ['{tmp_path}/A'], places: [{{place_id: m}}]}}\n  - {{project_id: pb, image_roots: ['{tmp_path}/B'], places: [{{place_id: m}}]}}\n")
    monkeypatch.setenv("VISUALVERIFY_PROJECTS", str(reg))
    a = str(G.write({"gen": "solid", "size": [8, 8], "color": [1, 2, 3]}, tmp_path / "A" / "a.png"))
    b = str(G.write({"gen": "solid", "size": [8, 8], "color": [1, 2, 3]}, tmp_path / "B" / "b.png"))
    assert call(project, "measure_image", path=a, project_id="pa")["project_id"] == "pa"
    with pytest.raises(PermissionError):
        call(project, "measure_image", path=b, project_id="pa")
    with pytest.raises(PermissionError):
        call(project, "compare_to_reference", candidate=a, reference=b, project_id="pa")
    with pytest.raises(PermissionError):
        call(project, "diff_images", before=a, after=b, project_id="pb")


def test_allowed_paths_widens_reading_but_never_where_files_are_written(project, tmp_path, monkeypatch):
    art = tmp_path / "art"
    art.mkdir()
    img = str(G.write({"gen": "vstep", "size": [32, 32], "left": [0, 0, 0], "right": [255, 255, 255], "x": 16}, art / "x.png"))
    with pytest.raises(PermissionError):
        call(project, "measure_image", path=img, **MAIN)
    monkeypatch.setenv("VISUALVERIFY_ALLOWED_PATHS", str(art))
    assert call(project, "measure_image", path=img, **MAIN)["images"][0]["name"] == "x.png"
    res = call(project, "render_diff_image", before=img, after=img, dry_run=False, **MAIN)
    assert project.workspace in Path(res["path"]).parents and art not in Path(res["path"]).parents
    assert [f.name for f in art.iterdir()] == ["x.png"]


def test_profiles_and_feedback_are_per_project(project, images):
    ref = images("ref", {"gen": "bands", "size": [63, 63], "colors": [[240, 240, 220], [200, 30, 30], [30, 150, 30]], "axis": "x"})
    call(project, "save_target_profile", name="look", reference=ref, dry_run=False, **MAIN)
    assert call(project, "get_target_profile", **MAIN)["profiles"] == ["look"]
    assert call(project, "get_target_profile", project_id="demo-stylized-obby")["profiles"] == []  # saved for the place, not the project level
    assert call(project, "get_target_profile", project_id="demo-pixel-brawler", place_id="arena")["profiles"] == []
    with pytest.raises(ValueError, match="no target profile 'look'"):
        call(project, "measure_image", path=ref, profile="look", project_id="demo-pixel-brawler", place_id="arena")
    call(project, "save_target_profile", name="shared", reference=ref, dry_run=False, project_id="demo-stylized-obby")
    assert "shared" in call(project, "get_target_profile", **MAIN)["profiles"]  # a project-level profile is visible from its places


def test_profile_values_are_validated(project, images):
    ref = images("ref", {"gen": "solid", "size": [8, 8], "color": [9, 9, 9]})
    for kw, msg in (({"saturation_range": [0.9, 0.2]}, "min <= max"), ({"edge_density_range": [0, 2]}, "min <= max"), ({"palette": ["#12"]}, "hex colour"), ({"contrast_min": 3.0}, "contrast_min")):
        with pytest.raises(ValueError, match=msg):
            call(project, "save_target_profile", name="p", **MAIN, **kw)
    res = call(project, "save_target_profile", name="p", palette=["#ff0000", "#00ff00"], saturation_range=[0.5, 1.0], **MAIN)
    assert res["profile"]["palette"] == [{"hex": "#ff0000", "share": 0.5}, {"hex": "#00ff00", "share": 0.5}] and res["profile"]["ranges"] == {"saturation_mean": [0.5, 1.0]}
    assert "pixels" not in json.dumps(res["profile"]) and ref not in json.dumps(res)


def test_profile_with_silhouette_descriptors_is_used(project, images):
    ref = images("ref", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [20, 20, 60, 60]})
    moved = images("moved", {"gen": "rect", "size": [100, 100], "bg": [200] * 3, "fg": [30] * 3, "box": [40, 20, 80, 60]})
    call(project, "save_target_profile", name="hero", reference=ref, include_silhouette=True, dry_run=False, **MAIN)
    same = call(project, "measure_image", path=ref, profile="hero", dimensions=["silhouette"], **MAIN)
    assert same["findings"] == [] and any("silhouette.profile_centroid_offset 0.0" in p for p in same["passed"])
    off = call(project, "compare_to_reference", candidate=moved, profile="hero", dimensions=["silhouette"], **MAIN)
    assert {f["check"] for f in off["findings"]} == {"silhouette.profile_centroid_offset", "silhouette.profile_bbox_offset"}
    assert off["summary"].startswith("compare_to_reference (profile):")
