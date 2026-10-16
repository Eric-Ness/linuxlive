"""Any app from Flathub, not just the catalog's: checks against Flathub's API.

A spec's ``flathub`` list names apps by their Flathub ID. Validating a spec
only checks the IDs' shape, so it works offline; ``validate --online`` (which
the build workflow runs before anything is built) asks Flathub's API that each
app exists, is a desktop or console app, and is built for the CPU the ISO is
for. Flathub's verified flag (the developer proved they publish it) is
reported, not required.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://flathub.org/api/v2"
USER_AGENT = "linuxlive (https://github.com/Eric-Ness/linuxlive)"


@dataclass(frozen=True)
class FlathubApp:
    id: str
    name: str
    arches: tuple[str, ...]
    verified: bool
    type: str
    # Built on a runtime that gets no more updates: often an app that moved to a new ID.
    eol: bool = False


class FlathubError(RuntimeError):
    """Flathub could not be asked (network), as opposed to an app it lacks."""


def _get(path: str, attempts: int = 4) -> dict | None:
    """JSON from the API, None for a 404; retries dropped connections and 5xx."""
    request = urllib.request.Request(f"{API}{path}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last = ""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = f"HTTP {exc.code}"
            if exc.code < 500 and exc.code != 429:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
            last = str(exc)
        time.sleep(2 * (attempt + 1))
    raise FlathubError(f"Flathub's API did not answer for {path} ({last})")


def lookup(app_id: str) -> FlathubApp | None:
    """The app as Flathub lists it, or None when Flathub has no such app."""
    appstream = _get(f"/appstream/{app_id}")
    if not isinstance(appstream, dict) or appstream.get("id") != app_id:
        return None
    summary = _get(f"/summary/{app_id}") or {}
    verification = _get(f"/verification/{app_id}/status") or {}
    return FlathubApp(
        id=app_id,
        name=str(appstream.get("name") or app_id),
        arches=tuple(summary.get("arches") or ()),
        verified=bool(verification.get("verified")),
        type=str(appstream.get("type") or ""),
        eol=bool((summary.get("metadata") or {}).get("runtimeIsEol")),
    )


def check_apps(app_ids: tuple[str, ...], arch: str = "x86_64") -> tuple[list[str], list[FlathubApp]]:
    """Problems with a spec's Flathub apps, and what Flathub says about each found."""
    problems: list[str] = []
    found: list[FlathubApp] = []
    for i, app_id in enumerate(app_ids):
        app = lookup(app_id)
        if app is None:
            problems.append(f"flathub/{i}: Flathub has no app {app_id!r}")
            continue
        found.append(app)
        if app.type not in ("desktop-application", "console-application"):
            problems.append(f"flathub/{i}: {app_id} is a {app.type or 'thing'} on Flathub, not an app")
        if arch not in app.arches:
            problems.append(f"flathub/{i}: {app_id} is not built for {arch} on Flathub (only {', '.join(app.arches) or 'no CPU'})")
    return problems, found
