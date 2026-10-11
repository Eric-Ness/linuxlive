from __future__ import annotations

import random
import struct
import zlib

import pytest

from linuxlive.png import (
    Image,
    ImageError,
    dominant_hue,
    hue_distance,
    hue_fraction,
    image_info,
    read_png,
    read_ppm,
    write_png,
)
from tests.conftest import EXAMPLES, PINK, solid_png, tiny_jpeg


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def _filtered_png(width, height, pixels, channels, colour_type, filters, palette=b""):
    """Encode rows with the given PNG filter types, independently of read_png."""
    stride = width * channels
    prev = bytes(stride)
    raw = bytearray()
    for y in range(height):
        line = pixels[y * stride : (y + 1) * stride]
        kind = filters[y % len(filters)]
        out = bytearray()
        for i, value in enumerate(line):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            predictor = {0: 0, 1: a, 2: b, 3: (a + b) // 2, 4: _paeth(a, b, c)}[kind]
            out.append((value - predictor) & 0xFF)
        raw += bytes([kind]) + out
        prev = line
    chunks = [(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))]
    if palette:
        chunks.append((b"PLTE", palette))
    chunks += [(b"IDAT", zlib.compress(bytes(raw))), (b"IEND", b"")]
    body = b"\x89PNG\r\n\x1a\n"
    for kind, data in chunks:
        body += struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return body


def test_write_then_read_round_trips(tmp_path):
    rng = random.Random(1)
    image = Image(7, 5, bytes(rng.randrange(256) for _ in range(7 * 5 * 3)))
    write_png(tmp_path / "x.png", image)
    assert read_png(tmp_path / "x.png") == image


@pytest.mark.parametrize("filters", [[1], [2], [3], [4], [0, 1, 2, 3, 4]])
def test_all_png_filter_types_decode(tmp_path, filters):
    rng = random.Random(2)
    width, height = 9, 6
    pixels = bytes(rng.randrange(256) for _ in range(width * height * 3))
    (tmp_path / "f.png").write_bytes(_filtered_png(width, height, pixels, 3, 2, filters))
    assert read_png(tmp_path / "f.png").pixels == pixels


def test_rgba_grey_and_palette_images_decode_to_rgb(tmp_path):
    rgba = bytes([10, 20, 30, 255, 40, 50, 60, 0])
    (tmp_path / "a.png").write_bytes(_filtered_png(2, 1, rgba, 4, 6, [0]))
    assert read_png(tmp_path / "a.png").pixels == bytes([10, 20, 30, 40, 50, 60])
    (tmp_path / "g.png").write_bytes(_filtered_png(2, 1, bytes([7, 200]), 1, 0, [1]))
    assert read_png(tmp_path / "g.png").pixels == bytes([7, 7, 7, 200, 200, 200])
    palette = bytes([255, 0, 0, 0, 0, 255])
    (tmp_path / "p.png").write_bytes(_filtered_png(2, 1, bytes([1, 0]), 1, 3, [0], palette))
    assert read_png(tmp_path / "p.png").pixels == bytes([0, 0, 255, 255, 0, 0])


def test_image_info_reads_png_and_jpeg_headers(tmp_path):
    assert image_info(solid_png(tmp_path / "a.png", 33, 21)) == ("png", 33, 21)
    assert image_info(tiny_jpeg(tmp_path / "b.jpg", 1920, 1080)) == ("jpeg", 1920, 1080)
    (tmp_path / "c.png").write_text("hello")
    with pytest.raises(ImageError):
        image_info(tmp_path / "c.png")


def test_unsupported_png_is_rejected(tmp_path):
    data = bytearray(_filtered_png(1, 1, bytes(3), 3, 2, [0]))
    data[24] = 16  # bit depth 16
    (tmp_path / "deep.png").write_bytes(bytes(data))
    with pytest.raises(ImageError, match="bit depth 16"):
        read_png(tmp_path / "deep.png")


def test_read_ppm(tmp_path):
    (tmp_path / "s.ppm").write_bytes(b"P6\n# QEMU\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6]))
    image = read_ppm(tmp_path / "s.ppm")
    assert (image.width, image.height, image.pixels) == (2, 1, bytes([1, 2, 3, 4, 5, 6]))
    (tmp_path / "bad.ppm").write_bytes(b"P3\n1 1\n255\n0 0 0\n")
    with pytest.raises(ImageError):
        read_ppm(tmp_path / "bad.ppm")


def solid(rgb, width=40, height=30):
    return Image(width, height, bytes(rgb) * (width * height))


def test_dominant_hue_of_a_solid_colour():
    assert hue_distance(dominant_hue(solid(PINK)), 336) < 3
    assert hue_distance(dominant_hue(solid((30, 90, 220))), 221) < 3


def test_dominant_hue_ignores_greys():
    assert dominant_hue(solid((128, 128, 128))) is None


def test_example_wallpaper_is_deep_pink():
    hue = dominant_hue(read_png(EXAMPLES / "assets" / "wallpaper.png"))
    assert hue_distance(hue, 335) < 6


def test_hue_fraction_positive_and_negative_controls():
    assert hue_fraction(solid(PINK), 336) == 1.0
    assert hue_fraction(solid((30, 90, 220)), 336) == 0.0  # another hue
    assert hue_fraction(solid((128, 128, 128)), 336) == 0.0  # grey has no hue
    assert hue_fraction(solid((48, 6, 23)), 336) == 1.0  # same hue, dimmed


def test_hue_fraction_measures_partial_coverage():
    top = bytes(PINK) * (40 * 15)
    bottom = bytes((20, 20, 20)) * (40 * 15)
    assert hue_fraction(Image(40, 30, top + bottom), 336, step=1) == pytest.approx(0.5)


def test_hue_distance_wraps_around():
    assert hue_distance(350, 10) == 20
    assert hue_distance(10, 350) == 20
    assert hue_distance(180, 0) == 180
