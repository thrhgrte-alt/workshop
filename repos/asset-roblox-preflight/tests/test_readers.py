"""Readers and measurements against hand-computed expectations."""
import base64
import json
import math
import struct
import zlib

import pytest

from preflight.domain import gltf, imageinfo, loader, measure, objio, summary, synth
from preflight.domain.mathx import mat_decompose, mat_from_trs, mat_mul, quat_angle_deg


def glb_of(builder, tmp_path, name="a.glb"):
    return builder.write_glb(tmp_path / name)


def test_unit_cube_measurements(tmp_path):
    b = synth.GltfBuilder()
    b.node("c", mesh=b.mesh("c", synth.box((1, 1, 1))))
    a = loader.load_export(glb_of(b, tmp_path))
    m = list(measure.measure_asset(a).values())[0]
    assert (m["triangles"], m["vertices"], m["islands"], m["boundary_edges"], m["multi_edges"]) == (12, 24, 1, 0, 0)
    assert m["signed_volume_local"] == pytest.approx(1.0) and m["closed"] and not m["inside_out"]
    assert m["dims_world"] == pytest.approx((1, 1, 1)) and m["bbox_local"][0] == pytest.approx((-0.5, 0, -0.5))
    assert m["texel_median"] == pytest.approx(math.sqrt(1 / 6), abs=1e-4)  # each face: uv area 1/6 over world area 1
    assert m["uv_coverage"] == pytest.approx(1.0, abs=1e-3) and m["uv_overlap_ratio"] == 0.0


def test_box_counts_and_volume_by_hand(tmp_path):
    b = synth.GltfBuilder()
    b.node("c", mesh=b.mesh("c", synth.box((2, 3, 4), sub=3)))
    m = list(measure.measure_asset(loader.load_export(glb_of(b, tmp_path))).values())[0]
    assert m["triangles"] == 12 * 9 and m["vertices"] == 6 * 4 * 4
    assert m["signed_volume_local"] == pytest.approx(24.0) and m["dims_world"] == pytest.approx((2, 3, 4))
    assert m["closed"] and m["non_manifold_edges"] == 0


def test_texel_density_of_a_known_quad(tmp_path):
    # a 2x2 quad whose UVs cover the whole square: uv area 1 over world area 4 -> sqrt(1/4) = 0.5
    quad = synth.triangle_mesh([((0, 0, 0), (2, 0, 0), (2, 2, 0)), ((0, 0, 0), (2, 2, 0), (0, 2, 0))],
                               [((0, 0), (1, 0), (1, 1)), ((0, 0), (1, 1), (0, 1))])
    b = synth.GltfBuilder()
    b.node("q", mesh=b.mesh("q", quad))
    m = list(measure.measure_asset(loader.load_export(glb_of(b, tmp_path))).values())[0]
    assert m["texel_median"] == pytest.approx(0.5, abs=1e-6) and m["texel_spread"] == pytest.approx(1.0)
    assert m["uv_coverage"] == pytest.approx(1.0, abs=0.02) and m["uv_overlap_ratio"] == 0.0 and m["boundary_edges"] == 4


def test_flipped_normals_and_winding(tmp_path):
    for kw, flipped, inside in [({"flip_normals": True}, 1.0, False), ({"invert_winding": True}, 1.0, True), ({"flip_normals": True, "invert_winding": True}, 0.0, True)]:
        b = synth.GltfBuilder()
        b.node("c", mesh=b.mesh("c", synth.box((1, 1, 1), **kw)))
        m = list(measure.measure_asset(loader.load_export(glb_of(b, tmp_path))).values())[0]
        assert m["flipped_ratio"] == flipped and m["inside_out"] is inside, kw


def test_node_transform_is_applied_to_world_bounds(tmp_path):
    b = synth.GltfBuilder()
    b.node("c", mesh=b.mesh("c", synth.box((1, 1, 1))), t=(10, 0, 0), s=(2, 3, 4))
    a = loader.load_export(glb_of(b, tmp_path))
    m = list(measure.measure_asset(a).values())[0]
    assert m["dims_world"] == pytest.approx((2, 3, 4)) and m["bbox_world"][0] == pytest.approx((9, 0, -2))
    assert a["objects"][0]["scale"] == pytest.approx((2, 3, 4))


def test_matrix_helpers_roundtrip():
    q = (0.0, math.sin(math.pi / 8), 0.0, math.cos(math.pi / 8))  # 45 degrees about Y
    t, r, s = mat_decompose(mat_from_trs((1, 2, 3), q, (2, 2, 2)))
    assert t == pytest.approx((1, 2, 3)) and s == pytest.approx((2, 2, 2)) and quat_angle_deg(r) == pytest.approx(45.0, abs=1e-6)
    _, _, s2 = mat_decompose(mat_from_trs((0, 0, 0), (0, 0, 0, 1), (-1, 1, 1)))
    assert s2[0] < 0
    assert mat_mul(mat_from_trs((1, 0, 0)), mat_from_trs((0, 2, 0)))[12:15] == [1.0, 2.0, 0.0]


def test_glb_container_errors(tmp_path):
    with pytest.raises(ValueError, match="magic"):
        gltf.split_glb(b"NOPE" + b"\0" * 40)
    good = synth.GltfBuilder()
    good.node("c", mesh=good.mesh("c", synth.box()))
    data = good.glb()
    with pytest.raises(ValueError, match="truncated"):
        gltf.split_glb(data[: len(data) // 2])
    with pytest.raises(ValueError, match="version"):
        gltf.split_glb(struct.pack("<III", 0x46546C67, 1, 20) + b"\0" * 8)


def test_compressed_geometry_is_refused(tmp_path):
    b = synth.GltfBuilder()
    b.node("c", mesh=b.mesh("c", synth.box()))
    b.doc["extensionsRequired"] = ["EXT_meshopt_compression"]
    with pytest.raises(ValueError, match="EXT_meshopt_compression"):
        loader.load_export(glb_of(b, tmp_path))


def _doc(buf: bytes, views, accessors, **extra):
    return {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(buf), "uri": "data:application/octet-stream;base64," + base64.b64encode(buf).decode()}],
            "bufferViews": views, "accessors": accessors, **extra}


def test_normalized_and_sparse_accessors():
    buf = struct.pack("<3f", 1, 2, 3) * 3 + struct.pack("<3H", 0, 32768, 65535) + struct.pack("<H", 1) + b"\0\0" + struct.pack("<3f", 9, 9, 9)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 6}, {"buffer": 0, "byteOffset": 42, "byteLength": 2},
             {"buffer": 0, "byteOffset": 46, "byteLength": 12}]
    acc = [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
           {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR", "normalized": True},
           {"componentType": 5126, "count": 3, "type": "VEC3", "sparse": {"count": 1, "indices": {"bufferView": 2, "componentType": 5123}, "values": {"bufferView": 3}}}]
    d = gltf._Doc(_doc(buf, views, acc), None, lambda u: None)
    assert d.accessor(0) == [(1.0, 2.0, 3.0)] * 3
    n = d.accessor(1)
    assert n[0] == 0.0 and n[2] == 1.0 and n[1] == pytest.approx(32768 / 65535)
    assert d.accessor(2) == [(0.0, 0.0, 0.0), (9.0, 9.0, 9.0), (0.0, 0.0, 0.0)]


def test_accessor_bounds_are_checked():
    buf = b"\0" * 12
    d = gltf._Doc(_doc(buf, [{"buffer": 0, "byteLength": 12}], [{"bufferView": 0, "componentType": 5126, "count": 5, "type": "VEC3"}]), None, lambda u: None)
    with pytest.raises(ValueError, match="past the end"):
        d.accessor(0)


def test_interleaved_stride():
    # two vec2 floats interleaved with a 4-byte pad each: stride 12
    raw = b"".join(struct.pack("<2f", i, i + 0.5) + b"\0\0\0\0" for i in range(2))
    d = gltf._Doc(_doc(raw, [{"buffer": 0, "byteLength": 24, "byteStride": 12}], [{"bufferView": 0, "componentType": 5126, "count": 2, "type": "VEC2"}]), None, lambda u: None)
    assert d.accessor(0) == [(0.0, 0.5), (1.0, 1.5)]


def test_gltf_with_external_files_and_traversal(tmp_path):
    b = synth.GltfBuilder()
    mat = b.material("m", base=b.image(synth.png_bytes(64, 32), "a", external="textures/a.png"))
    b.node("c", mesh=b.mesh("c", synth.box(), mat))
    p = b.write_gltf(tmp_path / "x.gltf")
    a = loader.load_export(p)
    img = a["images"][0]
    assert img["exists"] and (img["width"], img["height"]) == (64, 32) and a["format"] == "gltf"
    r = loader.make_resolver(tmp_path, None)
    assert r("../etc/passwd") is None and r("/abs/x.png") is None and r("C:/x.png") is None
    assert r("textures/a.png").exists()


def test_obj_negative_indices_and_ngon(tmp_path):
    (tmp_path / "t.obj").write_text("o thing\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0.5 1.5 0\nf -5 -4 -3 -2 -1\nf 1 2 3\n")
    a = objio.read_obj(tmp_path / "t.obj", lambda u: None)
    o = a["objects"][0]
    assert o["mesh"]["ngons"] == 1 and len(o["mesh"]["triangles"]) // 3 == 3 + 1
    with pytest.raises(ValueError, match="references element"):
        (tmp_path / "bad.obj").write_text("v 0 0 0\nf 1 2 3\n")
        objio.read_obj(tmp_path / "bad.obj", lambda u: None)


def test_png_header_and_jpeg_header():
    i = imageinfo.image_info(synth.png_bytes(300, 200, color_type=6))
    assert (i["width"], i["height"], i["channels"], i["has_alpha"]) == (300, 200, 4, True)
    assert imageinfo.image_info(synth.png_bytes(8, 8, color_type=0))["channels"] == 1
    srgb = imageinfo.image_info(synth.png_bytes(8, 8, extra_chunks=[(b"sRGB", b"\0")]))
    assert b"sRGB" in srgb["chunks"]
    jpeg = b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9 + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 123, 456, 3) + b"\0" * 10
    j = imageinfo.image_info(jpeg)
    assert (j["format"], j["width"], j["height"], j["channels"]) == ("jpeg", 456, 123, 3)
    assert imageinfo.image_info(b"not an image")["format"] == "unknown"


def _filter_rows(rows, bpp):
    """Forward PNG filtering written from the specification, independent of the decoder."""
    out, prev = bytearray(), bytes(len(rows[0]))
    for n, row in enumerate(rows):
        ft = n % 5
        out.append(ft)
        for i, x in enumerate(row):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 0:
                f = x
            elif ft == 1:
                f = x - a
            elif ft == 2:
                f = x - b
            elif ft == 3:
                f = x - ((a + b) >> 1)
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                f = x - (a if pa <= pb and pa <= pc else b if pb <= pc else c)
            out.append(f & 255)
        prev = row
    return bytes(out)


def test_png_unfilter_all_filter_types():
    import random

    rnd = random.Random(7)
    w, h, bpp = 9, 10, 3
    rows = [bytes(rnd.randrange(256) for _ in range(w * bpp)) for _ in range(h)]
    got = imageinfo._unfilter(_filter_rows(rows, bpp), w, h, bpp)
    assert [bytes(r) for r in got] == rows


def test_pixel_means_flat_normal_map_and_colour():
    n = imageinfo.pixel_means(synth.png_bytes(16, 16, synth.NORMAL_FLAT))
    assert n["mean"] == pytest.approx((128, 128, 255), abs=1)
    c = imageinfo.pixel_means(synth.png_bytes(16, 16, (200, 120, 60)))
    assert c["mean"] == pytest.approx((200, 120, 60), abs=1)
    assert imageinfo.pixel_means(b"junk") is None


def test_fbx_header_only(tmp_path):
    p = tmp_path / "a.fbx"
    p.write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7500) + b"\0" * 50)
    a = loader.load_export(p)
    assert a["fbx"] == {"flavour": "binary", "version": 7500} and a["objects"] == [] and a["geometry_measured"] is False
    (tmp_path / "b.fbx").write_bytes(b"hello")
    with pytest.raises(ValueError, match="FBX"):
        loader.load_export(tmp_path / "b.fbx")


def test_summary_validation_and_merge(tmp_path):
    assert any("format" in p for p in summary.validate_summary({"objects": []}))
    bad = {"format": summary.SUMMARY_FORMAT, "objects": [{"name": "x", "triangles": -3}, {"type": "MESH"}]}
    probs = summary.validate_summary(bad)
    assert any("non-negative" in p for p in probs) and any("name" in p for p in probs)
    doc = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "examples/assets/good_summary_prop.json").read_text())
    (tmp_path / "x.fbx").write_bytes(b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", 7400) + b"\0" * 50)
    a = loader.load_export(tmp_path / "x.fbx", summary_doc=doc)
    m = list(measure.measure_asset(a).values())[0]
    assert m["source"] == "reported" and m["triangles"] == 12 and a["objects"][0]["name"] == "prop_crate"


def test_fixtures_are_deterministic(tmp_path):
    from preflight.domain import fixtures

    a, b = tmp_path / "a", tmp_path / "b"
    fixtures.build_all(a)
    fixtures.build_all(b)
    names = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    assert names and all((a / n).read_bytes() == (b / n).read_bytes() for n in names)
