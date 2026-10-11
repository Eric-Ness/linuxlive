"""The catalog of bases, desktops and apps the compiler knows how to build."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Catalog:
    bases: dict[str, Base]
    desktops: dict[str, Desktop]
    apps: dict[str, App]

    def variant(self, desktop: str, base: str) -> DesktopVariant | None:
        return self.desktops[desktop].variants.get(base)


def _strings(value, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise CatalogError(f"{where} must be a list of non-empty strings")
    return tuple(value)


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
        apps[key] = App(id=key, name=_require(a, "name", where), flatpak=_require(a, "flatpak", where))
    if not bases or not desktops:
        raise CatalogError("catalog needs at least one base and one desktop")
    return Catalog(bases=bases, desktops=desktops, apps=apps)


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    path = DATA_DIR / "catalog.yaml"
    return parse_catalog(yaml.safe_load(path.read_text(encoding="utf-8")))
