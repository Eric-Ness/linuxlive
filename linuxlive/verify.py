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

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from linuxlive.catalog import Catalog, load_catalog
from linuxlive.compiler.extras import file_sha256, script_names
from linuxlive.compiler.options import KIOSK_USER, firefox_policies_text
from linuxlive.compiler.plan import LOGO_NAME, Plan, plan_build
from linuxlive.png import ImageError, dominant_hue, hue_fraction, image_info, read_png, screen_variety
from linuxlive.spec import Spec

CHECKS = ("iso", "boot", "session", "install", "persistence")
BOOT_MODES = ("bios-cd", "uefi-cd", "uefi-sb-cd", "bios-usb", "uefi-usb")
SECURE_BOOT_MODE = "uefi-sb-cd"
SESSION_MODE = "uefi-usb"
HUE_THRESHOLD = 0.25
# Kiosk mode shows an app, not the wallpaper: its screenshots must not be blank
# (one colour over nearly all of the screen, or hardly any colours at all).
BLANK_SHARE = 0.97
MIN_COLOURS = 24
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
# xorriso writes the boot catalog, and patches a boot info table into the El
# Torito image, as it writes the ISO, so md5sum.txt cannot cover those two.
MD5_UNLISTED = {"boot.catalog", "boot/grub/i386-pc/eltorito.img"}


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
    if plan.live_layout == "casper":
        # casper-md5check checks the medium against md5sum.txt in every live session.
        md5 = iso.get("md5sum")
        c.expect(isinstance(md5, dict), "md5sum.txt is on the ISO for casper's medium check")
        if isinstance(md5, dict):
            c.expect(
                md5.get("entries", 0) > 0 and not md5.get("mismatched") and not md5.get("missing"),
                f"md5sum.txt lists {md5.get('entries')} files and matches them "
                f"(mismatched: {md5.get('mismatched')}, missing: {md5.get('missing')})",
            )
            extra = sorted(set(md5.get("unlisted") or []) - MD5_UNLISTED)
            c.expect(not extra, f"md5sum.txt covers every file but the boot catalog and El Torito image (also unlisted: {extra})")
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
    if plan.desktop.id == "kde" and plan.kiosk_app is None:
        icons = _fact(result, "kde_launcher_icon")
        c.expect(icons == LOGO_NAME, f"KDE launcher icon is {icons!r}, expected the distro logo {LOGO_NAME!r}")


def _check_live_session(c: _Collector, plan: Plan, result: dict) -> None:
    """Nothing failed, nothing nags about updates, nothing offers a missing installer."""
    for key in ("failed_units", "failed_units_after_settle"):
        units = _fact(result, key)
        units = units.strip() if units is not None else None
        c.expect(units in ("none", ""), f"no failed systemd units ({key}: {units!r})")
    notifiers = _fact(result, "update_notifiers")
    c.expect(notifiers == "none", f"update notifiers in the live session: {notifiers!r}, expected 'none'")
    software = _fact(result, "gnome_software_updates")
    c.expect(
        software in ("false", "absent"),
        f"GNOME Software checks for updates: {software!r}, expected 'false' (or 'absent' without GNOME Software)",
    )
    if plan.desktop.id == "kde" and plan.kiosk_app is None:
        installer = _fact(result, "plasma_welcome_installer")
        c.expect(
            installer is not None and not installer.startswith("missing:"),
            f"Plasma Welcome offers installer {installer!r}, which must exist or be 'none'",
        )
    if plan.base.id == "ubuntu":
        # The last report wins; "running" means it had not finished when the VM stopped.
        verdicts = _facts(result, "casper_md5check")
        verdict = verdicts[-1] if verdicts else None
        c.expect(verdict in ("pass", "running"), f"casper's medium check: {verdict!r}, expected 'pass' (or still 'running')")


def _check_extras(c: _Collector, plan: Plan, result: dict, where: str, *, installed: bool) -> None:
    """The spec's own packages, files and scripts (bootcheck's extra_* and *_script facts)."""
    spec = plan.spec
    packages = sorted(_facts(result, "extra_package"))
    for name in spec.packages:
        c.expect(f"{name}=yes" in packages, f"{where}package {name} is installed (found {packages})")
    files = set(_facts(result, "extra_file"))
    for f in spec.files:
        c.expect(f"{f.dest}={file_sha256(f.src)}" in files, f"{where}{f.dest} holds the spec's {f.src.name}")
    built = set(_facts(result, "user_build"))
    for repo in spec.repos:
        c.expect(f"repo {repo.url} {repo.commit} ok" in built, f"{where}repo {repo.slug} was built in at {repo.commit[:12]}")
    for name in script_names(spec.scripts.build):
        c.expect(f"build {name} ok" in built, f"{where}build script {name} ran")
    logins = sorted(_facts(result, "first_login_script"))
    for name in script_names(spec.scripts.first_login):
        c.expect(f"{name}=0" in logins, f"{where}first-login script {name} ran and succeeded (found {logins})")
    if installed:
        boots = sorted(_facts(result, "first_boot_script"))
        for name in script_names(spec.scripts.first_boot):
            c.expect(f"{name}=0" in boots, f"{where}first-boot script {name} ran and succeeded (found {boots})")


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


def _check_settings(c: _Collector, plan: Plan, result: dict, where: str, *, live: bool) -> None:
    """The spec's settings, firefox and kiosk, as the running system has them."""
    spec = plan.spec
    s = spec.settings
    kiosk = plan.kiosk_app is not None
    if live:
        # An installed system has the name its installer was given.
        c.expect(_fact(result, "hostname") == plan.hostname, f"{where}computer name is {_fact(result, 'hostname')!r}, expected {plan.hostname!r}")
    if s.language:
        want = f"{s.language}.UTF-8"
        c.expect(_fact(result, "system_locale") == want, f"{where}system locale is {_fact(result, 'system_locale')!r}, expected {want!r}")
        lang = _fact(result, "session_lang") or ""
        c.expect(lang.lower().replace("utf8", "utf-8") == want.lower(), f"{where}the session runs in {lang!r}, expected {want!r}")
        if not kiosk:
            translation = _fact(result, "shell_translation") or ""
            c.expect(translation.startswith("/"), f"{where}the desktop shell has its words in {s.language} (catalog: {translation!r})")
    if s.timezone:
        c.expect(_fact(result, "timezone") == s.timezone, f"{where}time zone is {_fact(result, 'timezone')!r}, expected {s.timezone!r}")
    if s.keyboard:
        c.expect(_fact(result, "keyboard_system") == s.keyboard, f"{where}system keyboard layout is {_fact(result, 'keyboard_system')!r}, expected {s.keyboard!r}")
        c.expect(_fact(result, "keyboard_session") == s.keyboard, f"{where}the session types with layout {_fact(result, 'keyboard_session')!r}, expected {s.keyboard!r}")
    states = dict(f.split("=", 1) for f in _facts(result, "hidden_app") if "=" in f)
    for name in s.hidden_apps:
        c.expect(states.get(name) in ("hidden", "absent"), f"{where}menu entry {name} is not shown ({states.get(name)!r})")
    policies = firefox_policies_text(plan)
    if policies is not None:
        digest = hashlib.sha256(policies.encode("utf-8")).hexdigest()
        c.expect(_fact(result, "firefox_policies") == digest, f"{where}Firefox's system policies are the spec's (found {_fact(result, 'firefox_policies')!r})")
    if kiosk:
        c.expect(_fact(result, "desktop_shells") == "none", f"{where}kiosk mode runs no desktop shell (running: {_fact(result, 'desktop_shells')!r})")
        apps = (_fact(result, "kiosk_apps") or "").split()
        c.expect(plan.kiosk_app.flatpak in apps, f"{where}kiosk mode shows {plan.kiosk_app.flatpak} (running apps: {apps})")
        if plan.kiosk_app.id == "firefox" and spec.firefox is not None:
            opened = _facts(result, "firefox_open")
            if spec.firefox.homepage:
                c.expect(any(_same_url(u, spec.firefox.homepage) for u in opened),
                         f"{where}Firefox opened the home page {spec.firefox.homepage} (open: {opened})")
            marks = _facts(result, "firefox_bookmark")
            for b in spec.firefox.bookmarks:
                c.expect(any(_same_url(u, b.url) for u in marks), f"{where}Firefox has the bookmark {b.name} ({b.url}; found {marks})")


def _check_flatpak_runs(c: _Collector, plan: Plan, result: dict, where: str) -> None:
    """A Flatpak app starts as the session's user (its sandbox works). Kiosk
    mode checks its own app instead."""
    if not plan.apps or plan.kiosk_app is not None:
        return
    runs = _fact(result, "flatpak_runs") or ""
    c.expect(runs.endswith("=ok"), f"{where}a Flatpak app starts in the session ({runs!r})")


def _check_not_blank(c: _Collector, mode_dir: Path, result: dict) -> None:
    best = None
    measured = []
    for name in result.get("screenshots") or []:
        try:
            share, colours = screen_variety(read_png(mode_dir / name))
        except (ImageError, OSError) as exc:
            c.expect(False, f"screenshot {name} is unreadable: {exc}")
            continue
        measured.append(f"{name}: {share:.0%} one colour, {colours} colours")
        if share < BLANK_SHARE and colours >= MIN_COLOURS:
            best = name
    c.expect(best is not None, f"the kiosk's screen shows something (needs under {BLANK_SHARE:.0%} one colour and {MIN_COLOURS}+ colours; "
             f"measured {', '.join(measured) or 'no screenshots'})")


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
    # CI names each build (build.yml's build_id); a build without one has none to check.
    build_id = (_load(results / "meta.json") or {}).get("build_id")
    if build_id:
        c.expect(_fact(result, "build_id") == build_id, f"BUILD_ID is {_fact(result, 'build_id')!r}, expected this build's {build_id!r}")
    kiosk = plan.kiosk_app is not None
    # Kiosk mode: Cage, not a desktop; the installer is in the boot menu only.
    shell = "cage" if kiosk else plan.desktop.shell_process
    c.expect(_fact(result, "shell") == shell, f"desktop shell is {_fact(result, 'shell')!r}, expected {shell!r}")
    if kiosk:
        c.expect(_fact(result, "session_user") == KIOSK_USER, f"kiosk mode runs as {KIOSK_USER} (session user {_fact(result, 'session_user')!r})")
    else:
        c.expect(_fact(result, "installer_icon") == "desktop", f"the live desktop shows the installer (installer_icon {_fact(result, 'installer_icon')!r})")
        # The installer's window lists disks as the live user, through sudo -n; the
        # boot medium itself is always among them.
        listed = _fact(result, "installer_disks") or ""
        c.expect(listed.isdigit() and int(listed) >= 1, f"the installer's window can list the disks as the live user (installer_disks {listed!r})")
    c.expect(_fact(result, "first_login_wizard") == "none", f"no upstream welcome wizard opens in the live session (running: {_fact(result, 'first_login_wizard')!r})")
    after = _fact(result, "shell_after_settle")
    errors = _facts(result, "session_error")
    c.expect(
        after == shell,
        f"desktop shell still running 30 s after login: {after!r}, expected {shell!r}"
        + (f"; session errors: {' | '.join(errors)}" if errors else ""),
    )
    # bootcheck goes on watching the shell until the VM is stopped after the
    # last screenshot, and says so if it dies (or Cinnamon falls back) later.
    crashed = _fact(result, "shell_crashed")
    c.expect(
        _fact(result, "shell_watch") == shell and crashed is None,
        f"desktop shell kept running until the test ended (watched: {_fact(result, 'shell_watch')!r}"
        + (f", crashed {crashed} s after login" if crashed is not None else "")
        + ")"
        + (f"; session errors: {' | '.join(errors)}" if errors and crashed is not None else ""),
    )
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
    _check_extras(c, plan, result, "", installed=False)
    _check_settings(c, plan, result, "", live=True)
    _check_flatpak_runs(c, plan, result, "")
    if plan.base.id == "fedora":
        c.expect(_fact(result, "selinux") == "Enforcing", f"SELinux is {_fact(result, 'selinux')!r}, expected 'Enforcing'")
    _check_upstream_branding(c, plan, result)
    _check_live_session(c, plan, result)
    if kiosk:
        _check_not_blank(c, mode_dir, result)
        return c.outcomes

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


INSTALL_MODES = ("install", "installed-uefi-sb", "installed-bios")
TEST_USER = "tester"  # what scripts/boottest.py's unattended install creates


def check_install(plan: Plan, results: Path) -> list[Outcome]:
    """The installer: the unattended install onto a blank disk, then that disk
    booted alone under UEFI with Secure Boot and under BIOS. Fedora installs
    from the image the build published (meta.json's image), so a Fedora build
    that published none has no install to check."""
    c = _Collector("install")
    fedora = plan.base.id == "fedora"
    image = (_load(results / "meta.json") or {}).get("image")
    if fedora and not image:
        return c.outcomes
    modes = results / "modes"
    install = _load(modes / "install" / "result.json")
    if install is None:
        c.expect(False, "install: result.json is missing or not valid JSON")
        return c.outcomes
    error = _fact(install, "install_error")
    if not c.expect(install.get("outcome") == "installed",
                    f"install: the unattended install finished (outcome {install.get('outcome')!r})" + (f"; installer: {error}" if error else "")):
        return c.outcomes
    live = _load(modes / SESSION_MODE / "result.json") or {}
    uefi = _load(modes / "installed-uefi-sb" / "result.json")
    if uefi is None:
        c.expect(False, "installed-uefi-sb: result.json is missing or not valid JSON")
    else:
        c.expect(uefi.get("outcome") == "session", f"installed-uefi-sb: the installed disk boots to the desktop (outcome {uefi.get('outcome')!r})")
        c.expect(_fact(uefi, "boot_medium") == "disk", f"installed-uefi-sb: started from the disk, not the ISO (boot_medium {_fact(uefi, 'boot_medium')!r})")
        c.expect(_fact(uefi, "secureboot") == "1", f"installed-uefi-sb: Secure Boot on (guest reports {_fact(uefi, 'secureboot')!r})")
        if fedora:
            # composefs: an overlay at /, on the xfs bootc made at /sysroot.
            c.expect(_fact(uefi, "root_fstype") == "overlay" and _fact(uefi, "sysroot_fstype") == "xfs",
                     f"installed-uefi-sb: composefs root on xfs (/ is {_fact(uefi, 'root_fstype')!r}, /sysroot is {_fact(uefi, 'sysroot_fstype')!r})")
        else:
            c.expect(_fact(uefi, "root_fstype") == "ext4", f"installed-uefi-sb: root file system is {_fact(uefi, 'root_fstype')!r}, expected 'ext4'")
        if fedora:
            c.expect(_fact(uefi, "bootc_image") == image, f"installed-uefi-sb: a bootc system updating from {image!r} (bootc status: {_fact(uefi, 'bootc_image')!r})")
        else:
            c.expect(_fact(uefi, "casper_installed") == "no", f"installed-uefi-sb: casper removed (casper_installed {_fact(uefi, 'casper_installed')!r})")
        c.expect(_fact(uefi, "login_users") == TEST_USER, f"installed-uefi-sb: the only login user is {TEST_USER!r} (found {_fact(uefi, 'login_users')!r})")
        session_user = KIOSK_USER if plan.kiosk_app else TEST_USER
        c.expect(_fact(uefi, "session_user") == session_user, f"installed-uefi-sb: logged in as {session_user!r} (session user {_fact(uefi, 'session_user')!r})")
        if plan.spec.settings.autologin:
            # The install left automatic login to the spec (boottest.py --autoinstall-autologin spec).
            c.expect(_fact(uefi, "autologin_users") == TEST_USER,
                     f"installed-uefi-sb: the spec's automatic login is on for {TEST_USER!r} (login screen logs in: {_fact(uefi, 'autologin_users')!r})")
        c.expect(_fact(uefi, "os_name") == plan.spec.name, f"installed-uefi-sb: os-release NAME is {_fact(uefi, 'os_name')!r}")
        c.expect(_fact(uefi, "first_login_wizard") == "none", f"installed-uefi-sb: no first-login welcome wizard opens (running: {_fact(uefi, 'first_login_wizard')!r})")
        c.expect(_fact(uefi, "installer_icon") != "desktop", f"installed-uefi-sb: no installer on the installed desktop (installer_icon {_fact(uefi, 'installer_icon')!r})")
        apps = sorted(_facts(live, "flatpak"))
        c.expect(sorted(_facts(uefi, "flatpak")) == apps, f"installed-uefi-sb: the live session's apps are installed ({sorted(_facts(uefi, 'flatpak'))} vs {apps})")
        _check_extras(c, plan, uefi, "installed-uefi-sb: ", installed=True)
        _check_settings(c, plan, uefi, "installed-uefi-sb: ", live=False)
        _check_flatpak_runs(c, plan, uefi, "installed-uefi-sb: ")
    bios = _load(modes / "installed-bios" / "result.json")
    if bios is None:
        c.expect(False, "installed-bios: result.json is missing or not valid JSON")
    else:
        c.expect(bios.get("outcome") in ("booted", "session"), f"installed-bios: the installed disk boots under BIOS (outcome {bios.get('outcome')!r})")
        c.expect(_fact(bios, "firmware") == "bios" and _fact(bios, "boot_medium") == "disk",
                 f"installed-bios: BIOS firmware, started from the disk (firmware {_fact(bios, 'firmware')!r}, boot_medium {_fact(bios, 'boot_medium')!r})")
    return c.outcomes


PERSIST_MODES = ("persist-1", "persist-2")


def check_persistence(plan: Plan, results: Path) -> list[Outcome]:
    """Persistence: one stick booted twice. The first boot saves to the
    partition casper made; the second finds the system's and the user's
    markers from the first. A spec without persistence has nothing to check."""
    c = _Collector("persistence")
    if not plan.spec.persistence:
        return c.outcomes
    first = _load(results / "modes" / "persist-1" / "result.json")
    second = _load(results / "modes" / "persist-2" / "result.json")
    for mode, result in zip(PERSIST_MODES, (first, second)):
        if not c.expect(result is not None, f"{mode}: result.json is present"):
            return c.outcomes
        c.expect(result.get("outcome") == "session", f"{mode}: the live desktop came up from the stick (outcome {result.get('outcome')!r})")
        cmdline = (_fact(result, "cmdline") or "").split()
        c.expect("persistent" in cmdline, f"{mode}: the default boot entry saves changes (cmdline {' '.join(cmdline)!r})")
        partition = _fact(result, "persistence_partition") or "none"
        c.expect(partition != "none", f"{mode}: casper's writable partition is on the stick ({partition!r})")
    c.expect(_fact(first, "persistence_seen") == "none", f"persist-1: a new stick has nothing saved yet ({_fact(first, 'persistence_seen')!r})")
    boot_one = _fact(first, "boot_id")
    c.expect(bool(boot_one) and _fact(second, "persistence_seen") == boot_one,
             f"persist-2: the system kept what the first boot wrote (saw {_fact(second, 'persistence_seen')!r}, first boot {boot_one!r})")
    c.expect(bool(boot_one) and _fact(second, "persistence_home_seen") == boot_one,
             f"persist-2: the user's files were kept (saw {_fact(second, 'persistence_home_seen')!r}, first boot {boot_one!r})")
    c.expect(_fact(second, "boot_id") != boot_one, "persist-2 is a second boot, not the first one again")
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
    if "install" in checks:
        outcomes += check_install(plan, results)
    if "persistence" in checks:
        outcomes += check_persistence(plan, results)
    return outcomes
