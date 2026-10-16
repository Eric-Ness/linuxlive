"""The catalog of bases, desktops and apps the compiler knows how to build."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent / "data"


class CatalogError(ValueError):
    """The catalog file is malformed."""


@dataclass(frozen=True)
class Base:
    id: str
    label: str
    release: str
    image: str | None
    system_packages: tuple[str, ...]
    live_packages: tuple[str, ...]
    kernel_packages: tuple[str, ...]
    blocked_packages: tuple[str, ...]
    efi_packages: tuple[str, ...]
    # Upstream package -> its neutral replacement, swapped when the image has it.
    replaced_packages: tuple[tuple[str, str], ...] = ()
    # The installer (Ubuntu only so far): its tools in the image, and the
    # bootloader packages it installs onto the target disk.
    installer_packages: tuple[str, ...] = ()
    bootloader_packages: tuple[str, ...] = ()
    # Per-architecture replacements for the package lists above (arm64: ...).
    arch_packages: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)

    def packages(self, kind: str, arch: str) -> tuple[str, ...]:
        """A package list (efi_packages, bootloader_packages, ...) for one architecture."""
        return self.arch_packages.get(arch, {}).get(kind, getattr(self, kind))


@dataclass(frozen=True)
class DesktopVariant:
    """How one desktop is installed on one base."""

    packages: tuple[str, ...]
    image: str | None
    livesys_session: str | None


@dataclass(frozen=True)
class Desktop:
    id: str
    label: str
    shell_process: str
    variants: dict[str, DesktopVariant]


@dataclass(frozen=True)
class App:
    id: str
    name: str
    flatpak: str
    # Arguments after the app in kiosk mode (Firefox's --kiosk).
    kiosk_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Language:
    """A locale a spec can set, and where each base gets its translations."""

    id: str
    label: str
    ubuntu: str
    fedora: str
    ubuntu_fonts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalog:
    bases: dict[str, Base]
    desktops: dict[str, Desktop]
    apps: dict[str, App]
    languages: dict[str, Language] = field(default_factory=dict)
    # XKB layout -> label.
    keyboards: dict[str, str] = field(default_factory=dict)
    # Base -> the packages kiosk mode adds.
    kiosk_packages: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Bases whose kiosk app runs through Xwayland (their Cage is too old for Wayland apps going full screen).
    kiosk_x11: tuple[str, ...] = ()

    def variant(self, desktop: str, base: str) -> DesktopVariant | None:
        return self.desktops[desktop].variants.get(base)


def _strings(value, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CatalogError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _pairs(value, where: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items()):
        raise CatalogError(f"{where} must map package names to package names")
    return tuple(value.items())


def _require(mapping: dict, key: str, where: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{where}.{key} must be a non-empty string")
    return value


def parse_catalog(raw: dict) -> Catalog:
    if not isinstance(raw, dict):
        raise CatalogError("catalog must be a mapping")
    bases = {}
    for key, b in (raw.get("bases") or {}).items():
        where = f"bases.{key}"
        bases[key] = Base(
            id=key,
            label=_require(b, "label", where),
            release=_require(b, "release", where),
            image=b.get("image"),
            system_packages=_strings(b.get("system_packages"), f"{where}.system_packages"),
            live_packages=_strings(b.get("live_packages"), f"{where}.live_packages"),
            kernel_packages=_strings(b.get("kernel_packages"), f"{where}.kernel_packages"),
            blocked_packages=_strings(b.get("blocked_packages"), f"{where}.blocked_packages"),
            efi_packages=_strings(b.get("efi_packages"), f"{where}.efi_packages"),
            replaced_packages=_pairs(b.get("replaced_packages"), f"{where}.replaced_packages"),
            installer_packages=_strings(b.get("installer_packages"), f"{where}.installer_packages"),
            bootloader_packages=_strings(b.get("bootloader_packages"), f"{where}.bootloader_packages"),
            arch_packages={
                arch: {kind: _strings(names, f"{where}.{arch}.{kind}") for kind, names in (b.get(arch) or {}).items()}
                for arch in ("arm64",) if b.get(arch)
            },
        )
    desktops = {}
    for key, d in (raw.get("desktops") or {}).items():
        where = f"desktops.{key}"
        variants = {}
        for base_id in bases:
            v = d.get(base_id)
            if v is None:
                continue
            variants[base_id] = DesktopVariant(
                packages=_strings(v.get("packages"), f"{where}.{base_id}.packages"),
                image=v.get("image"),
                livesys_session=v.get("livesys_session"),
            )
        desktops[key] = Desktop(
            id=key,
            label=_require(d, "label", where),
            shell_process=_require(d, "shell_process", where),
            variants=variants,
        )
    apps = {}
    for key, a in (raw.get("apps") or {}).items():
        where = f"apps.{key}"
        apps[key] = App(id=key, name=_require(a, "name", where), flatpak=_require(a, "flatpak", where),
                        kiosk_args=_strings(a.get("kiosk_args"), f"{where}.kiosk_args"))
    languages = {}
    for key, lang in (raw.get("languages") or {}).items():
        where = f"languages.{key}"
        languages[key] = Language(
            id=key,
            label=_require(lang, "label", where),
            ubuntu=_require(lang, "ubuntu", where),
            fedora=_require(lang, "fedora", where),
            ubuntu_fonts=_strings(lang.get("ubuntu_fonts"), f"{where}.ubuntu_fonts"),
        )
    keyboards = raw.get("keyboards") or {}
    if not isinstance(keyboards, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in keyboards.items()):
        raise CatalogError("keyboards must map XKB layouts to labels")
    kiosk = {base_id: _strings(names, f"kiosk.{base_id}") for base_id, names in (raw.get("kiosk") or {}).items()}
    kiosk_x11 = _strings(raw.get("kiosk_x11"), "kiosk_x11")
    if not bases or not desktops:
        raise CatalogError("catalog needs at least one base and one desktop")
    return Catalog(bases=bases, desktops=desktops, apps=apps, languages=languages, keyboards=dict(keyboards), kiosk_packages=kiosk,
                   kiosk_x11=kiosk_x11)


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    path = DATA_DIR / "catalog.yaml"
    return parse_catalog(yaml.safe_load(path.read_text(encoding="utf-8")))
