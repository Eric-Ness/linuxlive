"""Load and validate linuxlive distro specs.

A spec is a small YAML file. Validation happens in two passes: the JSON Schema
in data/spec.schema.json checks shape and formats (the same schema can drive a
web form later), then the checks here cover what a schema cannot: catalog
lookups and the branding image files themselves. Every problem found is
reported at once, so a user can fix a spec in one go.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import jsonschema
import yaml

from linuxlive.catalog import DATA_DIR, Catalog, load_catalog
from linuxlive.png import ImageError, image_info

MIN_WALLPAPER = (1280, 720)
LOGO_SIZES = (128, 256, 512)
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,29}$")


class SpecError(ValueError):
    """A spec failed validation. ``problems`` lists every issue found."""

    def __init__(self, path: Path | str, problems: list[str]):
        self.path = Path(path)
        self.problems = list(problems)
        super().__init__(f"{path}: " + "; ".join(self.problems))


@dataclass(frozen=True)
class Branding:
    wallpaper: Path
    logo: Path
    accent_color: str | None


@dataclass(frozen=True)
class Spec:
    path: Path
    name: str
    id: str
    version: str
    base: str
    desktop: str
    apps: tuple[str, ...]
    branding: Branding


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads((DATA_DIR / "spec.schema.json").read_text(encoding="utf-8"))


def derive_id(name: str) -> str:
    """Turn a display name into an ID: "My Distro 2" becomes "my-distro-2"."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if slug and not slug[0].isalpha():
        slug = "os-" + slug
    return slug[:30].rstrip("-")


def _schema_problem(error: jsonschema.ValidationError) -> str:
    where = "/".join(str(p) for p in error.absolute_path) or "spec"
    return f"{where}: {error.message}"


def load_spec(path: Path | str, catalog: Catalog | None = None) -> Spec:
    """Read, validate and resolve a spec. Raises SpecError listing all problems."""
    path = Path(path)
    catalog = catalog or load_catalog()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SpecError(path, ["file not found"]) from None
    except yaml.YAMLError as exc:
        raise SpecError(path, [f"not valid YAML: {exc}"]) from None
    if not isinstance(raw, dict):
        raise SpecError(path, ["the top level must be a mapping of settings"])

    validator = jsonschema.Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise SpecError(path, [_schema_problem(e) for e in errors])

    problems: list[str] = []
    name = raw["name"]
    spec_id = raw.get("id") or derive_id(name)
    if not ID_PATTERN.match(spec_id):
        problems.append(f"id: could not derive a valid id from name {name!r}; set id explicitly")

    base, desktop = raw["base"], raw["desktop"]
    if catalog.variant(desktop, base) is None:
        problems.append(f"desktop: {desktop} is not available on {base}")

    apps = tuple(raw.get("apps") or ())
    for app in apps:
        if app not in catalog.apps:
            known = ", ".join(sorted(catalog.apps))
            problems.append(f"apps: unknown app {app!r} (known apps: {known})")

    branding = raw["branding"]
    wallpaper = (path.parent / branding["wallpaper"]).resolve()
    logo = (path.parent / branding["logo"]).resolve()
    problems += _check_wallpaper(wallpaper, branding["wallpaper"])
    problems += _check_logo(logo, branding["logo"])

    if problems:
        raise SpecError(path, problems)
    accent = branding.get("accent_color")
    return Spec(
        path=path.resolve(),
        name=name,
        id=spec_id,
        version=raw["version"],
        base=base,
        desktop=desktop,
        apps=apps,
        branding=Branding(wallpaper=wallpaper, logo=logo, accent_color=accent.lower() if accent else None),
    )


def _check_wallpaper(path: Path, given: str) -> list[str]:
    if not path.is_file():
        return [f"branding/wallpaper: file {given!r} not found"]
    try:
        _fmt, width, height = image_info(path)
    except ImageError as exc:
        return [f"branding/wallpaper: {given!r} is not usable: {exc}"]
    min_w, min_h = MIN_WALLPAPER
    if width < min_w or height < min_h:
        return [f"branding/wallpaper: {given!r} is {width}x{height}; it must be at least {min_w}x{min_h}"]
    return []


def _check_logo(path: Path, given: str) -> list[str]:
    if not path.is_file():
        return [f"branding/logo: file {given!r} not found"]
    try:
        fmt, width, height = image_info(path)
    except ImageError as exc:
        return [f"branding/logo: {given!r} is not usable: {exc}"]
    if fmt != "png":
        return [f"branding/logo: {given!r} must be a PNG"]
    if width != height or width not in LOGO_SIZES:
        sizes = ", ".join(str(s) for s in LOGO_SIZES)
        return [f"branding/logo: {given!r} is {width}x{height}; it must be square, {sizes} pixels"]
    return []
