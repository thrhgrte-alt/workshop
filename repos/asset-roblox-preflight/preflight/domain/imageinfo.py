"""Image headers and a tiny pixel sampler, without Pillow.

``image_info`` reads dimensions / channels / bit depth from PNG, JPEG, BMP, TGA headers and recognises WebP, DDS, KTX2.
``pixel_means`` returns mean RGB for 8-bit PNG (pure Python for small images, Pillow when installed) - used only for the
"does this look like a normal map" heuristic, which is a heuristic and is reported as such.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_CHANNELS = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}


def image_info(data: bytes) -> dict:
    """Header facts about an image, or {'format': 'unknown'}."""
    if data[:8] == PNG_SIG and data[12:16] == b"IHDR" and len(data) >= 33:
        w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", data[16:29])
        chunks = _png_chunk_names(data)
        return {"format": "png", "width": w, "height": h, "bit_depth": depth, "color_type": ctype,
                "channels": PNG_CHANNELS.get(ctype), "has_alpha": ctype in (4, 6) or b"tRNS" in chunks, "interlaced": bool(interlace),
                "chunks": sorted(set(chunks) & {b"sRGB", b"gAMA", b"iCCP", b"cHRM", b"tRNS", b"pHYs"}), }
    if data[:3] == b"\xff\xd8\xff":
        return _jpeg_info(data)
    if data[:2] == b"BM" and len(data) >= 30:
        w, h, planes, bpp = struct.unpack("<iiHH", data[18:30])
        return {"format": "bmp", "width": abs(w), "height": abs(h), "bit_depth": bpp, "channels": 4 if bpp == 32 else 3, "has_alpha": bpp == 32}
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return {"format": "webp", "width": None, "height": None, "channels": None, "has_alpha": None}
    if data[:4] == b"DDS ":
        h, w = struct.unpack("<II", data[12:20])
        return {"format": "dds", "width": w, "height": h, "channels": None, "has_alpha": None}
    if data[:12] == b"\xabKTX 20\xbb\r\n\x1a\n":
        w, h = struct.unpack("<II", data[20:28])
        return {"format": "ktx2", "width": w, "height": h, "channels": None, "has_alpha": None}
    tga = _tga_info(data)
    if tga:
        return tga
    return {"format": "unknown", "width": None, "height": None, "channels": None, "has_alpha": None}


def _tga_info(data: bytes) -> dict | None:
    if len(data) < 18:
        return None
    id_len, cmap_type, img_type = data[0], data[1], data[2]
    if cmap_type not in (0, 1) or img_type not in (1, 2, 3, 9, 10, 11):
        return None
    w, h = struct.unpack("<HH", data[12:16])
    bpp = data[16]
    if w == 0 or h == 0 or bpp not in (8, 15, 16, 24, 32):
        return None
    return {"format": "tga", "width": w, "height": h, "bit_depth": 8, "channels": bpp // 8 or 1, "has_alpha": bpp == 32}


def _png_chunk_names(data: bytes) -> list[bytes]:
    names, pos = [], 8
    while pos + 8 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        names.append(data[pos + 4:pos + 8])
        if data[pos + 4:pos + 8] == b"IDAT":
            break  # ancillary colour chunks must precede IDAT
        pos += 12 + n
    return names


def _jpeg_info(data: bytes) -> dict:
    pos = 2
    while pos + 9 < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        seg = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            depth, h, w, comps = struct.unpack(">BHHB", data[pos + 4:pos + 10])
            return {"format": "jpeg", "width": w, "height": h, "bit_depth": depth, "channels": comps, "has_alpha": False}
        pos += 2 + seg
    return {"format": "jpeg", "width": None, "height": None, "channels": None, "has_alpha": False}


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> list[bytearray]:
    stride = width * bpp
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        elif ft != 0:
            raise ValueError(f"bad PNG filter type {ft}")
        rows.append(line)
        prev = line
    return rows


def pixel_means(data: bytes, max_pure_pixels: int = 512 * 512) -> dict | None:
    """Mean R, G, B (0-255) of an image, or None when it cannot be measured here (and why is not guessed)."""
    info = image_info(data)
    if not info.get("width"):
        return None
    try:
        from PIL import Image  # optional
        import io

        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im = im.resize((min(64, im.width), min(64, im.height)))
            px = list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())
        n = len(px)
        return {"mean": tuple(round(sum(p[i] for p in px) / n, 2) for i in range(3)), "method": "pillow"}
    except ImportError:
        pass
    except Exception:
        return None
    if info["format"] != "png" or info.get("bit_depth") != 8 or info.get("interlaced") or info["color_type"] == 3:
        return None
    w, h = info["width"], info["height"]
    if w * h > max_pure_pixels:
        return None
    idat, pos = b"", 8
    while pos + 8 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    ch = info["channels"]
    try:
        rows = _unfilter(zlib.decompress(idat), w, h, ch)
    except (zlib.error, ValueError, IndexError):
        return None
    sums, count = [0, 0, 0], 0
    step = max(1, h // 64)
    for row in rows[::step]:
        for x in range(0, w, max(1, w // 64)):
            o = x * ch
            if ch in (1, 2):
                v = row[o]
                px = (v, v, v)
            else:
                px = (row[o], row[o + 1], row[o + 2])
            for i in range(3):
                sums[i] += px[i]
            count += 1
    return {"mean": tuple(round(s / count, 2) for s in sums), "method": "pure-python"}
