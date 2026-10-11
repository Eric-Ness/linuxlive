"""The Lawn Darts example art matches its brief, measured from the pixels.

The wallpaper is a lawn with an orange drain grate in the bottom-right. The
grate must stay inside the middle 4:3 of the image, because a 4:3 screen crops
the sides of a 16:9 wallpaper, and run off the bottom edge. The logo is a
256 px square PNG with a transparent surround. The TestOS art fails the same
measurements, which shows they can fail.
"""

from __future__ import annotations

import colorsys
import math
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from linuxlive.png import Image, _unfilter, dominant_hue, hue_fraction, image_info, read_png, write_png
from tests.conftest import EXAMPLES

ART = EXAMPLES / "assets" / "lawndarts"
SAFE_4_3 = (240, 1680)  # the x range of a 1920x1080 wallpaper that a 4:3 screen shows


@dataclass(frozen=True)
class Measurements:
    width: int
    height: int
    dominant: float | None
    coverage: float  # share of pixels within 18 degrees of the dominant hue
    orange: float  # share of pixels that are vivid orange
    orange_in_corner: float  # share of those in the bottom-right corner region
    corner_box: tuple[int, int, int, int] | None  # x0, y0, x1, y1 of the orange there


def is_orange(r: int, g: int, b: int) -> bool:
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return 12 <= h * 360 <= 40 and s >= 0.6 and v >= 0.4


@lru_cache(maxsize=None)
def measure(path: Path) -> Measurements:
    image = read_png(path)
    w, h, px = image.width, image.height, image.pixels
    dominant = dominant_hue(image)
    coverage = hue_fraction(image, dominant) if dominant is not None else 0.0
    total = orange = corner = 0
    box = None
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            total += 1
            i = (y * w + x) * 3
            if not is_orange(px[i], px[i + 1], px[i + 2]):
                continue
            orange += 1
            if x >= 0.55 * w and y >= 0.75 * h:
                corner += 1
                box = (x, y, x, y) if box is None else (min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y))
    share = corner / orange if orange else 0.0
    return Measurements(w, h, dominant, coverage, orange / total, share, box)


def is_lawn(m: Measurements) -> bool:
    return m.dominant is not None and 75 <= m.dominant <= 140 and m.coverage >= 0.6


def has_corner_grate(m: Measurements) -> bool:
    if m.orange < 0.01 or m.orange_in_corner < 0.9 or m.corner_box is None:
        return False
    x0, _y0, x1, y1 = m.corner_box
    return SAFE_4_3[0] <= x0 and x1 <= SAFE_4_3[1] and y1 >= m.height - 4


def png_alpha(path: Path) -> tuple[int, int, bytes]:
    """Width, height and the alpha channel of an 8-bit RGBA PNG."""
    data = path.read_bytes()
    pos, header, idat = 8, None, []
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
    assert header is not None
    width, height, depth, colour, _c, _f, interlace = header
    assert (depth, colour, interlace) == (8, 6, 0), "logo must be an 8-bit, non-interlaced RGBA PNG"
    rows = _unfilter(zlib.decompress(b"".join(idat)), width, height, 4)
    return width, height, rows[3::4]


def test_wallpaper_is_a_full_hd_png():
    assert image_info(ART / "wallpaper.png") == ("png", 1920, 1080)


def test_wallpaper_is_mostly_grass_green():
    m = measure(ART / "wallpaper.png")
    assert is_lawn(m), m


def test_grate_sits_bottom_right_inside_4_3_and_runs_off_the_bottom():
    m = measure(ART / "wallpaper.png")
    assert has_corner_grate(m), m


def level_share(path: Path) -> float:
    """Share of the grate's columns whose top lies in the same 8 px band.

    Paint in the turf's shade counts as orange here. Grass blades over the edge
    push some columns down, so a level grate scores well short of 1; a grate
    turned 3 degrees spreads its top over about 23 px and scores near 1/3.
    """
    image = read_png(path)
    w, h, px = image.width, image.height, image.pixels
    tops: dict[int, int] = {}
    for y in range(int(0.75 * h), h, 2):
        for x in range(int(0.55 * w), w, 2):
            if x in tops:
                continue
            i = (y * w + x) * 3
            hue, s, v = colorsys.rgb_to_hsv(px[i] / 255, px[i + 1] / 255, px[i + 2] / 255)
            if 12 <= hue * 360 <= 40 and s >= 0.5 and v >= 0.25:
                tops[x] = y
    if not tops:
        return 0.0
    values = sorted(tops.values())
    densest = max(sum(1 for v in values if start <= v < start + 8) for start in range(values[0], values[-1] + 1))
    return densest / len(values)


def lawn_with_grate(path: Path, tilt_degrees: float) -> Path:
    """A green frame with an orange grate in the bottom-right whose top edge is tilted."""
    w, h = 1920, 1080
    grass, iron = bytes((70, 140, 40)), bytes((224, 106, 34))
    slope = math.tan(math.radians(tilt_degrees))
    rows = []
    for y in range(h):
        row = bytearray(grass * w)
        for x in range(1220, 1660):
            if y >= 880 + (x - 1220) * slope:
                row[x * 3 : x * 3 + 3] = iron
        rows.append(bytes(row))
    write_png(path, Image(w, h, b"".join(rows)))
    return path


def test_grate_top_edge_runs_parallel_to_the_bottom():
    assert level_share(ART / "wallpaper.png") >= 0.6


def test_level_check_passes_a_level_grate_and_rejects_a_tilted_one(tmp_path):
    assert level_share(lawn_with_grate(tmp_path / "level.png", 0)) >= 0.6
    assert level_share(lawn_with_grate(tmp_path / "tilted.png", 4)) < 0.6


def test_logo_is_a_256_px_square_with_a_transparent_surround():
    width, height, alpha = png_alpha(ART / "logo.png")
    assert (width, height) == (256, 256)
    for x, y in ((0, 0), (255, 0), (0, 255), (255, 255)):
        assert alpha[y * width + x] == 0, (x, y)
    assert alpha[128 * width + 128] == 255


def test_measurements_reject_the_testos_art():
    testos = measure(EXAMPLES / "assets" / "wallpaper.png")
    assert not is_lawn(testos)
    assert not has_corner_grate(testos)


def test_credits_name_the_public_domain_sources():
    sources = [line for line in (ART / "CREDITS.md").read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
    assert len(sources) == 2
    assert all("CC0 1.0" in line for line in sources)
    assert any("commons.wikimedia.org" in line for line in sources)
    assert any("polyhaven.com" in line for line in sources)
