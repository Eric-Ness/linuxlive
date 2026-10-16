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
from pathlib import Path, PurePosixPath

import jsonschema
import yaml

from linuxlive.catalog import DATA_DIR, Catalog, load_catalog
from linuxlive.png import ImageError, image_info

MIN_WALLPAPER = (1280, 720)
LOGO_SIZES = (128, 256, 512)
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,29}$")
MAX_FILES_BYTES = 20 << 20
MAX_SCRIPT_BYTES = 256 << 10
# Where linuxlive keeps its own files; a spec's files can't replace them.
RESERVED_DESTS = (
    "/etc/linuxlive",
    "/usr/lib/linuxlive",
    "/usr/libexec/linuxlive",
    "/usr/lib/os-release",
    "/etc/os-release",
)


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
class ExtraFile:
    src: Path
    dest: str
    mode: int


@dataclass(frozen=True)
class Repo:
    url: str
    commit: str
    run: str

    @property
    def slug(self) -> str:
        """owner/name"""
        return self.url.removeprefix("https://github.com/")


@dataclass(frozen=True)
class Scripts:
    build: tuple[Path, ...] = ()
    first_boot: tuple[Path, ...] = ()
    first_login: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Settings:
    """How the system is set up. None (or empty) keeps the base's own default."""

    language: str | None = None
    keyboard: str | None = None
    timezone: str | None = None
    hostname: str | None = None
    autologin: bool = False
    hidden_apps: tuple[str, ...] = ()


@dataclass(frozen=True)
class Bookmark:
    name: str
    url: str


@dataclass(frozen=True)
class Firefox:
    homepage: str | None = None
    bookmarks: tuple[Bookmark, ...] = ()


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
    # Any other Flathub apps, by Flathub ID (checked online by validate --online).
    flathub: tuple[str, ...] = ()
    # A distro's own additions: distribution packages, files, scripts and GitHub code.
    packages: tuple[str, ...] = ()
    files: tuple[ExtraFile, ...] = ()
    scripts: Scripts = Scripts()
    repos: tuple[Repo, ...] = ()
    # Settings and no-code options: all data, nothing that runs.
    settings: Settings = Settings()
    firefox: Firefox | None = None
    kiosk: str | None = None  # the app kiosk mode shows
    # Save changes to the USB stick (casper's persistence; Ubuntu only).
    persistence: bool = False
    # The CPUs to build ISOs for, one ISO each.
    arches: tuple[str, ...] = ("amd64",)

    @property
    def runs_own_code(self) -> bool:
        """The spec brings code of its own: scripts or repos (not just packages and files)."""
        s = self.scripts
        return bool(s.build or s.first_boot or s.first_login or self.repos)


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

    flathub = tuple(raw.get("flathub") or ())
    catalog_ids = {a.flatpak.lower(): a.id for a in catalog.apps.values()}
    for i, app_id in enumerate(flathub):
        if app_id.lower() in catalog_ids:
            problems.append(f"flathub/{i}: {app_id} is in the catalog; list it in apps as {catalog_ids[app_id.lower()]!r}")

    branding = raw["branding"]
    wallpaper = (path.parent / branding["wallpaper"]).resolve()
    logo = (path.parent / branding["logo"]).resolve()
    problems += _check_wallpaper(wallpaper, branding["wallpaper"])
    problems += _check_logo(logo, branding["logo"])

    folder = path.parent.resolve()
    files, file_problems = _resolve_files(folder, base, raw.get("files") or [])
    problems += file_problems
    scripts_raw = raw.get("scripts") or {}
    script_lists = {}
    for key in ("build", "first-boot", "first-login"):
        resolved, script_problems = _resolve_scripts(folder, key, scripts_raw.get(key) or [])
        script_lists[key] = resolved
        problems += script_problems
    repos = tuple(Repo(url=r["url"], commit=r["commit"], run=r["run"]) for r in raw.get("repos") or [])
    seen_repos = set()
    for repo in repos:
        key = (repo.url.lower(), repo.commit)
        if key in seen_repos:
            problems.append(f"repos: {repo.url} at {repo.commit} is listed twice")
        seen_repos.add(key)

    settings, firefox, kiosk, option_problems = _resolve_options(raw, apps + flathub, catalog)
    problems += option_problems
    persistence = bool(raw.get("persistence", False))
    if persistence and base != "ubuntu":
        problems.append(
            "persistence: only Ubuntu-based distros can save changes to the USB stick so far "
            "(Fedora's live boot keeps a saved layer only on a partition nothing makes on a written stick)"
        )

    arches = tuple(raw.get("arches") or ("amd64",))
    for app in apps:
        missing = [a for a in arches if app in catalog.apps and a not in catalog.apps[app].arches]
        if missing:
            problems.append(f"apps: {app} is not built for {', '.join(missing)} on Flathub; take it out or drop {', '.join(missing)} from arches")

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
        flathub=flathub,
        branding=Branding(wallpaper=wallpaper, logo=logo, accent_color=accent.lower() if accent else None),
        packages=tuple(raw.get("packages") or ()),
        files=files,
        scripts=Scripts(
            build=script_lists["build"],
            first_boot=script_lists["first-boot"],
            first_login=script_lists["first-login"],
        ),
        repos=repos,
        settings=settings,
        firefox=firefox,
        kiosk=kiosk,
        persistence=persistence,
        arches=arches,
    )


@lru_cache(maxsize=1)
def known_timezones() -> frozenset[str]:
    """Time zone names from the tz database (the tzdata package where the system has none)."""
    try:
        import zoneinfo

        return frozenset(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001 - no tz database: the image build checks the name
        return frozenset()


def _resolve_options(raw: dict, apps: tuple[str, ...], catalog: Catalog) -> tuple[Settings, Firefox | None, str | None, list[str]]:
    problems: list[str] = []
    s = raw.get("settings") or {}
    language, keyboard, timezone = s.get("language"), s.get("keyboard"), s.get("timezone")
    if language and language not in catalog.languages:
        problems.append(f"settings/language: {language!r} is not one linuxlive knows (known: {', '.join(sorted(catalog.languages))})")
    if keyboard and keyboard not in catalog.keyboards:
        problems.append(f"settings/keyboard: {keyboard!r} is not one linuxlive knows (known: {', '.join(sorted(catalog.keyboards))})")
    zones = known_timezones()
    if timezone and zones and timezone not in zones:
        problems.append(f"settings/timezone: {timezone!r} is not a time zone; use a name like Europe/Berlin")
    hidden = tuple(s.get("hidden_apps") or ())
    for name in hidden:
        if name.lower().startswith("linuxlive"):
            problems.append(f"settings/hidden_apps: {name!r} is linuxlive's own")
    settings = Settings(
        language=language,
        keyboard=keyboard,
        timezone=timezone,
        hostname=s.get("hostname"),
        autologin=bool(s.get("autologin", False)),
        hidden_apps=hidden,
    )
    firefox = None
    if "firefox" in raw:
        f = raw.get("firefox") or {}
        firefox = Firefox(
            homepage=f.get("homepage"),
            bookmarks=tuple(Bookmark(name=b["name"], url=b["url"]) for b in f.get("bookmarks") or ()),
        )
        if "firefox" not in apps:
            problems.append("firefox: the home page and bookmarks are Firefox's; add firefox to apps")
        if firefox.homepage is None and not firefox.bookmarks:
            firefox = None
    kiosk = (raw.get("kiosk") or {}).get("app")
    if kiosk is not None and kiosk not in apps:
        problems.append(f"kiosk/app: {kiosk!r} must be one of the spec's apps or flathub apps")
    return settings, firefox, kiosk, problems


def _inside(folder: Path, given: str) -> Path | None:
    """The file a spec names, if it is inside the spec's folder."""
    candidate = (folder / given).resolve()
    return candidate if candidate.is_relative_to(folder) else None


def _resolve_files(folder: Path, base: str, entries: list[dict]) -> tuple[tuple[ExtraFile, ...], list[str]]:
    problems: list[str] = []
    files: list[ExtraFile] = []
    dests: set[str] = set()
    total = 0
    for i, entry in enumerate(entries):
        where = f"files/{i}"
        dest = entry["dest"]
        parts = PurePosixPath(dest).parts
        if dest.endswith("/") or "//" in dest or any(p in (".", "..") for p in dest.split("/")):
            problems.append(f"{where}/dest: {dest!r} must be a plain path to a file")
        elif any(dest == r or dest.startswith(r + "/") for r in RESERVED_DESTS):
            problems.append(f"{where}/dest: {dest!r} is where linuxlive keeps its own files")
        elif base == "fedora" and len(parts) > 2 and parts[1:3] == ("usr", "local"):
            # On Fedora Atomic /usr/local is a link into /var, which an image cannot fill.
            problems.append(f"{where}/dest: {dest!r} is not part of a Fedora Atomic image; use /usr or /etc")
        if dest in dests:
            problems.append(f"{where}/dest: {dest!r} is used twice")
        dests.add(dest)
        src = _inside(folder, entry["src"])
        if src is None:
            problems.append(f"{where}/src: {entry['src']!r} must be inside the spec's folder")
            continue
        if not src.is_file():
            problems.append(f"{where}/src: file {entry['src']!r} not found")
            continue
        total += src.stat().st_size
        files.append(ExtraFile(src=src, dest=dest, mode=int(entry.get("mode", "0644"), 8)))
    if total > MAX_FILES_BYTES:
        problems.append(f"files: {total / (1 << 20):.1f} MB in all; the limit is {MAX_FILES_BYTES >> 20} MB")
    return tuple(files), problems


def _resolve_scripts(folder: Path, key: str, entries: list[str]) -> tuple[tuple[Path, ...], list[str]]:
    problems: list[str] = []
    scripts: list[Path] = []
    for i, given in enumerate(entries):
        where = f"scripts/{key}/{i}"
        src = _inside(folder, given)
        if src is None:
            problems.append(f"{where}: {given!r} must be inside the spec's folder")
            continue
        if not src.is_file():
            problems.append(f"{where}: file {given!r} not found")
            continue
        data = src.read_bytes()
        if len(data) > MAX_SCRIPT_BYTES:
            problems.append(f"{where}: {given!r} is {len(data)} bytes; a script can be at most {MAX_SCRIPT_BYTES >> 10} KB")
            continue
        if b"\0" in data:
            problems.append(f"{where}: {given!r} is not a text file")
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{where}: {given!r} is not a text file (UTF-8)")
            continue
        scripts.append(src)
    return tuple(scripts), problems


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
