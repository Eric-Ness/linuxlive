"""A distro's own additions: distribution packages, files, scripts and GitHub code.

Packages are installed with the base's package manager after the desktop, the
installer and linuxlive's own packages. Files land in the image's root file
system with everything else under rootfs/, but may not replace a file linuxlive
writes. Build scripts and repos run as one step after that, before linuxlive's
finalize, so branding, the installer and the boot checks always come last and
still get tested. First-boot and first-login scripts are placed in the image,
where small runners start them (usr/libexec/linuxlive/first-boot-scripts and
first-login-scripts).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from linuxlive.compiler.context import BuildContext, CompileError
from linuxlive.compiler.plan import Plan

# In the build context; the Containerfile copies it to EXTRAS_IN_IMAGE for the build step.
EXTRAS_DIR = "extras"
EXTRAS_IN_IMAGE = "/tmp/linuxlive-extras"
FIRST_BOOT_DIR = "usr/lib/linuxlive/first-boot.d"
FIRST_LOGIN_DIR = "usr/lib/linuxlive/first-login.d"
MANIFEST = "usr/lib/linuxlive/extras.json"


def script_name(index: int, src: Path) -> str:
    """The name a script runs under: its place in the list, then its own name."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", src.name).strip("-.") or "script"
    return f"{(index + 1) * 10:02d}-{stem}"


def script_names(scripts: tuple[Path, ...]) -> list[str]:
    return [script_name(i, src) for i, src in enumerate(scripts)]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def has_build_step(plan: Plan) -> bool:
    return bool(plan.spec.scripts.build or plan.spec.repos)


def add_extras(ctx: BuildContext, plan: Plan) -> None:
    """Add the spec's files and scripts. Runs after every other file is in place."""
    spec = plan.spec
    taken = set(ctx.paths())
    for f in spec.files:
        path = f"rootfs{f.dest}"
        if path in taken:
            raise CompileError(f"files: {f.dest} is a file linuxlive writes itself; pick another place")
        ctx.add_bytes(path, f.src.read_bytes(), mode=f.mode)
    for name, src in zip(script_names(spec.scripts.build), spec.scripts.build):
        ctx.add_bytes(f"{EXTRAS_DIR}/build-scripts/{name}", _text(src), mode=0o755)
    for i, repo in enumerate(spec.repos):
        number = f"{(i + 1) * 10:02d}"
        ctx.add_text(f"{EXTRAS_DIR}/repos/{number}.repo", f"{repo.url}\n{repo.commit}\n")
        ctx.add_text(f"{EXTRAS_DIR}/repos/{number}.run", repo.run, mode=0o755)
    for name, src in zip(script_names(spec.scripts.first_boot), spec.scripts.first_boot):
        ctx.add_bytes(f"rootfs/{FIRST_BOOT_DIR}/{name}", _text(src), mode=0o755)
    for name, src in zip(script_names(spec.scripts.first_login), spec.scripts.first_login):
        ctx.add_bytes(f"rootfs/{FIRST_LOGIN_DIR}/{name}", _text(src), mode=0o755)
    if spec.scripts.first_login:
        ctx.add_text("rootfs/etc/xdg/autostart/linuxlive-first-login.desktop", "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            f"Name={spec.name} first-login setup",
            "Exec=/usr/libexec/linuxlive/first-login-scripts",
            "NoDisplay=true",
            "X-GNOME-Autostart-Phase=Application",
        ]))
    if spec.packages or spec.files:
        # What the boot check looks for (usr/libexec/linuxlive/bootcheck).
        ctx.add_text(f"rootfs/{MANIFEST}", json.dumps({
            "packages": list(spec.packages),
            "files": [f.dest for f in spec.files],
        }, indent=2))


def has_extras(plan: Plan) -> bool:
    spec = plan.spec
    return bool(spec.packages or spec.files or spec.runs_own_code)


def _text(src: Path) -> bytes:
    return src.read_bytes().replace(b"\r\n", b"\n")


def packages_step(plan: Plan) -> str:
    """The Containerfile step installing the spec's packages, or nothing."""
    packages = list(plan.spec.packages)
    if plan.base.id == "ubuntu" and plan.spec.repos:
        # Repos are fetched over HTTPS inside the build (usr/libexec/linuxlive/user-build).
        packages += [p for p in ("curl", "ca-certificates") if p not in packages]
    if not packages:
        return ""
    names = " ".join(packages)
    if plan.base.id == "ubuntu":
        run = ("RUN set -eux; \\\n    apt-get update; \\\n    apt-get install -y " + names
               + "; \\\n    apt-get clean; \\\n    rm -rf /var/lib/apt/lists/*")
    else:
        run = f"RUN set -eux; \\\n    dnf -y install {names}; \\\n    dnf clean all"
    return f"\n# The distro's own packages (packages in the spec).\n{run}\n"


def build_step(plan: Plan) -> str:
    """The Containerfile step running the spec's repos and build scripts, or nothing."""
    if not has_build_step(plan):
        return ""
    return (
        "\n# The distro's own build steps (repos, then scripts.build in the spec), as root.\n"
        f"COPY {EXTRAS_DIR}/ {EXTRAS_IN_IMAGE}/\n"
        f"RUN /bin/sh /usr/libexec/linuxlive/user-build {EXTRAS_IN_IMAGE} && rm -rf {EXTRAS_IN_IMAGE}\n"
    )


def as_dict(plan: Plan) -> dict:
    spec = plan.spec
    return {
        "packages": list(spec.packages),
        "files": [{"dest": f.dest, "mode": f"{f.mode:04o}", "sha256": file_sha256(f.src)} for f in spec.files],
        "scripts": {
            "build": script_names(spec.scripts.build),
            "first-boot": script_names(spec.scripts.first_boot),
            "first-login": script_names(spec.scripts.first_login),
        },
        "repos": [{"url": r.url, "commit": r.commit} for r in spec.repos],
    }
