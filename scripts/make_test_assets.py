#!/usr/bin/env python3
"""Generate the example wallpaper and logo used by examples/*.yaml.

The wallpaper is a deep pink gradient with diagonal bands, all one hue, so the
CI screenshot check can find it on screen even where the desktop dims or blurs
it. The logo is a white ring around a pink "L" on a transparent background.

    python scripts/make_test_assets.py [OUTPUT_DIR]
"""

from __future__ import annotations

import colorsys
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.png import Image, write_png  # noqa: E402

HUE = 335 / 360  # deep pink


def wallpaper(width: int = 1920, height: int = 1080) -> Image:
    rows = []
    for y in range(height):
        row = bytearray(width * 3)
        for x in range(width):
            band = ((x + y) // 160) % 2  # diagonal bands
            value = 0.78 - 0.30 * (y / height) - 0.06 * band
            r, g, b = colorsys.hsv_to_rgb(HUE, 0.82, value)
            row[x * 3 : x * 3 + 3] = bytes((round(r * 255), round(g * 255), round(b * 255)))
        rows.append(bytes(row))
    return Image(width, height, b"".join(rows))


def logo_rgba(size: int = 256) -> bytes:
    """RGBA pixels: white ring, pink L, transparent elsewhere."""
    pink = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(HUE, 0.82, 0.78))
    c = (size - 1) / 2
    outer, inner = size * 0.48, size * 0.40
    out = bytearray()
    for y in range(size):
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            in_l = (0.36 * size <= x <= 0.46 * size and 0.28 * size <= y <= 0.70 * size) or (
                0.36 * size <= x <= 0.64 * size and 0.60 * size <= y <= 0.70 * size
            )
            if inner <= d <= outer:
                out += bytes((255, 255, 255, 255))
            elif d < inner and in_l:
                out += bytes((*pink, 255))
            elif d < inner:
                out += bytes((255, 255, 255, 40))
            else:
                out += bytes((0, 0, 0, 0))
    return bytes(out)


def write_rgba_png(path: Path, size: int, rgba: bytes) -> None:
    stride = size * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(size))
    chunks = (
        (b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)),
        (b"IDAT", zlib.compress(raw, 9)),
        (b"IEND", b""),
    )
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        for kind, body in chunks:
            fh.write(struct.pack(">I", len(body)) + kind + body)
            fh.write(struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "assets"
    out.mkdir(parents=True, exist_ok=True)
    write_png(out / "wallpaper.png", wallpaper(), level=9)
    write_rgba_png(out / "logo.png", 256, logo_rgba())
    print(f"wrote {out / 'wallpaper.png'} and {out / 'logo.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
