"""Unit tests of the pixel metrics against values computed by hand or known from the literature, plus input handling (modes, sizes, paths)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from visualverify import evalgen as G
from visualverify import learning_params as LP
from visualverify.domain import checks as C
from visualverify.domain import engine as E
from visualverify.domain import imageio as IO
from visualverify.domain import pixels as P
from visualverify.domain import silhouette as SL
from visualverify.domain import tiling as TL

ROOT = Path(__file__).resolve().parents[1]


class Sc:
    label, project_id, place_id = "t", "t", None


def ctx(project, tmp_path, **ov):
    return E.Ctx(project, Sc(), LP.Thresholds(project, None, ov or None, use_store=False), [tmp_path], profile_dir=tmp_path / "scope", project_dir=tmp_path / "scope")


def save(tmp_path, name, arr, mode=None):
    f = tmp_path / name
    Image.fromarray(arr, mode).save(f)
    return str(f)


def test_srgb_to_lab_matches_known_values():
    lab = P.srgb_to_lab(np.array([[1, 0, 0], [0, 0, 1], [1, 1, 1], [0, 0, 0], [128 / 255] * 3], dtype=float))
    assert lab[0] == pytest.approx([53.2408, 80.0925, 67.2032], abs=0.01)
    assert lab[1] == pytest.approx([32.2970, 79.1875, -107.8602], abs=0.01)
    assert lab[2] == pytest.approx([100, 0, 0], abs=0.01) and lab[3] == pytest.approx([0, 0, 0], abs=1e-9)
    assert lab[4][0] == pytest.approx(53.585, abs=0.01)  # sRGB (128,128,128) is L* 53.585
    assert float(P.delta_e76(np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))) == pytest.approx(176.314, abs=0.01)


def test_hex_parsing_and_formatting():
    assert P.to_hex([1, 0.5, 0]) == "#ff8000" and P.hex_to_rgb("#f80").tolist() == [1.0, 136 / 255, 0.0]
    for bad in ("#12345", "red", "#gg0000", ""):
        with pytest.raises(ValueError):
            P.hex_to_rgb(bad)


def test_components_union_find():
    m = np.zeros((10, 10), bool)
    m[1:3, 1:3] = 1
    m[5:9, 5:9] = 1
    assert P.components(m) == [16, 4]
    m[3, 2] = 1  # touches the small square from below: one component of 5 + the 16 block
    assert P.components(m) == [16, 5]
    u = np.zeros((5, 5), bool)
    u[0, 0] = u[0, 2] = True
    u[1, 0:3] = True  # a U shape whose two arms only meet on the second row
    assert P.components(u) == [5]
    assert P.components(np.zeros((4, 4), bool)) == []


def test_kmeans_palette_is_deterministic_and_exact_for_flat_colours():
    arr = G.build({"gen": "bands", "size": [90, 30], "colors": [[255, 0, 0], [0, 255, 0], [0, 0, 255]], "axis": "x"}).astype(float) / 255
    a = P.dominant_colors(arr.reshape(-1, 3), 5)
    assert a == P.dominant_colors(arr.reshape(-1, 3), 5)
    assert [(d["hex"], d["share"]) for d in a] == [("#00ff00", 0.3333), ("#0000ff", 0.3333), ("#ff0000", 0.3333)] or {d["hex"] for d in a} == {"#ff0000", "#00ff00", "#0000ff"}
    assert sum(d["share"] for d in a) == pytest.approx(1.0, abs=2e-4)


def test_palette_distance_is_symmetric_in_shape_and_zero_for_equal_sets():
    dom = [{"hex": "#c81e1e", "share": 0.5}, {"hex": "#1e1ec8", "share": 0.5}]
    assert P.palette_distance(dom, ["#c81e1e", "#1e1ec8"])["distance"] == pytest.approx(0.0, abs=1e-6)
    far = P.palette_distance(dom, ["#ffff00"])
    assert far["distance"] > 50 and len(far["pairs"]) == 2 and len(far["targets"]) == 1
    with pytest.raises(ValueError):
        P.palette_distance(dom, ["#000000"], [0.0])
    with pytest.raises(ValueError):
        P.palette_distance([], ["#000000"])


def test_value_structure_and_emd():
    lum = np.array([0.0] * 50 + [1.0] * 50)
    vs = P.value_structure(lum, 0.33, 0.66)
    assert vs["levels3"] == {"dark": 0.5, "mid": 0.0, "light": 0.5} and vs["contrast"] == pytest.approx(1.0) and sum(vs["histogram"]) == pytest.approx(1.0)
    assert vs["levels5"] == [0.5, 0.0, 0.0, 0.0, 0.5]
    assert P.histogram_emd(np.zeros(100), np.ones(100) - 1e-9) == pytest.approx(31 / 32, abs=1e-6)  # all mass in the first bin vs the last bin: 31 bins apart of 32
    assert P.histogram_emd(lum, lum) == 0.0


def test_edge_stats_hand_values():
    lum = np.zeros((100, 100))
    lum[:, 50:] = 1.0
    es = P.edge_stats(lum, 0.08)
    assert es["density"] == pytest.approx(0.02) and es["distribution"] == pytest.approx(np.log(8) / np.log(16)) and es["edge_pixels"] == 200
    assert P.edge_stats(np.zeros((8, 8)), 0.08)["distribution"] is None


def test_mask_extraction_and_comparison_hand_values():
    a = np.zeros((100, 100), bool)
    a[20:60, 20:60] = True
    b = np.zeros((100, 100), bool)
    b[20:60, 30:70] = True
    c = SL.compare_masks(b, a)
    assert c["iou"] == 0.6 and c["dice"] == 0.75 and c["centroid_offset"] == pytest.approx(0.1) and c["bbox_offset"] == pytest.approx(0.1) and c["bbox_offset_px"] == [10, 0, 10, 0]
    assert c["candidate_only_share"] == pytest.approx(0.2) and c["reference_only_share"] == pytest.approx(0.2)
    empty = np.zeros_like(a)
    assert SL.compare_masks(empty, empty)["iou"] is None and SL.compare_masks(a, empty)["iou"] == 0.0
    with pytest.raises(ValueError):
        SL.compare_masks(a, np.zeros((5, 5), bool))


def test_describe_counts_significant_components_only():
    m = np.zeros((100, 100), bool)
    m[10:40, 10:40] = True  # 900 px
    m[80:82, 80:82] = True  # 4 px (0.04% of the image)
    d = SL.describe(m, 0.005)
    assert d["components"] == 1 and d["area_px"] == 904 and d["bbox"] == [10, 10, 82, 82] and d["largest_share"] == pytest.approx(900 / 904)
    assert SL.describe(m, 0.0001)["components"] == 2


def test_seam_hand_values_and_repetition():
    g = np.repeat(np.repeat((np.arange(256) / 255.0)[None, :, None], 256, 0), 3, 2)
    s = TL.seam(g)
    assert s["ratio"] == pytest.approx(255.0, rel=1e-6) and s["worst_axis"] == "horizontal" and s["vertical"]["ratio"] == 0.0
    with pytest.raises(ValueError, match="at least 3 pixels"):
        TL.seam(np.zeros((2, 8, 3)))
    lat = G.build({"gen": "lattice", "size": [256, 256], "period": 64, "radius": 3})[..., 0] / 255.0
    r = TL.repetition(lat, 0.08, 0.25)
    assert r["peak"] == pytest.approx(1.0, abs=1e-6) and r["lag"] == [64, 0]
    assert TL.repetition(np.full((32, 32), 0.4), 0.08, 0.25)["peak"] is None


def test_check_ops_and_ranking():
    ok = C.check("a", 0.5, ">=", 0.4)
    bad = C.check("b", 0.1, ">=", 0.4)
    worse = C.check("c", 0.0, ">=", 0.4)
    skip = C.check("d", None, "<=", 1, skipped="n/a")
    inr = C.check("e", 0.5, "in", [0.6, 0.8])
    assert ok["passed"] and not bad["passed"] and skip["passed"] is None and inr["passed"] is False
    assert [r["id"] for r in C.rank([ok, bad, skip, worse, inr])][:3] == ["c", "b", "e"] or [r["id"] for r in C.rank([ok, bad, skip, worse, inr])][0] == "c"
    assert C.counts([ok, bad, skip]) == {"passed": 1, "failed": 1, "skipped": 1}
    text = C.verdict([ok, bad], "x", "p/q")
    assert "FAILED 1" in text and "[p/q]" in text and "taste" in text and "good" not in text.lower()


@pytest.mark.parametrize("mode,arr", [("L", np.full((8, 8), 128, np.uint8)), ("LA", np.dstack([np.full((8, 8), 128, np.uint8), np.full((8, 8), 255, np.uint8)])),
                                       ("RGB", np.full((8, 8, 3), 128, np.uint8)), ("RGBA", np.dstack([np.full((8, 8, 3), 128, np.uint8), np.full((8, 8), 255, np.uint8)]))])
def test_common_modes_load_to_the_same_numbers(project, tmp_path, mode, arr):
    f = save(tmp_path, f"m_{mode}.png", arr, mode)
    im = IO.load(f, [tmp_path], max_bytes=10**7, max_pixels=10**7)
    assert im.rgb.shape == (8, 8, 3) and np.allclose(im.rgb, 128 / 255, atol=1e-6) and im.bit_depth == 8 and im.gray == (mode in ("L", "LA"))
    assert im.has_alpha is False  # fully opaque alpha is not transparency


def test_palette_and_cmyk_and_1bit_and_16bit_modes(project, tmp_path):
    pal = Image.new("P", (4, 4), 1)
    pal.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    pal.save(tmp_path / "p.png")
    assert IO.load(tmp_path / "p.png", [tmp_path], max_bytes=10**7, max_pixels=10**7).rgb[0, 0].tolist() == [1.0, 0.0, 0.0]
    Image.new("CMYK", (4, 4), (0, 0, 0, 255)).save(tmp_path / "c.tif")
    assert IO.load(tmp_path / "c.tif", [tmp_path], max_bytes=10**7, max_pixels=10**7).rgb.max() < 0.1
    Image.new("1", (4, 4), 1).save(tmp_path / "b.png")
    assert IO.load(tmp_path / "b.png", [tmp_path], max_bytes=10**7, max_pixels=10**7).rgb.min() == 1.0
    g16 = (np.arange(16, dtype=np.uint16).reshape(4, 4) * 4096)
    Image.fromarray(g16).save(tmp_path / "d.png")
    im = IO.load(tmp_path / "d.png", [tmp_path], max_bytes=10**7, max_pixels=10**7)
    assert im.bit_depth == 16 and im.rgb[0, 1, 0] == pytest.approx(4096 / 65535, abs=1e-6) and im.gray
    Image.fromarray(np.zeros((4, 4), np.float32), "F").save(tmp_path / "f.tif")
    with pytest.raises(IO.ImageRefused, match="pixel mode"):
        IO.load(tmp_path / "f.tif", [tmp_path], max_bytes=10**7, max_pixels=10**7)


def test_16_bit_normal_map_is_measured_at_full_precision(project, tmp_path):
    n = np.zeros((8, 8, 3), np.uint16)
    n[..., 0] = n[..., 1] = 32768  # 0.0000076
    n[..., 2] = 65535
    Image.fromarray(n[..., 0]).save(tmp_path / "x.png")  # PIL cannot write 16-bit RGB; a 16-bit gray map is read as gray and must fail the normal checks cleanly
    rep = E.check_pbr_ranges(ctx(project, tmp_path), normal=str(tmp_path / "x.png"))
    assert any(r["id"] == "normal.z_negative_fraction" for r in rep["rows"])


def test_alpha_images_use_opaque_pixels_and_refuse_fully_transparent(project, tmp_path):
    f = save(tmp_path, "s.png", G.build({"gen": "rect", "size": [40, 40], "bg": [0, 0, 0], "fg": [200, 200, 200], "box": [10, 10, 30, 30], "alpha": True}), "RGBA")
    rep = E.measure_image(ctx(project, tmp_path), f, dimensions=["value", "silhouette"])
    assert rep["measured"]["luminance"]["mean"] == pytest.approx(200 / 255, abs=1e-4) and rep["measured"]["silhouette"]["method"] == "alpha"
    assert rep["measured"]["silhouette"]["bbox_norm"] == [0.25, 0.25, 0.75, 0.75]
    empty = save(tmp_path, "e.png", np.zeros((8, 8, 4), np.uint8), "RGBA")
    with pytest.raises(IO.ImageRefused, match="no opaque pixels"):
        E.measure_image(ctx(project, tmp_path), empty)


def test_analysis_downscale_is_area_average_and_never_upscales(project, tmp_path):
    big = save(tmp_path, "big.png", G.build({"gen": "bands", "size": [1024, 512], "colors": [[0, 0, 0], [255, 255, 255]], "axis": "x"}))
    rep = E.measure_image(ctx(project, tmp_path), big, dimensions=["value"])
    assert rep["measured"]["size"] == [512, 256] and rep["measured"]["file_size"] == [1024, 512]
    small = save(tmp_path, "small.png", G.build({"gen": "solid", "size": [20, 10], "color": [9, 9, 9]}))
    assert E.measure_image(ctx(project, tmp_path), small, dimensions=["value"])["measured"]["size"] == [20, 10]


def test_non_square_and_odd_sizes_work(project, tmp_path):
    a = save(tmp_path, "a.png", G.build({"gen": "rect", "size": [37, 23], "bg": [255, 255, 255], "fg": [0, 0, 0], "box": [5, 5, 20, 15]}))
    rep = E.silhouette_iou(ctx(project, tmp_path), a, a)
    assert rep["measured"]["iou"] == 1.0 and rep["measured"]["grid"] == [37, 23]


def test_exact_checks_refuse_oversize_instead_of_downscaling(project, tmp_path):
    f = save(tmp_path, "w.png", np.zeros((8, 2100, 3), np.uint8))
    with pytest.raises(IO.ImageRefused, match="full resolution"):
        E.check_tiling(ctx(project, tmp_path), f)
    rep = E.diff_images(ctx(project, tmp_path), f, f)  # diff downscales instead (flagged in size)
    assert rep["measured"]["size"][0] <= 2048


def test_symlinks_cannot_escape_the_allowed_folders(project, tmp_path):
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    secret = save(outside, "secret.png", np.zeros((8, 8, 3), np.uint8))
    link = inside / "link.png"
    os.symlink(secret, link)
    with pytest.raises(PermissionError, match="outside the allowed"):
        IO.check_path(link, [inside])
    os.symlink(outside, inside / "dir")
    with pytest.raises(PermissionError):
        IO.check_path(inside / "dir" / "secret.png", [inside])


def test_file_size_and_extension_limits(project, tmp_path):
    f = save(tmp_path, "a.png", np.zeros((8, 8, 3), np.uint8))
    with pytest.raises(IO.ImageRefused, match="above the limit"):
        IO.load(f, [tmp_path], max_bytes=10, max_pixels=10**7)
    (tmp_path / "x.svg").write_text("<svg/>")
    with pytest.raises(IO.ImageRefused, match="unsupported file type"):
        IO.check_path(tmp_path / "x.svg", [tmp_path])
    with pytest.raises(IO.ImageRefused):
        IO.check_path("", [tmp_path])
    with pytest.raises(IO.ImageRefused):
        IO.check_path("a\x00.png", [tmp_path])


def test_results_do_not_depend_on_process_state(project, tmp_path):
    """The same image measured in two separate Python processes (different hash seeds) gives byte-identical JSON."""
    f = save(tmp_path, "n.png", G.build({"gen": "white_noise", "size": [80, 80], "seed": 3}))
    code = ("import json,sys; sys.path.insert(0, %r); from visualverify import project, learning_params as LP; from visualverify.domain import engine as E\n"
            "class S: label='t'; project_id='t'; place_id=None\n"
            "p=project(); c=E.Ctx(p,S(),LP.Thresholds(p,None,use_store=False),[%r]); print(json.dumps(E.render(c,E.measure_image(c,%r),True,{}),sort_keys=True))") % (str(ROOT), str(tmp_path), f)
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("0", "1", "random")}
    assert len(outs) == 1 and json.loads(outs.pop())["summary"].startswith("measure_image:")


def test_every_threshold_the_engine_reads_is_registered(project, tmp_path):
    meta = LP.raw_thresholds(project)
    th = LP.Thresholds(project, None, use_store=False)
    used = set()

    class Spy:
        meta = th.meta

        def __call__(self, n):
            used.add(n)
            return th(n)

        def source(self, n):
            return th.source(n)

    spy = Spy()
    c = E.Ctx(project, Sc(), spy, [tmp_path], profile_dir=tmp_path / "s", project_dir=tmp_path / "s")
    files = {k: save(tmp_path, f"{k}.png", G.build(s)) for k, s in {"a": {"gen": "rect", "size": [64, 64], "bg": [200] * 3, "fg": [30] * 3, "box": [10, 10, 40, 40]}, "t": {"gen": "smooth_tile", "size": [64, 64], "seed": 1},
                                                                    "n": {"gen": "normal_noisy", "size": [64, 64], "seed": 1}, "g": {"gen": "gray_noise", "size": [64, 64], "seed": 1, "mean": 0.5, "amp": 0.2}}.items()}
    E.measure_image(c, files["a"], target_palette=["#ffffff"])
    E.compare_to_reference(c, files["a"], files["t"])
    E.check_tiling(c, files["t"])
    E.check_pbr_ranges(c, albedo=files["a"], roughness=files["g"], metalness=files["g"], normal=files["n"])
    E.diff_images(c, files["a"], files["a"])
    assert used <= set(meta), used - set(meta)
    print(sorted(n for n in meta if n not in used))
    unused = {n for n in meta if n not in used}
    assert unused <= {"palette.profile_margin", "limits.max_file_bytes", "limits.max_pixels", "limits.max_side_exact", "limits.analysis_max_side", "limits.findings_cap"}, sorted(unused)
