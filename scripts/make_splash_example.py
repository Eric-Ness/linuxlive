#!/usr/bin/env python3
"""Generate the example boot splash used by examples/lawndarts-*.yaml.

Lawn Darts Linux's splash is a Plymouth script (lawndarts.script, written by
hand) and the images it draws, written here: a lawn dart that arcs across a
grass-green screen below the logo, and a progress bar. The logo itself is not
written: the pipeline puts the spec's logo into every splash as watermark.png.

The images are plain RGB PNGs drawn on the splash's own background colour, so
they need no transparency.

    python scripts/make_splash_example.py [OUTPUT_DIR]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.png import Image, write_png  # noqa: E402

GRASS = (47, 107, 42)      # #2f6b2a, the splash background (lawndarts.script sets the same)
ORANGE = (224, 102, 31)    # #e0661f, the distro's accent
STEEL = (214, 220, 226)
SHADE = (36, 82, 32)


def canvas(width: int, height: int, colour=GRASS) -> list[list[tuple[int, int, int]]]:
    return [[colour] * width for _ in range(height)]


def to_image(rows) -> Image:
    return Image(len(rows[0]), len(rows), b"".join(bytes(c) for row in rows for c in row))


def dart() -> Image:
    """A lawn dart flying left to right: steel shaft, orange fins, a pointed tip."""
    w, h = 112, 32
    px = canvas(w, h)
    mid = h // 2
    for x in range(18, 92):                       # the shaft
        for y in (mid - 1, mid, mid + 1):
            px[y][x] = STEEL
    for x in range(92, 110):                      # the tip narrows to a point
        half = max(0, (110 - x) // 6)
        for y in range(mid - half, mid + half + 1):
            px[y][x] = STEEL
    for i in range(14):                           # three fins at the back
        for y in range(mid - 2 - i, mid - 1):
            px[y][4 + i] = ORANGE
        for y in range(mid + 2, mid + 3 + i):
            px[y][4 + i] = ORANGE
    for x in range(4, 22):
        px[mid][x] = ORANGE
    return to_image(px)


def bar(colour, width=300, height=6) -> Image:
    return to_image(canvas(width, height, colour))


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "assets" / "lawndarts" / "splash"
    out.mkdir(parents=True, exist_ok=True)
    write_png(out / "dart.png", dart(), level=9)
    write_png(out / "bar.png", bar(ORANGE), level=9)
    write_png(out / "bar-bg.png", bar(SHADE), level=9)
    print(f"wrote dart.png, bar.png and bar-bg.png to {out}")


if __name__ == "__main__":
    main()
