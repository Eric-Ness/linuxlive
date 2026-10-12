"""Check CI build and boot results against the spec they came from.

CI writes a results directory:

  meta.json                   commit, base and spec path of the run
  iso.json                    `linuxlive inspect-iso` output
  modes/<mode>/result.json    one QEMU boot (scripts/boottest.py)
  modes/<mode>/*.png          screenshots from that boot

This module turns those into pass/fail outcomes. Each expectation is derived
from the spec and catalog, never copied from the results, so a run that boots
the wrong thing fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from linuxlive.catalog import Catalog, load_catalog
from linuxlive.compiler.plan import LOGO_NAME, Plan, plan_build
from linuxlive.png import ImageError, dominant_hue, hue_fraction, image_info, read_png
from linuxlive.spec import Spec

CHECKS = ("iso", "boot", "session")
BOOT_MODES = ("bios-cd", "uefi-cd", "uefi-sb-cd", "bios-usb", "uefi-usb")
SECURE_BOOT_MODE = "uefi-sb-cd"
SESSION_MODE = "uefi-usb"
HUE_THRESHOLD = 0.25
MB = 1 << 20

COMMON_FILES = {
    ".disk/info": 1,
    "boot/grub/grub.cfg": 1,
    "boot/grub/i386-pc/eltorito.img": 1,
    "EFI/BOOT/BOOTX64.EFI": 100 * 1024,
    "EFI/BOOT/grubx64.efi": 100 * 1024,
    "EFI/BOOT/mmx64.efi": 1,
}
LAYOUT_FILES = {
    "casper": {"casper/filesystem.squashfs": 500 * MB, "casper/vmlinuz": 5 * MB, "casper/initrd": 10 * MB},
    "liveos": {"LiveOS/squashfs.img": 500 * MB, "live/vmlinuz": 5 * MB, "live/initrd.img": 10 * MB},
}


@dataclass(frozen=True)
class Outcome:
    check: str
    ok: bool
    detail: str


class _Collector:
    def __init__(self, check: str) -> None:
        self.check = check
        self.outcomes: list[Outcome] = []

    def expect(self, ok: bool, detail: str) -> bool:
        self.outcomes.append(Outcome(self.check, bool(ok), detail))
        return bool(ok)


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _facts(result: dict, key: str) -> list[str]:
    return list((result.get("facts") or {}).get(key) or [])


def _fact(result: dict, key: str) -> str | None:
    values = _facts(result, key)
    return values[0] if values else None


def check_meta(spec: Spec, results: Path, expect_commit: str | None) -> list[Outcome]:
    c = _Collector("meta")
    meta = _load(results / "meta.json")
    if meta is None:
        c.expect(False, "meta.json is missing or not valid JSON")
        return c.outcomes
    c.expect(meta.get("base") == spec.base, f"results are for base {meta.get('base')!r}, spec says {spec.base!r}")
    if expect_commit:
        c.expect(
            meta.get("commit") == expect_commit,
            f"results come from commit {meta.get('commit')!r}, expected {expect_commit!r}",
        )
    return c.outcomes


def check_iso(plan: Plan, results: Path) -> list[Outcome]:
    c = _Collector("iso")
    iso = _load(results / "iso.json")
    if iso is None:
        c.expect(False, "iso.json is missing or not valid JSON")
        return c.outcomes
    c.expect(iso.get("volume_id") == plan.volid, f"volume ID is {iso.get('volume_id')!r}, expected {plan.volid!r}")
    torito = iso.get("el_torito") or {}
    c.expect(torito.get("bios") is True, "El Torito catalog has a bootable BIOS entry")
    c.expect(torito.get("efi") is True, "El Torito catalog has a bootable EFI entry")
    c.expect((iso.get("mbr") or {}).get("signature") is True, "MBR boot signature is present (USB BIOS boot)")
    gpt = iso.get("gpt") or {}
    c.expect(gpt.get("present") is True and gpt.get("esp") is not None, "GPT with an EFI system partition (USB UEFI boot)")
    files = iso.get("files") or {}
    for name, minimum in {**COMMON_FILES, **LAYOUT_FILES[plan.live_layout]}.items():
        size = files.get(name)
        c.expect(size is not None and size >= minimum, f"{name} is {size} bytes, needs at least {minimum}")
    return c.outcomes


def check_boot(spec: Spec, results: Path) -> list[Outcome]:
    c = _Collector("boot")
    for mode in BOOT_MODES:
        result = _load(results / "modes" / mode / "result.json")
        if result is None:
            c.expect(False, f"{mode}: result.json is missing or not valid JSON")
            continue
        outcome = result.get("outcome")
        if not c.expect(outcome in ("booted", "session"), f"{mode}: reached userspace (outcome {outcome!r})"):
            continue
        firmware = "bios" if mode.startswith("bios") else "uefi"
        c.expect(_fact(result, "firmware") == firmware, f"{mode}: guest reports {_fact(result, 'firmware')!r} firmware, expected {firmware!r}")
        secure = "1" if mode == SECURE_BOOT_MODE else "0"
        c.expect(
            _fact(result, "secureboot") == secure,
            f"{mode}: guest reports Secure Boot {_fact(result, 'secureboot')!r}, expected {secure!r}",
        )
        c.expect(_fact(result, "os_name") == spec.name, f"{mode}: os-release NAME is {_fact(result, 'os_name')!r}")
    return c.outcomes


def wallpaper_hue(spec: Spec) -> float:
    fmt, _w, _h = image_info(spec.branding.wallpaper)
    if fmt != "png":
        raise ImageError("the screenshot check needs a PNG wallpaper to measure its colour")
    hue = dominant_hue(read_png(spec.branding.wallpaper))
    if hue is None:
        raise ImageError("the wallpaper has no dominant colour to look for")
    return hue


def _check_upstream_branding(c: _Collector, plan: Plan, result: dict) -> None:
    """Ubuntu's and Fedora's logos are their trademarks; the distro shows its own."""
    spec = plan.spec
    if plan.base.id == "fedora":
        logos = _fact(result, "fedora_logo_packages")
        c.expect(logos == "none", f"Fedora logo packages installed: {logos!r}, expected 'none'")
    if plan.base.id == "ubuntu":
        issue = _fact(result, "issue") or ""
        c.expect(
            issue.startswith(f"{spec.name} {spec.version} "),
            f"/etc/issue begins {issue!r}, expected {spec.name + ' ' + spec.version!r}",
        )
        if plan.desktop.id == "gnome":
            icon = _fact(result, "dock_show_apps_icon")
            c.expect(icon == "generic", f"dock's Show Apps icon is {icon!r}, expected GNOME's grid ('generic'), not the Ubuntu logo")
    if plan.desktop.id == "kde":
        icons = _fact(result, "kde_launcher_icon")
        c.expect(icons == LOGO_NAME, f"KDE launcher icon is {icons!r}, expected the distro logo {LOGO_NAME!r}")


def check_session(plan: Plan, results: Path) -> list[Outcome]:
    spec = plan.spec
    c = _Collector("session")
    mode_dir = results / "modes" / SESSION_MODE
    result = _load(mode_dir / "result.json")
    if result is None:
        c.expect(False, f"{SESSION_MODE}: result.json is missing or not valid JSON")
        return c.outcomes
    c.expect(result.get("outcome") == "session", f"{SESSION_MODE}: desktop session came up (outcome {result.get('outcome')!r})")
    pretty = _fact(result, "os_pretty_name") or ""
    prefix = f"{spec.name} {spec.version} (based on "
    c.expect(pretty.startswith(prefix), f"PRETTY_NAME is {pretty!r}, expected it to start with {prefix!r}")
    c.expect(_fact(result, "image_id") == spec.id, f"IMAGE_ID is {_fact(result, 'image_id')!r}, expected {spec.id!r}")
    c.expect(
        _fact(result, "image_version") == spec.version,
        f"IMAGE_VERSION is {_fact(result, 'image_version')!r}, expected {spec.version!r}",
    )
    shell = plan.desktop.shell_process
    c.expect(_fact(result, "shell") == shell, f"desktop shell is {_fact(result, 'shell')!r}, expected {shell!r}")
    c.expect(
        _fact(result, "plymouth_theme") == "linuxlive",
        f"Plymouth theme is {_fact(result, 'plymouth_theme')!r}, expected 'linuxlive'",
    )
    packed = (_fact(result, "initrd_plymouth_themes") or "").split()
    c.expect("linuxlive" in packed, f"initramfs carries the linuxlive boot splash (themes packed: {packed})")
    if plan.base.id == "ubuntu":
        # Ubuntu shows a separate text-mode splash when early graphics are missing.
        c.expect(
            _fact(result, "plymouth_text_theme") == "linuxlive-text",
            f"text-mode splash is {_fact(result, 'plymouth_text_theme')!r}, expected 'linuxlive-text'",
        )
        c.expect(
            "linuxlive-text" in packed and "ubuntu-text" not in packed,
            f"initramfs carries the linuxlive text splash and not Ubuntu's (themes packed: {packed})",
        )
    installed = set(_facts(result, "flatpak"))
    for app in plan.apps:
        c.expect(app.flatpak in installed, f"Flatpak {app.flatpak} ({app.name}) is installed")
    if plan.base.id == "fedora":
        c.expect(_fact(result, "selinux") == "Enforcing", f"SELinux is {_fact(result, 'selinux')!r}, expected 'Enforcing'")
    _check_upstream_branding(c, plan, result)

    try:
        hue = wallpaper_hue(spec)
    except (ImageError, OSError) as exc:
        c.expect(False, f"could not measure the wallpaper: {exc}")
        return c.outcomes
    best = 0.0
    measured = []
    for name in result.get("screenshots") or []:
        try:
            fraction = hue_fraction(read_png(mode_dir / name), hue)
        except (ImageError, OSError) as exc:
            c.expect(False, f"screenshot {name} is unreadable: {exc}")
            continue
        measured.append(f"{name}={fraction:.2f}")
        best = max(best, fraction)
    c.expect(
        best >= HUE_THRESHOLD,
        f"wallpaper hue {hue:.0f} deg covers {best:.0%} of the best screenshot "
        f"(needs {HUE_THRESHOLD:.0%}; measured {', '.join(measured) or 'no screenshots'})",
    )
    return c.outcomes


def verify_results(
    spec: Spec,
    results: Path,
    checks=CHECKS,
    *,
    expect_commit: str | None = None,
    catalog: Catalog | None = None,
) -> list[Outcome]:
    unknown = set(checks) - set(CHECKS)
    if unknown:
        raise ValueError(f"unknown checks: {', '.join(sorted(unknown))}")
    results = Path(results)
    plan = plan_build(spec, catalog or load_catalog())
    outcomes = check_meta(spec, results, expect_commit)
    if "iso" in checks:
        outcomes += check_iso(plan, results)
    if "boot" in checks:
        outcomes += check_boot(spec, results)
    if "session" in checks:
        outcomes += check_session(plan, results)
    return outcomes
