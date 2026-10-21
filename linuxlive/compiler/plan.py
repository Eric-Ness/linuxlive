"""Resolve a spec against the catalog into everything the build needs."""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass

from linuxlive.catalog import App, Base, Catalog, Desktop, DesktopVariant, Language
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
# Each desktop's file manager, always first on a dock the spec sets (settings.dock).
FILE_MANAGERS = {"gnome": "org.gnome.Nautilus.desktop", "kde": "org.kde.dolphin.desktop", "cinnamon": "nemo.desktop"}
MARKER_DIR = ".disk"


@dataclass(frozen=True)
class Arch:
    """One CPU architecture, as each tool names it."""

    name: str  # Debian's and the ISO file name's: amd64, arm64
    machine: str  # the kernel's and Flatpak's: x86_64, aarch64
    efi: str  # UEFI's file suffix: x64 (BOOTX64.EFI, shimx64.efi), aa64
    grub_target: str  # grub-install's UEFI target
    bios: bool  # also boots on BIOS (El Torito and MBR GRUB)

    @property
    def boot_efi(self) -> str:
        return f"BOOT{self.efi.upper()}.EFI"


ARCHES = {
    "amd64": Arch("amd64", "x86_64", "x64", "x86_64-efi", bios=True),
    "arm64": Arch("arm64", "aarch64", "aa64", "arm64-efi", bios=False),
}


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
    # Settings (spec.settings) resolved against the catalog.
    language: Language | None = None
    keyboard: str | None = None
    hostname: str = ""
    kiosk_app: App | None = None
    kiosk_packages: tuple[str, ...] = ()
    kiosk_x11: bool = False
    arch: Arch = ARCHES["amd64"]

    @property
    def flatpak_arch(self) -> str:
        return self.arch.machine

    def base_packages(self, kind: str) -> tuple[str, ...]:
        """The base's package list for this plan's architecture."""
        return self.base.packages(kind, self.arch.name)

    @property
    def wallpaper_path(self) -> str:
        return f"{WALLPAPER_DIR}/{self.wallpaper_file}"

    @property
    def dock(self) -> tuple[str, ...]:
        """The dock's (or panel's) pinned apps as desktop file names: the file
        manager, then the spec's settings.dock in order. Empty: the desktop's own."""
        if not self.spec.settings.dock:
            return ()
        by_id = {app.id: app for app in self.apps}
        return (FILE_MANAGERS[self.desktop.id],) + tuple(f"{by_id[a].flatpak}.desktop" for a in self.spec.settings.dock)

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
                "arch": self.arch.name,
                "persistence": s.persistence,
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


def flathub_app(app_id: str) -> App:
    """An app from the spec's flathub list: its Flathub ID is its id and name."""
    return App(id=app_id, name=app_id, flatpak=app_id)


def plan_build(spec: Spec, catalog: Catalog, arch: str = "") -> Plan:
    """The build for one of the spec's architectures (its first when not given)."""
    arch = arch or spec.arches[0]
    if arch not in ARCHES:
        raise CompileError(f"no architecture {arch!r}; linuxlive builds {', '.join(ARCHES)}")
    if arch not in spec.arches:
        raise CompileError(f"the spec builds for {', '.join(spec.arches)}, not {arch}; add it to arches")
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
        apps=tuple(catalog.apps[a] for a in spec.apps) + tuple(flathub_app(a) for a in spec.flathub),
        volid=volid,
        iso_name=f"{spec.id}-{safe_version}-{spec.base}-{arch}.iso",
        marker=f"{MARKER_DIR}/linuxlive-{volid.lower()}",
        live_layout=layout,
        kernel_args=kernel_args,
        # Name the file after its real format, whatever extension the user gave it.
        wallpaper_file="wallpaper.png" if image_info(spec.branding.wallpaper)[0] == "png" else "wallpaper.jpg",
        accent_rgb=parse_hex(accent) if accent else None,
        gnome_accent=nearest_gnome_accent(accent) if accent and spec.desktop == "gnome" else None,
        language=catalog.languages[spec.settings.language] if spec.settings.language else None,
        keyboard=spec.settings.keyboard,
        hostname=spec.settings.hostname or spec.id,
        kiosk_app=(catalog.apps.get(spec.kiosk) or flathub_app(spec.kiosk)) if spec.kiosk else None,
        kiosk_packages=catalog.kiosk_packages.get(spec.base, ()) if spec.kiosk else (),
        kiosk_x11=bool(spec.kiosk) and spec.base in catalog.kiosk_x11,
        arch=ARCHES[arch],
    )
