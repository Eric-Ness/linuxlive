"""Dependency-free image helpers.

The compiler only needs image headers to validate branding assets, but the CI
verifier also decodes the wallpaper, reads QEMU screendumps (PPM) and writes
PNG screenshots. Doing that with the standard library keeps the verifier
runnable on a bare checkout, including the Windows machine the gates run on.
"""

from __future__ import annotations

import colorsys
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# JPEG start-of-frame markers that carry the image size (excludes DHT, JPG, DAC).
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}

# PNG colour type -> channels per pixel.
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class ImageError(ValueError):
    """Raised when a file is not an image this module understands."""


@dataclass(frozen=True)
class Image:
    """An 8-bit RGB raster. ``pixels`` holds width * height * 3 bytes."""

    width: int
    height: int
    pixels: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ImageError("image dimensions must be positive")
        if len(self.pixels) != self.width * self.height * 3:
            raise ImageError("pixel buffer does not match image size")


def image_info(path: Path | str) -> tuple[str, int, int]:
    """Return ``(format, width, height)`` for a PNG or JPEG file.

    Only headers are parsed, so this is cheap even for large wallpapers.
    """
    data = Path(path).read_bytes()
    if data.startswith(PNG_SIGNATURE):
        width, height, _depth, _colour = _png_ihdr(data)
        return "png", width, height
    if data.startswith(b"\xff\xd8"):
        width, height = _jpeg_size(data)
        return "jpeg", width, height
    raise ImageError("not a PNG or JPEG file")


def _png_ihdr(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ImageError("PNG is missing its IHDR chunk")
    width, height, depth, colour = struct.unpack(">IIBB", data[16:26])
    if width == 0 or height == 0:
        raise ImageError("PNG has a zero dimension")
    return width, height, depth, colour


def _jpeg_size(data: bytes) -> tuple[int, int]:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise ImageError("corrupt JPEG marker stream")
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:  # markers without a length
            pos += 2
            continue
        if marker == 0xD9:  # end of image
            break
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        if marker in _JPEG_SOF:
            if pos + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            if width == 0 or height == 0:
                raise ImageError("JPEG has a zero dimension")
            return width, height
        pos += 2 + length
    raise ImageError("JPEG has no frame header")


def read_png(path: Path | str) -> Image:
    """Decode an 8-bit, non-interlaced PNG into RGB. Alpha is dropped."""
    data = Path(path).read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ImageError("not a PNG file")
    pos = len(PNG_SIGNATURE)
    header = None
    palette = b""
    idat: list[bytes] = []
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if len(body) != length:
            raise ImageError("truncated PNG chunk")
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if header is None:
        raise ImageError("PNG is missing its IHDR chunk")
    width, height, depth, colour, _compression, _filter, interlace = header
    if depth != 8:
        raise ImageError(f"unsupported PNG bit depth {depth}; only 8-bit images are supported")
    if interlace:
        raise ImageError("interlaced PNGs are not supported")
    channels = _PNG_CHANNELS.get(colour)
    if channels is None:
        raise ImageError(f"unsupported PNG colour type {colour}")
    if colour == 3 and not palette:
        raise ImageError("palette PNG has no PLTE chunk")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise ImageError(f"PNG image data is corrupt: {exc}") from exc
    rows = _unfilter(raw, width, height, channels)
    return Image(width, height, _to_rgb(rows, colour, palette))


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytes:
    stride = width * bpp
    if len(raw) != height * (stride + 1):
        raise ImageError("PNG image data has the wrong length")
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        kind = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += stride + 1
        if kind == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif kind != 0:
            raise ImageError(f"unknown PNG filter type {kind}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return bytes(out)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _to_rgb(rows: bytes, colour: int, palette: bytes) -> bytes:
    if colour == 2:
        return rows
    count = len(rows) // _PNG_CHANNELS[colour]
    out = bytearray(count * 3)
    if colour == 6:
        out[0::3], out[1::3], out[2::3] = rows[0::4], rows[1::4], rows[2::4]
    elif colour == 0:
        out[0::3] = out[1::3] = out[2::3] = rows
    elif colour == 4:
        grey = rows[0::2]
        out[0::3] = out[1::3] = out[2::3] = grey
    else:  # palette
        for i, index in enumerate(rows):
            entry = palette[index * 3 : index * 3 + 3]
            if len(entry) != 3:
                raise ImageError("PNG palette index out of range")
            out[i * 3 : i * 3 + 3] = entry
    return bytes(out)


def write_png(path: Path | str, image: Image, *, level: int = 6) -> None:
    """Write an RGB PNG. Every row uses filter 0, so read_png decodes it fast."""
    stride = image.width * 3
    raw = b"".join(
        b"\x00" + image.pixels[y * stride : (y + 1) * stride] for y in range(image.height)
    )
    header = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(PNG_SIGNATURE)
        for kind, body in ((b"IHDR", header), (b"IDAT", zlib.compress(raw, level)), (b"IEND", b"")):
            fh.write(struct.pack(">I", len(body)))
            fh.write(kind + body)
            fh.write(struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def read_ppm(path: Path | str) -> Image:
    """Read a binary (P6) PPM such as the ones QEMU's screendump writes."""
    data = Path(path).read_bytes()
    fields: list[bytes] = []
    pos = 0
    while len(fields) < 4:
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        if pos < len(data) and data[pos : pos + 1] == b"#":
            while pos < len(data) and data[pos : pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos : pos + 1].isspace():
            pos += 1
        if start == pos:
            raise ImageError("truncated PPM header")
        fields.append(data[start:pos])
    if fields[0] != b"P6":
        raise ImageError("not a binary PPM (P6) file")
    width, height, maxval = (int(f) for f in fields[1:])
    if maxval != 255:
        raise ImageError("only 8-bit PPM files are supported")
    pos += 1  # the single whitespace byte after maxval
    pixels = data[pos : pos + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ImageError("truncated PPM pixel data")
    return Image(width, height, pixels)


def _samples(image: Image, step: int):
    px = image.pixels
    for y in range(0, image.height, step):
        row = y * image.width * 3
        for x in range(0, image.width, step):
            i = row + x * 3
            yield px[i], px[i + 1], px[i + 2]


def dominant_hue(
    image: Image, *, min_saturation: float = 0.35, min_value: float = 0.2, step: int = 4
) -> float | None:
    """Return the most common hue in degrees, ignoring greys and near-blacks.

    Hues are binned in 10-degree buckets; the result is the circular mean of
    the fullest bucket. Returns None when no pixel is colourful enough.
    """
    counts = [0] * 36
    vec_x = [0.0] * 36
    vec_y = [0.0] * 36
    for r, g, b in _samples(image, step):
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < min_saturation or v < min_value:
            continue
        bucket = int(h * 36) % 36
        angle = h * 2 * math.pi
        counts[bucket] += 1
        vec_x[bucket] += math.cos(angle)
        vec_y[bucket] += math.sin(angle)
    best = max(range(36), key=lambda i: counts[i])
    if counts[best] == 0:
        return None
    return math.degrees(math.atan2(vec_y[best], vec_x[best])) % 360


def hue_distance(a: float, b: float) -> float:
    """Shortest distance between two hues on the colour wheel, in degrees."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def hue_fraction(
    image: Image,
    hue: float,
    *,
    tolerance: float = 18.0,
    min_saturation: float = 0.3,
    min_value: float = 0.1,
    step: int = 4,
) -> float:
    """Fraction of sampled pixels whose hue lies within ``tolerance`` of ``hue``.

    The value floor is low on purpose: desktops dim or blur the wallpaper
    behind overlays, which keeps the hue but lowers brightness.
    """
    total = matched = 0
    for r, g, b in _samples(image, step):
        total += 1
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s >= min_saturation and v >= min_value and hue_distance(h * 360, hue) <= tolerance:
            matched += 1
    return matched / total if total else 0.0


def screen_variety(image: Image, *, step: int = 4) -> tuple[float, int]:
    """How much is on screen: the share of sampled pixels in the commonest colour,
    and how many colours there are, with each channel cut to 16 levels.

    A blank or frozen screen is nearly one colour; a page or an app is not.
    """
    counts: dict[tuple[int, int, int], int] = {}
    total = 0
    for r, g, b in _samples(image, step):
        key = (r >> 4, g >> 4, b >> 4)
        counts[key] = counts.get(key, 0) + 1
        total += 1
    if not total:
        return 1.0, 0
    return max(counts.values()) / total, len(counts)
