#!/usr/bin/env python3
"""Generate the Lawn Darts Linux wallpaper and logo used by examples/lawndarts-*.yaml.

The wallpaper is a photograph of a lawn seen from above, with an orange
cast-iron drain grate set into it. The grate's top edge runs parallel to the
bottom of the screen and the grate carries on past it, so only its top shows.
It sits inside the middle 4:3 of the image, because a 4:3 screen crops the
sides of a 16:9 wallpaper, and high enough to clear a bottom panel.

The logo is a lawn dart stuck in an orange target ring on a round patch of
grass, drawn as vector art.

Both are composed as SVG and rasterised by a headless Chrome or Chromium (set
CHROME to its path if it is not found). The photographs are public domain
(CC0) and are downloaded on first use, checked against pinned SHA-256 sums and
cached in build/asset-sources/. CREDITS.md next to the output names them.

    python scripts/make_lawndarts_assets.py [OUTPUT_DIR]
"""

from __future__ import annotations

import colorsys
import hashlib
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.png import image_info, read_png, write_png  # noqa: E402

WIDTH, HEIGHT = 1920, 1080
LOGO = 256
SEED = 1987  # the year US stores stopped selling lawn darts
CACHE = ROOT / "build" / "asset-sources"


@dataclass(frozen=True)
class Source:
    file: str
    url: str
    sha256: str
    size: tuple[int, int]
    credit: str


LAWN = Source(
    "lawn.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/5/5f/Green_grass_bird%27s_eye_view_texture.jpg",
    "a5ac998b8ddad485c3b8447a79d5c2a4b4f6d5ffdfc6bf8a20fd62da5a5c3a8e",
    (4239, 3456),
    "Green grass bird's eye view texture, by Kurt Kaiser, CC0 1.0, "
    "https://commons.wikimedia.org/wiki/File:Green_grass_bird%27s_eye_view_texture.jpg",
)
PAINT = Source(
    "paint.jpg",
    "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/rusty_metal_02/rusty_metal_02_diff_2k.jpg",
    "4c887dd400ed8a5964377c65e20654262307d6888443eae85feb80cc6e72287a",
    (2048, 2048),
    "Rusty Metal 02, by Rob Tuytel, CC0 1.0, https://polyhaven.com/a/rusty_metal_02",
)

# The part of the lawn photo that fills the screen: its left edge and top, in
# photo pixels. The right of the photo is clean mown grass; fallen oak leaves
# stay on the left. At this scale one screen pixel is about 1.2 mm of lawn.
LAWN_LEFT, LAWN_TOP = 420, 1308
LAWN_SCALE = WIDTH / (LAWN.size[0] - LAWN_LEFT)
# The grate, in screen pixels: about 52 cm wide, top edge 200 px above the
# bottom of the screen, right edge inside x=1680 where a 4:3 crop ends.
GRATE_LEFT, GRATE_TOP, GRATE_WIDTH = 1220, 880, 440
GRATE_BOTTOM = HEIGHT + 80  # it carries on past the screen
PAINT_TILE = 830  # screen pixels per metre of the 1 m paint scan


def hsv(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, min(max(s, 0), 1), min(max(v, 0), 1))
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def fetch(source: Source) -> Path:
    """The source file from the cache, downloading it first if needed. Refuses a changed file."""
    path = CACHE / source.file
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.sha256:
        CACHE.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(source.url, headers={"User-Agent": "linuxlive-assets/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        digest = hashlib.sha256(data).hexdigest()
        if digest != source.sha256:
            raise SystemExit(f"{source.url} has SHA-256 {digest}, expected {source.sha256}")
        path.write_bytes(data)
    return path


def blade(rng: random.Random, x: float, y: float, angle: float, length: float, width: float, colour: str) -> str:
    """One tapered, slightly curved grass blade as a filled path."""
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy, dx  # normal
    bend = rng.uniform(-0.35, 0.35) * length
    tip = (x + dx * length + nx * bend * 0.6, y + dy * length + ny * bend * 0.6)
    mid = (x + dx * length * 0.5 + nx * bend * 0.5, y + dy * length * 0.5 + ny * bend * 0.5)
    w = width / 2
    left = (x + nx * w, y + ny * w)
    right = (x - nx * w, y - ny * w)
    return (
        f'<path d="M{left[0]:.1f},{left[1]:.1f}Q{mid[0] + nx * w * 0.6:.1f},{mid[1] + ny * w * 0.6:.1f} '
        f'{tip[0]:.1f},{tip[1]:.1f}Q{mid[0] - nx * w * 0.6:.1f},{mid[1] - ny * w * 0.6:.1f} '
        f'{right[0]:.1f},{right[1]:.1f}Z" fill="{colour}"/>'
    )


def ribbon(rng: random.Random, x: float, y: float, angle: float, length: float, width: float, colour: str) -> str:
    """A grass blade seen from above: parallel sides along a gentle curve, then a point."""
    turn = rng.gauss(0, 0.35)
    # Walk the centre line in four steps, turning a little each time.
    cx, cy, line = x, y, []
    for i in range(5):
        a = angle + turn * i / 4
        line.append((cx, cy, a, 0.55 if i == 4 else 1.0))
        cx += math.cos(a) * length / 4
        cy += math.sin(a) * length / 4
    tip = (cx, cy)
    half = width / 2
    left = [(px - math.sin(a) * half * k, py + math.cos(a) * half * k) for px, py, a, k in line]
    right = [(px + math.sin(a) * half * k, py - math.cos(a) * half * k) for px, py, a, k in line]
    d = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in left)
    d += f" L{tip[0]:.1f},{tip[1]:.1f} L" + " L".join(f"{px:.1f},{py:.1f}" for px, py in reversed(right)) + "Z"
    return f'<path d="{d}" fill="{colour}"/>'


WALLPAPER_DEFS = """
<filter id="lawn-grade" color-interpolation-filters="sRGB">
  <!-- A touch more colour than the overcast photo has. -->
  <feColorMatrix type="saturate" values="1.12"/>
  <feComponentTransfer>
    <feFuncR type="linear" slope="1.04" intercept="-0.01"/>
    <feFuncG type="linear" slope="1.06" intercept="-0.01"/>
    <feFuncB type="linear" slope="1.0" intercept="-0.01"/>
  </feComponentTransfer>
</filter>
<filter id="orange-paint" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
  <!-- The scan is white paint with rust. Soften the rust, then paint it orange:
       white becomes the paint colour and rust a darker, redder brown. -->
  <feColorMatrix type="saturate" values="0.55" result="desat"/>
  <feComponentTransfer in="desat" result="lift">
    <feFuncR type="linear" slope="0.72" intercept="0.28"/>
    <feFuncG type="linear" slope="0.72" intercept="0.28"/>
    <feFuncB type="linear" slope="0.72" intercept="0.28"/>
  </feComponentTransfer>
  <feFlood flood-color="#e06a22" result="paint"/>
  <feBlend in="lift" in2="paint" mode="multiply" result="painted"/>
  <feComposite in="painted" in2="SourceAlpha" operator="in"/>
</filter>
<filter id="cast-light" x="-1%" y="-1%" width="102%" height="102%" color-interpolation-filters="sRGB">
  <!-- Rounded cast edges, lit softly from the top left like the overcast photo. -->
  <feGaussianBlur in="SourceAlpha" stdDeviation="3.4" result="height"/>
  <feDiffuseLighting in="height" surfaceScale="4" diffuseConstant="1.3" lighting-color="#ffffff" result="diffuse">
    <feDistantLight azimuth="235" elevation="50"/>
  </feDiffuseLighting>
  <feSpecularLighting in="height" surfaceScale="4" specularConstant="0.4" specularExponent="18" lighting-color="#fff1df" result="spec">
    <feDistantLight azimuth="235" elevation="50"/>
  </feSpecularLighting>
  <feComposite in="spec" in2="SourceAlpha" operator="in" result="spec-in"/>
  <feBlend in="SourceGraphic" in2="diffuse" mode="multiply" result="shaded"/>
  <feComposite in="shaded" in2="SourceAlpha" operator="in" result="shaded-in"/>
  <feComposite in="shaded-in" in2="spec-in" operator="arithmetic" k2="1" k3="0.5"/>
</filter>
<filter id="letter-light" x="-5%" y="-20%" width="110%" height="140%" color-interpolation-filters="sRGB">
  <!-- Raised letters: their faces catch more light than the band, worn smooth. -->
  <feGaussianBlur in="SourceAlpha" stdDeviation="1.2" result="height"/>
  <feDiffuseLighting in="height" surfaceScale="3" diffuseConstant="1.3" result="diffuse">
    <feDistantLight azimuth="235" elevation="50"/>
  </feDiffuseLighting>
  <feSpecularLighting in="height" surfaceScale="3" specularConstant="0.9" specularExponent="12" lighting-color="#ffe2c4" result="spec">
    <feDistantLight azimuth="235" elevation="38"/>
  </feSpecularLighting>
  <feComposite in="spec" in2="SourceAlpha" operator="in" result="spec-in"/>
  <feBlend in="SourceGraphic" in2="diffuse" mode="multiply" result="shaded"/>
  <feComposite in="shaded" in2="SourceAlpha" operator="in" result="shaded-in"/>
  <feComposite in="shaded-in" in2="spec-in" operator="arithmetic" k2="1" k3="0.6"/>
</filter>
<filter id="wear" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
  <!-- Paint worn through to dark iron where edges take knocks. -->
  <feGaussianBlur in="SourceAlpha" stdDeviation="2.2" result="soft"/>
  <feComposite in="SourceAlpha" in2="soft" operator="arithmetic" k2="2.6" k3="-2.6" result="edge"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.09" numOctaves="3" seed="5" result="noise"/>
  <feColorMatrix in="noise" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  1.4 0 0 0 -0.62" result="spots"/>
  <feComposite in="spots" in2="edge" operator="in" result="worn"/>
  <feFlood flood-color="#4a4540"/>
  <feComposite in2="worn" operator="in"/>
</filter>
<filter id="edge-dirt" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
  <!-- Dirt collects along every edge of the iron, unevenly. -->
  <feGaussianBlur in="SourceAlpha" stdDeviation="5" result="soft"/>
  <feComposite in="SourceAlpha" in2="soft" operator="arithmetic" k2="1.6" k3="-1.6" result="edge"/>
  <feTurbulence type="fractalNoise" baseFrequency="0.05" numOctaves="3" seed="13" result="noise"/>
  <feColorMatrix in="noise" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  1.2 0 0 0 -0.25" result="patchy"/>
  <feComposite in="edge" in2="patchy" operator="arithmetic" k1="1.3" result="dirt"/>
  <feFlood flood-color="#2b1d10"/>
  <feComposite in2="dirt" operator="in"/>
</filter>
<filter id="leaf-key" filterUnits="userSpaceOnUse" x="1194" y="-338" width="74" height="62" color-interpolation-filters="sRGB">
  <!-- Lifts a fallen oak leaf out of the photo: leaves are redder than grass.
       Closing the shape fills gaps where blades of grass lay across it. -->
  <feColorMatrix in="SourceGraphic" type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  9 -9 0 0 -0.12" result="leaf"/>
  <feMorphology in="leaf" operator="dilate" radius="4" result="grown"/>
  <feMorphology in="grown" operator="erode" radius="4" result="closed"/>
  <feFlood flood-color="#9a6b3f"/>
  <feComposite in2="closed" operator="in" result="base"/>
  <feMerge result="whole"><feMergeNode in="base"/><feMergeNode in="leaf"/></feMerge>
  <!-- A dry leaf lying on wet iron reads a little darker and warmer than on the lawn. -->
  <feColorMatrix in="whole" type="saturate" values="1.25" result="warm"/>
  <feComponentTransfer in="warm">
    <feFuncR type="linear" slope="0.86"/><feFuncG type="linear" slope="0.8"/><feFuncB type="linear" slope="0.74"/>
  </feComponentTransfer>
</filter>
<filter id="leaf-shadow" filterUnits="userSpaceOnUse" x="1184" y="-348" width="94" height="82" color-interpolation-filters="sRGB">
  <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  9 -9 0 0 -0.12" result="leaf"/>
  <feMorphology in="leaf" operator="dilate" radius="4" result="grown"/>
  <feMorphology in="grown" operator="erode" radius="4" result="closed"/>
  <feGaussianBlur in="closed" stdDeviation="2.5"/>
</filter>
<filter id="grime" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
  <feTurbulence type="fractalNoise" baseFrequency="0.02 0.035" numOctaves="4" seed="9" result="noise"/>
  <feColorMatrix in="noise" type="matrix" values="0 0 0 0 0.16  0 0 0 0 0.10  0 0 0 0 0.05  1.6 0 0 0 -0.7"/>
  <feComposite in2="SourceAlpha" operator="in"/>
</filter>
<filter id="blur2" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="2"/></filter>
<filter id="blur5" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="5"/></filter>
<filter id="blur9" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="9"/></filter>
<filter id="blur1" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="0.9"/></filter>
<linearGradient id="void" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#050403"/><stop offset="0.35" stop-color="#0d0a07"/><stop offset="1" stop-color="#17120c"/>
</linearGradient>
"""


def grate_parts() -> tuple[str, str]:
    """The drain: (white-on-black mask of the iron, the cast lettering).

    A frame is set into the turf and a grate drops into it with a small gap.
    The grate has a solid top band, then bars with slots between them that
    run on past the bottom of the screen.
    """
    x0, y0, w, bottom = GRATE_LEFT, GRATE_TOP, GRATE_WIDTH, GRATE_BOTTOM
    frame, gap = 14, 2
    gx0, gx1, gy0 = x0 + frame + gap, x0 + w - frame - gap, y0 + frame + gap
    band_top = 34
    slot_w, bar_w, slots = 25, 22, 8
    span = slots * slot_w + (slots - 1) * bar_w
    sx0 = (gx0 + gx1) / 2 - span / 2
    slot_top = gy0 + band_top
    mask = [
        f'<rect x="{x0}" y="{y0}" width="{w}" height="{bottom - y0}" rx="5" fill="#fff"/>',
        f'<rect x="{x0 + frame}" y="{y0 + frame}" width="{w - 2 * frame}" height="{bottom - y0}" rx="3" fill="#000"/>',
        f'<rect x="{gx0}" y="{gy0}" width="{gx1 - gx0}" height="{bottom - gy0}" rx="3" fill="#fff"/>',
    ]
    for i in range(slots):
        sx = sx0 + i * (slot_w + bar_w)
        mask.append(f'<rect x="{sx:.1f}" y="{slot_top}" width="{slot_w}" height="{bottom - slot_top}" rx="9" fill="#000"/>')
    letters = (
        f'<text x="{(gx0 + gx1) / 2}" y="{gy0 + band_top / 2 + 7}" text-anchor="middle" '
        'font-family="Arial Black, Arial, sans-serif" font-weight="900" font-size="19" letter-spacing="6">'
        "LAWN DARTS LINUX</text>"
    )
    return "".join(mask), letters


def slots() -> list[tuple[float, float, float]]:
    """(left, top, width) of each slot in the grate."""
    gx0, gx1 = GRATE_LEFT + 16, GRATE_LEFT + GRATE_WIDTH - 16
    slot_w, bar_w, count = 25, 22, 8
    span = count * slot_w + (count - 1) * bar_w
    sx0 = (gx0 + gx1) / 2 - span / 2
    return [(sx0 + i * (slot_w + bar_w), GRATE_TOP + 16 + 34, slot_w) for i in range(count)]


def grass_colour(rng: random.Random, lift: float = 0.0) -> str:
    return hsv(rng.uniform(92, 112), rng.uniform(0.55, 0.75), rng.uniform(0.3, 0.62) + lift)


def tufts(rng: random.Random) -> str:
    """Grass growing in over the iron from the turf around it, each blade with its shadow.

    Roots come in clumps with bare stretches between, as along a real turf edge,
    and most blades lean over rather than pointing straight in.
    """
    x0, y0, w = GRATE_LEFT, GRATE_TOP, GRATE_WIDTH
    edges = [  # (clumps, root along the edge, inward direction)
        (26, lambda: (x0 - 8 + rng.random() * (w + 16), y0 + rng.uniform(-7, 0)), math.pi / 2),
        (9, lambda: (x0 + rng.uniform(-7, 0), y0 + rng.random() * (HEIGHT - y0)), 0.0),
        (9, lambda: (x0 + w + rng.uniform(0, 7), y0 + rng.random() * (HEIGHT - y0)), math.pi),
    ]
    shadows, blades = [], []
    for clumps, root, inward in edges:
        for _ in range(clumps):
            x, y = root()
            lean = rng.choice((-1, 1)) * rng.uniform(0.2, 0.9)
            for _ in range(rng.choice((1, 2, 3, 4, 6, 9))):
                angle = inward + lean + rng.gauss(0, 0.35)
                length, width = rng.uniform(9, 34), rng.uniform(2.2, 3.6)
                bx, by = x + rng.uniform(-4, 4), y + rng.uniform(-2, 2)
                state = rng.getstate()
                shadows.append(ribbon(rng, bx + 2, by + 3, angle, length, width, "#000"))
                rng.setstate(state)  # the same curve for blade and shadow
                blades.append(ribbon(rng, bx, by, angle, length, width, grass_colour(rng)))
    return f'<g opacity="0.42" filter="url(#blur1)">{"".join(shadows)}</g><g>{"".join(blades)}</g>'


def clippings(rng: random.Random) -> str:
    """Dry grass clippings lying on the iron, not over the slots."""
    x0, y0, w = GRATE_LEFT, GRATE_TOP, GRATE_WIDTH
    out = []
    while len(out) < 70:
        x, y = rng.uniform(x0 + 8, x0 + w - 8), rng.uniform(y0 + 8, HEIGHT - 4)
        if any(sx - 2 <= x <= sx + sw + 2 and y >= top - 2 for sx, top, sw in slots()):
            continue
        angle, length, width = rng.uniform(0, math.tau), rng.uniform(4, 11), rng.uniform(1.1, 2.0)
        colour = hsv(rng.uniform(55, 95), rng.uniform(0.35, 0.6), rng.uniform(0.45, 0.78))
        state = rng.getstate()
        out.append(blade(rng, x + 1, y + 1.4, angle, length, width, "#1a0e05"))
        rng.setstate(state)
        out.append(blade(rng, x, y, angle, length, width, colour))
    return "".join(out)


def wallpaper_svg(lawn: str, paint: str) -> str:
    rng = random.Random(SEED)
    s = LAWN_SCALE
    photo = (
        f'<image href="{lawn}" x="{-LAWN_LEFT * s:.2f}" y="{-LAWN_TOP * s:.2f}" '
        f'width="{LAWN.size[0] * s:.2f}" height="{LAWN.size[1] * s:.2f}" preserveAspectRatio="none"/>'
    )
    mask, letters = grate_parts()
    x0, y0, w, bottom = GRATE_LEFT, GRATE_TOP, GRATE_WIDTH, GRATE_BOTTOM
    iron = f'x="{x0}" y="{y0}" width="{w}" height="{bottom - y0}"'
    # An oak leaf from above the visible part of the photo, so it is not seen twice.
    leaf = f'<g clip-path="url(#leaf-box)">{photo}</g>'
    leaf_at = "translate(1350 990) rotate(32) translate(-1231 307)"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
<defs>{WALLPAPER_DEFS}
<pattern id="paint" patternUnits="userSpaceOnUse" width="{PAINT_TILE}" height="{PAINT_TILE}"
  patternTransform="translate({x0 - 140} {y0 - 310}) rotate(90)">
  <image href="{paint}" width="{PAINT_TILE}" height="{PAINT_TILE}"/>
</pattern>
<mask id="iron" maskUnits="userSpaceOnUse" x="0" y="0" width="{WIDTH}" height="{bottom}">{mask}</mask>
<clipPath id="leaf-box"><rect x="1194" y="-338" width="74" height="62"/></clipPath>
</defs>
<g filter="url(#lawn-grade)">{photo}</g>
<!-- Soil and shadow where the turf is cut for the frame. -->
<rect x="{x0 - 6}" y="{y0 - 6}" width="{w + 12}" height="{bottom - y0}" rx="9" fill="#120c05" opacity="0.8" filter="url(#blur5)"/>
<!-- Down the drain. -->
<rect {iron} fill="url(#void)"/>
<g filter="url(#cast-light)">
  <g mask="url(#iron)">
    <rect {iron} fill="url(#paint)" filter="url(#orange-paint)"/>
    <rect {iron} fill="#000" filter="url(#grime)" opacity="0.55"/>
  </g>
</g>
<g filter="url(#wear)" opacity="0.75"><g mask="url(#iron)"><rect {iron} fill="#fff"/></g></g>
<g filter="url(#edge-dirt)" opacity="0.7"><g mask="url(#iron)"><rect {iron} fill="#fff"/></g></g>
<g mask="url(#iron)">
  <!-- The turf stands a little above the iron and shades its edges. -->
  <rect x="{x0 - 4}" y="{y0 - 4}" width="{w + 8}" height="{bottom - y0 + 8}" fill="none" stroke="#000" stroke-width="16"
    opacity="0.4" filter="url(#blur9)"/>
</g>
<g transform="translate(1.5 2)" fill="#2a1003" opacity="0.55" filter="url(#blur1)">{letters}</g>
<g filter="url(#letter-light)"><g fill="url(#paint)" filter="url(#orange-paint)">{letters}</g></g>
{clippings(rng)}
<g transform="translate(3 5) {leaf_at}" opacity="0.55"><g filter="url(#leaf-shadow)">{leaf}</g></g>
<g transform="{leaf_at}"><g filter="url(#leaf-key)">{leaf}</g></g>
{tufts(rng)}
</svg>"""


def dart(outline: str, solid: str | None = None) -> str:
    """A lawn dart lying along +x with its point at the origin: steel spike, weighted body, fins.

    With ``solid`` every part is filled with that one colour, for the shadow.
    """
    paint = (lambda _gradient: solid) if solid else (lambda gradient: f"url(#{gradient})")
    highlights = "" if solid else """
<path d="M64,-12 C72,-14 84,-12 92,-9" stroke="#fff7d6" stroke-width="2.4" fill="none" stroke-linecap="round" opacity="0.85"/>
<path d="M66,-11.5 L66,11.5 M74,-12 L74,12" stroke="#c2271c" stroke-width="4"/>
"""
    return f"""
<g stroke="{outline}" stroke-width="3.2" stroke-linejoin="round">
  <path d="M120,-4 L152,-27 L162,-25 L146,-4 Z" fill="{paint('fin')}"/>
  <path d="M120,4 L152,27 L162,25 L146,4 Z" fill="{paint('fin')}"/>
  <path d="M96,-4.5 L150,-4 L150,4 L96,4.5 Z" fill="{paint('shaft')}"/>
  <path d="M120,-1.6 L158,-1.4 L164,0 L158,1.4 L120,1.6 Z" fill="{solid or '#c2271c'}" stroke-width="2"/>
  <path d="M48,-8 C58,-15 86,-12 100,-6 L100,6 C86,12 58,15 48,8 Z" fill="{paint('body')}"/>
  <path d="M2,0 L50,-6.5 L50,6.5 Z" fill="{paint('steel')}"/>
</g>{highlights}"""


def logo_svg() -> str:
    rng = random.Random(SEED + 1)
    c = LOGO / 2
    tufts = "".join(
        blade(rng, rng.uniform(10, 246), rng.uniform(10, 246), math.radians(-90) + rng.gauss(0, 0.45),
              rng.uniform(7, 13), rng.uniform(1.6, 2.6), hsv(rng.uniform(90, 108), 0.7, rng.uniform(0.32, 0.62)))
        for _ in range(420)
    )
    ring_c = (c - 18, c + 22)
    tip = ring_c
    angle = -45
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{LOGO}" height="{LOGO}" viewBox="0 0 {LOGO} {LOGO}">
<defs>
<radialGradient id="turf" cx="0.42" cy="0.36" r="0.75">
  <stop offset="0" stop-color="{hsv(96, 0.66, 0.74)}"/><stop offset="0.65" stop-color="{hsv(102, 0.74, 0.47)}"/>
  <stop offset="1" stop-color="{hsv(106, 0.8, 0.3)}"/>
</radialGradient>
<linearGradient id="steel" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#f4f7f9"/><stop offset="0.45" stop-color="#bac5cd"/><stop offset="1" stop-color="#66737d"/>
</linearGradient>
<linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#ffe27a"/><stop offset="0.5" stop-color="#ffc21f"/><stop offset="1" stop-color="#d88f00"/>
</linearGradient>
<linearGradient id="shaft" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#ffd54a"/><stop offset="1" stop-color="#c98500"/>
</linearGradient>
<linearGradient id="fin" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="#ff5a3c"/><stop offset="1" stop-color="#c2271c"/>
</linearGradient>
<clipPath id="disc"><circle cx="{c}" cy="{c}" r="112"/></clipPath>
<filter id="shadow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="2.4"/></filter>
</defs>
<circle cx="{c}" cy="{c + 3}" r="121" fill="#000" opacity="0.28" filter="url(#shadow)"/>
<circle cx="{c}" cy="{c}" r="120" fill="#123a0c"/>
<circle cx="{c}" cy="{c}" r="112" fill="url(#turf)"/>
<g clip-path="url(#disc)" opacity="0.9">{tufts}</g>
<circle cx="{ring_c[0] + 3}" cy="{ring_c[1] + 5}" r="54" fill="none" stroke="#0d2a07" stroke-width="15" opacity="0.4" filter="url(#shadow)"/>
<circle cx="{ring_c[0]}" cy="{ring_c[1]}" r="54" fill="none" stroke="#7a2c00" stroke-width="16"/>
<circle cx="{ring_c[0]}" cy="{ring_c[1]}" r="54" fill="none" stroke="#ff7a1a" stroke-width="11"/>
<circle cx="{ring_c[0]}" cy="{ring_c[1]}" r="55.5" fill="none" stroke="#ffb066" stroke-width="2.5" stroke-dasharray="40 60" stroke-dashoffset="18"/>
<g clip-path="url(#disc)"><g transform="translate({tip[0]} {tip[1]}) rotate(18) scale(1 0.62)" opacity="0.4" filter="url(#shadow)">{dart("#000", solid="#000")}</g></g>
<ellipse cx="{tip[0]}" cy="{tip[1] + 1}" rx="10" ry="6" fill="#2b1a08" opacity="0.85"/>
<g transform="translate({tip[0]} {tip[1]}) rotate({angle}) scale(1 1.25)">{dart("#14290c")}</g>
</svg>"""


def find_chrome() -> str:
    candidates = [
        os.environ.get("CHROME", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("google-chrome") or "",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise SystemExit("no Chrome, Edge or Chromium found; set CHROME to its path")


def render(chrome: str, svg: str, size: tuple[int, int], out: Path, work: Path) -> None:
    page = work / f"{out.stem}.html"
    page.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;padding:0;background:transparent;overflow:hidden}svg{display:block}"
        f"</style></head><body>{svg}</body></html>",
        encoding="utf-8",
    )
    subprocess.run(
        [
            chrome, "--headless=new", f"--user-data-dir={work / 'profile'}", "--no-first-run",
            "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
            "--default-background-color=00000000", f"--window-size={size[0]},{size[1]}",
            f"--screenshot={out}", page.as_uri(),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
    )
    if image_info(out)[1:] != size:
        raise SystemExit(f"{out} came out {image_info(out)[1:]}, expected {size}")


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "assets" / "lawndarts"
    out.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    with tempfile.TemporaryDirectory(prefix="lawndarts-") as tmp:
        work = Path(tmp)
        for source in (LAWN, PAINT):
            shutil.copyfile(fetch(source), work / source.file)
        shot = work / "wallpaper-rgba.png"
        render(chrome, wallpaper_svg(LAWN.file, PAINT.file), (WIDTH, HEIGHT), shot, work)
        write_png(out / "wallpaper.png", read_png(shot), level=9)
        render(chrome, logo_svg(), (LOGO, LOGO), out / "logo.png", work)
    lines = [
        "# Lawn Darts Linux artwork",
        "",
        "Made by scripts/make_lawndarts_assets.py. The wallpaper is built from these",
        "public domain (CC0 1.0) images; the logo is original vector art.",
        "",
        *(f"- {source.credit}" for source in (LAWN, PAINT)),
    ]
    (out / "CREDITS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'wallpaper.png'}, {out / 'logo.png'} and {out / 'CREDITS.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
