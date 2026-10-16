#!/usr/bin/env python3
"""Check that everything the catalog names exists upstream.

  - every app's Flatpak ID has a Flathub listing
  - every Ubuntu package is in the release's amd64 archive
  - every Fedora package is in the release's repositories
  - every base container image tag exists

Prints one line per item and "CATALOG UPSTREAM OK" only when all of them pass.
Needs network access.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.catalog import load_catalog  # noqa: E402

UA = {"User-Agent": "linuxlive-catalog-check/0.1"}


def fetch(url: str, attempts: int = 5) -> tuple[int, bytes]:
    """Status and body; a dropped connection or timeout is tried again, then reported as status 0."""
    request = urllib.request.Request(url, headers=UA)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return exc.code, b""
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(2 * (attempt + 1))
    return 0, b""


def flathub(app_id: str) -> str | None:
    status, body = fetch(f"https://flathub.org/api/v2/appstream/{app_id}")
    if status != 200:
        return f"Flathub returned HTTP {status}"
    data = json.loads(body or b"null")
    if not isinstance(data, dict) or data.get("id") != app_id:
        return "Flathub has no appstream entry with this ID"
    return None


def ubuntu_package(release_codename: str, name: str) -> str | None:
    status, body = fetch(f"https://packages.ubuntu.com/{release_codename}/amd64/{name}")
    text = body.decode("utf-8", "replace")
    if status != 200 or "No such package" in text or f"Package: {name}" not in text:
        return f"not in Ubuntu {release_codename} for amd64"
    return None


def fedora_package(release: str, name: str) -> str | None:
    status, body = fetch(f"https://mdapi.fedoraproject.org/f{release}/pkg/{name}")
    if status != 200:
        return f"not in Fedora {release} (mdapi HTTP {status})"
    return None


def image_tag(ref: str) -> str | None:
    registry, _, rest = ref.partition("/")
    repo, _, tag = rest.rpartition(":")
    if registry == "docker.io":
        status, _ = fetch(f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}")
        return None if status == 200 else f"Docker Hub returned HTTP {status}"
    if registry == "quay.io":
        status, body = fetch(f"https://quay.io/api/v1/repository/{repo}/tag/?specificTag={tag}&onlyActiveTags=true")
        tags = json.loads(body or b"{}").get("tags") if status == 200 else None
        return None if tags else f"quay.io has no active tag {tag}"
    return f"no checker for registry {registry}"


UBUNTU_CODENAMES = {"26.04": "resolute"}


def main() -> int:
    catalog = load_catalog()
    checks: list[tuple[str, Callable[[], str | None]]] = []
    for app in catalog.apps.values():
        checks.append((f"flatpak {app.flatpak}", lambda a=app.flatpak: flathub(a)))
    for base in catalog.bases.values():
        replacements = [new for _old, new in base.replaced_packages]
        packages = [*base.system_packages, *base.live_packages, *base.kernel_packages, *base.efi_packages, *replacements]
        for desktop in catalog.desktops.values():
            variant = desktop.variants.get(base.id)
            if variant:
                packages += variant.packages
                if variant.image:
                    checks.append((f"image {variant.image}", lambda r=variant.image: image_tag(r)))
        if base.image:
            checks.append((f"image {base.image}", lambda r=base.image: image_tag(r)))
        # The languages' translations (compiler/options.py names them the same way) and kiosk mode.
        for lang in catalog.languages.values():
            if base.id == "ubuntu":
                packages += [f"language-pack-{lang.ubuntu}", f"language-pack-gnome-{lang.ubuntu}", f"language-pack-kde-{lang.ubuntu}", *lang.ubuntu_fonts]
            else:
                packages.append(f"langpacks-{lang.fedora}")
        if base.id == "ubuntu":
            packages.append("cinnamon-l10n")
        packages += catalog.kiosk_packages.get(base.id, ())
        if base.id == "ubuntu":
            codename = UBUNTU_CODENAMES[base.release]
            for name in dict.fromkeys(packages):
                checks.append((f"ubuntu {name}", lambda n=name, c=codename: ubuntu_package(c, n)))
        elif base.id == "fedora":
            for name in dict.fromkeys(packages):
                checks.append((f"fedora {name}", lambda n=name, r=base.release: fedora_package(r, n)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda item: (item[0], item[1]()), checks))
    failures = 0
    for label, problem in results:
        print(f"{'FAIL' if problem else 'ok  '} {label}{': ' + problem if problem else ''}")
        failures += bool(problem)
    if failures:
        print(f"CATALOG UPSTREAM FAILED: {failures} of {len(results)} items missing")
        return 1
    print(f"CATALOG UPSTREAM OK ({len(results)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
