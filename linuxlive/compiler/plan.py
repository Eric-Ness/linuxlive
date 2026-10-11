"""Resolve a spec against the catalog into everything the build needs."""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

from linuxlive.catalog import App, Base, Catalog, Desktop, DesktopVariant
from linuxlive.compiler.context import CompileError
from linuxlive.png import image_info
from linuxlive.spec import Spec

# GNOME 47+ accent colours (org.gnome.desktop.interface accent-color).
GNOME_ACCENTS = {
    "blue": "#3584e4",
    "teal": "#2190a4",
    "green": "#3a944a",
    "yellow": "#c88800",
    "orange": "#ed5b00",
    "red": "#e62d42",
    "pink": "#d56199",
    "purple": "#9141ac",
    "slate": "#6f8396",
}

WALLPAPER_DIR = "/usr/share/backgrounds/linuxlive"
LOGO_NAME = "linuxlive-logo"
MARKER_DIR = ".disk"


@dataclass(frozen=True)
class Plan:
    spec: Spec
    base: Base
    desktop: Desktop
    variant: DesktopVariant
    apps: tuple[App, ...]
    volid: str
    iso_name: str
    marker: str
    live_layout: str
    kernel_args: str
    wallpaper_file: str
    accent_rgb: tuple[int, int, int] | None
    gnome_accent: str | None

    @property
    def wallpaper_path(self) -> str:
        return f"{WALLPAPER_DIR}/{self.wallpaper_file}"

    @property
    def base_image(self) -> str:
        image = self.variant.image or self.base.image
        if not image:
            raise CompileError(f"no base image for {self.desktop.id} on {self.base.id}")
        return image

    def as_dict(self) -> dict:
        s = self.spec
        return {
            "name": s.name,
            "id": s.id,
            "version": s.version,
            "base": {"id": self.base.id, "label": self.base.label, "release": self.base.release},
            "base_image": self.base_image,
            "desktop": {"id": self.desktop.id, "label": self.desktop.label, "shell_process": self.desktop.shell_process},
            "apps": [{"id": a.id, "name": a.name, "flatpak": a.flatpak} for a in self.apps],
            "branding": {
                "wallpaper": self.wallpaper_path,
                "logo": f"/usr/share/pixmaps/{LOGO_NAME}.png",
                "accent_color": s.branding.accent_color,
                "gnome_accent": self.gnome_accent,
            },
            "iso": {
                "volume_id": self.volid,
                "file_name": self.iso_name,
                "layout": self.live_layout,
                "kernel_args": self.kernel_args,
            },
        }


def volume_id(spec: Spec) -> str:
    """ISO 9660 volume ID: upper case, A-Z 0-9 and underscore, at most 32 chars."""
    raw = f"{spec.id}_{spec.version}".upper()
    vol = re.sub(r"[^A-Z0-9_]+", "_", raw).strip("_")
    return re.sub(r"_+", "_", vol)[:32].rstrip("_")


def nearest_gnome_accent(hex_colour: str) -> str:
    r, g, b = parse_hex(hex_colour)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if s < 0.25 or v < 0.2:
        return "slate"
    best, best_distance = "blue", 361.0
    for name, ref in GNOME_ACCENTS.items():
        if name == "slate":
            continue
        rh, _rs, _rv = colorsys.rgb_to_hsv(*(c / 255 for c in parse_hex(ref)))
        d = abs(h - rh) * 360 % 360
        d = min(d, 360 - d)
        if d < best_distance:
            best, best_distance = name, d
    return best


def parse_hex(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def plan_build(spec: Spec, catalog: Catalog) -> Plan:
    base = catalog.bases[spec.base]
    desktop = catalog.desktops[spec.desktop]
    variant = catalog.variant(spec.desktop, spec.base)
    if variant is None:
        raise CompileError(f"{desktop.label} is not available on {base.label}")
    volid = volume_id(spec)
    if spec.base == "ubuntu":
        layout, kernel_args = "casper", "boot=casper quiet splash ---"
    elif spec.base == "fedora":
        layout = "liveos"
        kernel_args = f"root=live:CDLABEL={volid} rd.live.image rd.live.overlay.overlayfs=1 quiet rhgb"
    else:
        raise CompileError(f"no live boot layout for base {spec.base}")
    accent = spec.branding.accent_color
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", spec.version)
    return Plan(
        spec=spec,
        base=base,
        desktop=desktop,
        variant=variant,
        apps=tuple(catalog.apps[a] for a in spec.apps),
        volid=volid,
        iso_name=f"{spec.id}-{safe_version}-{spec.base}-amd64.iso",
        marker=f"{MARKER_DIR}/linuxlive-{volid.lower()}",
        live_layout=layout,
        kernel_args=kernel_args,
        # Name the file after its real format, whatever extension the user gave it.
        wallpaper_file="wallpaper.png" if image_info(spec.branding.wallpaper)[0] == "png" else "wallpaper.jpg",
        accent_rgb=parse_hex(accent) if accent else None,
        gnome_accent=nearest_gnome_accent(accent) if accent and spec.desktop == "gnome" else None,
    )
