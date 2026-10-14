"""verify_results against synthetic CI results: one good run, then one break each."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from linuxlive.spec import load_spec
from linuxlive.verify import BOOT_MODES, SECURE_BOOT_MODE, SESSION_MODE, verify_results
from tests.conftest import EXAMPLES, PINK, solid_png

COMMIT = "0123456789abcdef0123456789abcdef01234567"
GB = 1 << 30


def iso_report(layout: str) -> dict:
    payload = {
        "casper": {"casper/filesystem.squashfs": 3 * GB, "casper/vmlinuz": 15 << 20, "casper/initrd": 80 << 20},
        "liveos": {"LiveOS/squashfs.img": 3 * GB, "live/vmlinuz": 15 << 20, "live/initrd.img": 80 << 20},
    }[layout]
    return {
        "volume_id": "TESTOS_0_1",
        "el_torito": {"present": True, "bios": True, "efi": True, "entries": []},
        "mbr": {"signature": True, "partitions": []},
        "gpt": {"present": True, "esp": {"index": 2, "first_lba": 100, "last_lba": 200}, "partitions": 2},
        "files": {
            ".disk/info": 50,
            "boot/grub/grub.cfg": 900,
            "boot/grub/i386-pc/eltorito.img": 30000,
            "EFI/BOOT/BOOTX64.EFI": 950_000,
            "EFI/BOOT/grubx64.efi": 2_500_000,
            "EFI/BOOT/mmx64.efi": 850_000,
            **payload,
        },
        "md5sum": (
            {"entries": 312, "mismatched": [], "missing": [], "unlisted": ["boot.catalog", "boot/grub/i386-pc/eltorito.img"]}
            if layout == "casper"
            else None
        ),
    }


def boot_result(mode: str, base: str, *, session: bool) -> dict:
    facts = {
        "os_name": ["TestOS"],
        "os_pretty_name": ["TestOS 0.1 (based on Ubuntu 26.04 LTS)" if base == "ubuntu" else "TestOS 0.1 (based on Fedora Linux 44 (Kinoite))"],
        "image_id": ["testos"],
        "image_version": ["0.1"],
        "firmware": ["bios" if mode.startswith("bios") else "uefi"],
        "secureboot": ["1" if mode == SECURE_BOOT_MODE else "0"],
        "plymouth_theme": ["linuxlive"],
        "plymouth_text_theme": ["linuxlive-text" if base == "ubuntu" else "text"],
        "initrd_plymouth_themes": ["linuxlive linuxlive-text spinner" if base == "ubuntu" else "details linuxlive text"],
        "selinux": ["absent" if base == "ubuntu" else "Enforcing"],
        "flatpak": ["org.mozilla.firefox", "org.videolan.VLC"],
        "issue": ["TestOS 0.1 \\n \\l" if base == "ubuntu" else "\\S"],
    }
    if base == "ubuntu":
        facts["dock_show_apps_icon"] = ["generic"]
    else:
        facts["fedora_logo_packages"] = ["none"]
    markers = ["LINUXLIVE_BOOTCHECK_START", "LINUXLIVE_BOOTED"]
    if session:
        facts["shell"] = ["gnome-shell" if base == "ubuntu" else "plasmashell"]
        facts["shell_after_settle"] = facts["shell"]
        facts["shell_watch"] = facts["shell"]
        facts["failed_units"] = ["none"]
        facts["failed_units_after_settle"] = ["none"]
        facts["update_notifiers"] = ["none"]
        facts["gnome_software_updates"] = ["false" if base == "ubuntu" else "absent"]
        facts["installer_icon"] = ["desktop"]
        if base == "ubuntu":
            facts["casper_md5check"] = ["pass"]
        if base == "fedora":  # TestOS on Fedora is KDE
            facts["kde_launcher_icon"] = ["linuxlive-logo"]
            facts["plasma_welcome_installer"] = ["none"]
        markers.append("LINUXLIVE_SESSION_READY")
    return {
        "mode": mode,
        "outcome": "session" if session else "booted",
        "markers": markers,
        "facts": facts,
        "screenshots": ["screenshot-1.png"] if session else [],
    }


FEDORA_IMAGE = "ghcr.io/eric-ness/linuxlive/testos-fedora:latest"


def make_results(root: Path, base: str = "ubuntu") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    meta = {"commit": COMMIT, "base": base, "spec": f"examples/testos-{base}.yaml"}
    if base == "fedora":
        meta["image"] = FEDORA_IMAGE
    (root / "meta.json").write_text(json.dumps(meta))
    (root / "iso.json").write_text(json.dumps(iso_report("casper" if base == "ubuntu" else "liveos")))
    for mode in BOOT_MODES:
        mode_dir = root / "modes" / mode
        mode_dir.mkdir(parents=True)
        session = mode == SESSION_MODE
        (mode_dir / "result.json").write_text(json.dumps(boot_result(mode, base, session=session)))
        if session:
            # A screenshot filled with the wallpaper colour.
            solid_png(mode_dir / "screenshot-1.png", 160, 100, PINK)
    # The installer: an unattended install, then the installed disk alone.
    live = boot_result(SESSION_MODE, base, session=True)
    installed = boot_result("uefi-sb-cd", base, session=True)
    installed["mode"] = "installed-uefi-sb"
    installed["facts"].update(boot_medium=["disk"], login_users=["tester"], session_user=["tester"],
                              flatpak=live["facts"]["flatpak"], first_login_wizard=["none"])
    if base == "ubuntu":
        installed["facts"].update(root_fstype=["ext4"], casper_installed=["no"])
    else:
        installed["facts"].update(root_fstype=["overlay"], sysroot_fstype=["xfs"], bootc_image=[FEDORA_IMAGE])
    bios = boot_result("bios-usb", base, session=False)
    bios["mode"] = "installed-bios"
    bios["facts"].update(boot_medium=["disk"])
    for name, data in [("install", {"mode": "install", "outcome": "installed", "facts": {}, "markers": ["LINUXLIVE_INSTALLED"]}),
                       ("installed-uefi-sb", installed), ("installed-bios", bios)]:
        (root / "modes" / name).mkdir(parents=True)
        (root / "modes" / name / "result.json").write_text(json.dumps(data))
    return root


def load(base: str = "ubuntu"):
    return load_spec(EXAMPLES / f"testos-{base}.yaml")


def failures(outcomes):
    return [o.detail for o in outcomes if not o.ok]


def edit_json(path: Path, change) -> None:
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_good_results_pass_every_check(tmp_path, base):
    results = make_results(tmp_path / "r", base)
    outcomes = verify_results(load(base), results, expect_commit=COMMIT)
    assert failures(outcomes) == []
    # Every boot mode, every app and the screenshot were actually looked at.
    details = " ".join(o.detail for o in outcomes)
    for mode in BOOT_MODES:
        assert f"{mode}:" in details
    assert "org.videolan.VLC" in details and "covers 100%" in details


def _set_fact(mode, key, value):
    def change(data):
        data["facts"][key] = value

    return f"modes/{mode}/result.json", change


BREAKS = {
    "the unattended install failed": (
        "modes/install/result.json",
        lambda d: d.update(outcome="install-failed", facts={"install_error": ["installing GRUB for BIOS failed"]}),
        "the unattended install finished (outcome 'install-failed'); installer: installing GRUB for BIOS failed",
    ),
    "installed system kept casper": (*_set_fact("installed-uefi-sb", "casper_installed", ["yes"]), "casper removed (casper_installed 'yes')"),
    "installed disk started the ISO instead": (*_set_fact("installed-uefi-sb", "boot_medium", ["live"]), "started from the disk, not the ISO (boot_medium 'live')"),
    "installed disk without Secure Boot": (*_set_fact("installed-uefi-sb", "secureboot", ["0"]), "installed-uefi-sb: Secure Boot on (guest reports '0')"),
    "installed apps missing": (*_set_fact("installed-uefi-sb", "flatpak", []), "the live session's apps are installed"),
    "Ubuntu's welcome wizard opens on the installed desktop": (
        *_set_fact("installed-uefi-sb", "first_login_wizard", ["gnome-initial-setup"]),
        "no first-login welcome wizard opens (running: 'gnome-initial-setup')",
    ),
    "installed disk fails under BIOS": ("modes/installed-bios/result.json", lambda d: d.update(outcome="timeout"), "the installed disk boots under BIOS (outcome 'timeout')"),
    "no installer on the live desktop": (*_set_fact(SESSION_MODE, "installer_icon", ["menu-only"]), "the live desktop shows the installer (installer_icon 'menu-only')"),
    "results from another commit": ("meta.json", lambda d: d.update(commit="f" * 40), "results come from commit"),
    "results for another base": ("meta.json", lambda d: d.update(base="fedora"), "results are for base"),
    "wrong volume id": ("iso.json", lambda d: d.update(volume_id="OTHER"), "volume ID is 'OTHER'"),
    "no EFI boot entry": ("iso.json", lambda d: d["el_torito"].update(efi=False), "bootable EFI entry"),
    "no BIOS boot entry": ("iso.json", lambda d: d["el_torito"].update(bios=False), "bootable BIOS entry"),
    "no MBR signature": ("iso.json", lambda d: d["mbr"].update(signature=False), "MBR boot signature"),
    "no EFI partition": ("iso.json", lambda d: d["gpt"].update(esp=None), "GPT with an EFI system partition"),
    "squashfs missing": ("iso.json", lambda d: d["files"].pop("casper/filesystem.squashfs"), "casper/filesystem.squashfs is None"),
    "squashfs too small": ("iso.json", lambda d: d["files"].update({"casper/filesystem.squashfs": 1000}), "casper/filesystem.squashfs is 1000 bytes"),
    "no md5sum.txt": ("iso.json", lambda d: d.update(md5sum=None), "md5sum.txt is on the ISO"),
    "md5sum.txt not reported": ("iso.json", lambda d: d.pop("md5sum"), "md5sum.txt is on the ISO"),
    "a checksum does not match": (
        "iso.json",
        lambda d: d["md5sum"].update(mismatched=["casper/filesystem.squashfs"]),
        "mismatched: ['casper/filesystem.squashfs']",
    ),
    "a listed file is missing": ("iso.json", lambda d: d["md5sum"].update(missing=["casper/initrd"]), "missing: ['casper/initrd']"),
    "an empty md5sum.txt": ("iso.json", lambda d: d["md5sum"].update(entries=0), "md5sum.txt lists 0 files"),
    "a file md5sum.txt leaves out": (
        "iso.json",
        lambda d: d["md5sum"]["unlisted"].append("casper/vmlinuz"),
        "also unlisted: ['casper/vmlinuz']",
    ),
    "a mode timed out": ("modes/bios-usb/result.json", lambda d: d.update(outcome="timeout"), "bios-usb: reached userspace"),
    "Secure Boot off in the SB mode": (*_set_fact(SECURE_BOOT_MODE, "secureboot", ["0"]), "uefi-sb-cd: guest reports Secure Boot '0'"),
    "Secure Boot claimed without SB firmware": (*_set_fact("uefi-cd", "secureboot", ["1"]), "uefi-cd: guest reports Secure Boot '1'"),
    "BIOS mode booted via UEFI": (*_set_fact("bios-cd", "firmware", ["uefi"]), "bios-cd: guest reports 'uefi' firmware"),
    "wrong OS name": (*_set_fact("uefi-cd", "os_name", ["Ubuntu"]), "uefi-cd: os-release NAME is 'Ubuntu'"),
    "no desktop session": (f"modes/{SESSION_MODE}/result.json", lambda d: d.update(outcome="booted"), "desktop session came up"),
    "wrong PRETTY_NAME": (*_set_fact(SESSION_MODE, "os_pretty_name", ["Ubuntu 26.04 LTS"]), "PRETTY_NAME is 'Ubuntu 26.04 LTS'"),
    "wrong IMAGE_ID": (*_set_fact(SESSION_MODE, "image_id", ["ubuntu"]), "IMAGE_ID is 'ubuntu'"),
    "wrong desktop shell": (*_set_fact(SESSION_MODE, "shell", ["plasmashell"]), "desktop shell is 'plasmashell'"),
    "upstream boot splash": (*_set_fact(SESSION_MODE, "plymouth_theme", ["bgrt"]), "Plymouth theme is 'bgrt'"),
    "splash configured but not in the initramfs": (
        *_set_fact(SESSION_MODE, "initrd_plymouth_themes", ["bgrt spinner ubuntu-text"]),
        "initramfs carries the linuxlive boot splash",
    ),
    "Ubuntu's text splash still selected": (
        *_set_fact(SESSION_MODE, "plymouth_text_theme", ["ubuntu-text"]),
        "text-mode splash is 'ubuntu-text'",
    ),
    "Ubuntu's text splash still packed": (
        *_set_fact(SESSION_MODE, "initrd_plymouth_themes", ["linuxlive spinner ubuntu-text"]),
        "initramfs carries the linuxlive text splash and not Ubuntu's",
    ),
    "app missing": (*_set_fact(SESSION_MODE, "flatpak", ["org.mozilla.firefox"]), "Flatpak org.videolan.VLC (VLC) is installed"),
    "shell crashed after starting": (
        f"modes/{SESSION_MODE}/result.json",
        lambda d: d["facts"].update(shell_after_settle=["none"], session_error=["cinnamon[812]: segfault at 0 ip 00007f"]),
        "desktop shell still running 30 s after login: 'none', expected 'gnome-shell'; session errors: cinnamon[812]: segfault",
    ),
    "shell crashed after the settle check": (
        f"modes/{SESSION_MODE}/result.json",
        lambda d: d["facts"].update(shell_crashed=["64"], session_error=["systemd-coredump[990]: Process 812 (cinnamon) of user 999 dumped core."]),
        "desktop shell kept running until the test ended (watched: 'gnome-shell', crashed 64 s after login); session errors: systemd-coredump",
    ),
    "shell not watched": (
        *_set_fact(SESSION_MODE, "shell_watch", []),
        "desktop shell kept running until the test ended (watched: None)",
    ),
    "Ubuntu logo on the dock": (*_set_fact(SESSION_MODE, "dock_show_apps_icon", ["ubuntu"]), "dock's Show Apps icon is 'ubuntu'"),
    "dock icon not reported": (*_set_fact(SESSION_MODE, "dock_show_apps_icon", []), "dock's Show Apps icon is None"),
    "/etc/issue names Ubuntu": (*_set_fact(SESSION_MODE, "issue", ["Ubuntu 26.04 LTS \\n \\l"]), "/etc/issue begins 'Ubuntu 26.04 LTS"),
    "no screenshots": (f"modes/{SESSION_MODE}/result.json", lambda d: d.update(screenshots=[]), "measured no screenshots"),
    "a unit failed": (
        *_set_fact(SESSION_MODE, "failed_units", ["casper-md5check.service "]),
        "no failed systemd units (failed_units: 'casper-md5check.service')",
    ),
    "a unit failed after login": (
        *_set_fact(SESSION_MODE, "failed_units_after_settle", ["plymouth-start.service"]),
        "no failed systemd units (failed_units_after_settle: 'plymouth-start.service')",
    ),
    "failed units not reported": (*_set_fact(SESSION_MODE, "failed_units", []), "no failed systemd units (failed_units: None)"),
    "an update notifier starts": (
        *_set_fact(SESSION_MODE, "update_notifiers", ["update-notifier"]),
        "update notifiers in the live session: 'update-notifier'",
    ),
    "update notifiers not reported": (*_set_fact(SESSION_MODE, "update_notifiers", []), "update notifiers in the live session: None"),
    "GNOME Software checks for updates": (
        *_set_fact(SESSION_MODE, "gnome_software_updates", ["true"]),
        "GNOME Software checks for updates: 'true'",
    ),
    "GNOME Software's setting unreadable": (
        *_set_fact(SESSION_MODE, "gnome_software_updates", ["error"]),
        "GNOME Software checks for updates: 'error'",
    ),
    "casper's medium check failed": (
        *_set_fact(SESSION_MODE, "casper_md5check", ["running", "fail"]),
        "casper's medium check: 'fail'",
    ),
    "casper's medium check service failed": (
        *_set_fact(SESSION_MODE, "casper_md5check", ["failed"]),
        "casper's medium check: 'failed'",
    ),
    "casper's medium check not reported": (*_set_fact(SESSION_MODE, "casper_md5check", []), "casper's medium check: None"),
}


@pytest.mark.parametrize("case", sorted(BREAKS))
def test_each_break_is_caught(tmp_path, case):
    rel, change, expected = BREAKS[case]
    results = make_results(tmp_path / "r")
    edit_json(results / rel, change)
    found = failures(verify_results(load(), results, expect_commit=COMMIT))
    assert any(expected in f for f in found), found


@pytest.mark.parametrize(("colour", "label"), [((40, 90, 210), "blue"), ((128, 128, 128), "grey"), ((5, 5, 5), "black")])
def test_screenshot_without_the_wallpaper_fails(tmp_path, colour, label):
    results = make_results(tmp_path / "r")
    solid_png(results / "modes" / SESSION_MODE / "screenshot-1.png", 160, 100, colour)
    found = failures(verify_results(load(), results, expect_commit=COMMIT))
    assert any("covers 0% of the best screenshot" in f for f in found), (label, found)


def test_selinux_must_be_enforcing_on_fedora(tmp_path):
    results = make_results(tmp_path / "r", "fedora")
    rel, change = _set_fact(SESSION_MODE, "selinux", ["Permissive"])
    edit_json(results / rel, change)
    found = failures(verify_results(load("fedora"), results, expect_commit=COMMIT))
    assert any("SELinux is 'Permissive'" in f for f in found), found


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("fedora_logo_packages", ["fedora-logos"], "Fedora logo packages installed: 'fedora-logos'"),
        ("fedora_logo_packages", [], "Fedora logo packages installed: None"),
        ("kde_launcher_icon", ["start-here"], "KDE launcher icon is 'start-here'"),
        ("kde_launcher_icon", ["none"], "KDE launcher icon is 'none'"),
        ("plasma_welcome_installer", ["missing:liveinst"], "Plasma Welcome offers installer 'missing:liveinst'"),
        ("plasma_welcome_installer", [], "Plasma Welcome offers installer None"),
        ("failed_units", ["mcelog.service "], "no failed systemd units (failed_units: 'mcelog.service')"),
    ],
)
def test_fedora_and_kde_branding_must_be_the_distros(tmp_path, key, value, expected):
    results = make_results(tmp_path / "r", "fedora")
    rel, change = _set_fact(SESSION_MODE, key, value)
    edit_json(results / rel, change)
    found = failures(verify_results(load("fedora"), results, expect_commit=COMMIT))
    assert any(expected in f for f in found), found


def test_checks_can_be_run_separately(tmp_path):
    results = make_results(tmp_path / "r")
    edit_json(results / f"modes/{SESSION_MODE}/result.json", lambda d: d.update(outcome="booted"))
    assert failures(verify_results(load(), results, ["iso", "boot"], expect_commit=COMMIT)) == []
    assert failures(verify_results(load(), results, ["session"], expect_commit=COMMIT))


def test_missing_results_fail_rather_than_pass(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    found = failures(verify_results(load(), empty, expect_commit=COMMIT))
    assert any("meta.json is missing" in f for f in found)
    assert any("iso.json is missing" in f for f in found)
    # Every boot mode, the session, and the install.
    assert sum("result.json is missing" in f for f in found) == len(BOOT_MODES) + 2


def test_unknown_check_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown checks: bogus"):
        verify_results(load(), make_results(tmp_path / "r"), ["iso", "bogus"])


def test_fedora_install_is_checked_against_the_published_image(tmp_path):
    results = make_results(tmp_path / "r", "fedora")
    assert failures(verify_results(load("fedora"), results, ["install"], expect_commit=COMMIT)) == []
    edit_json(results / "modes/installed-uefi-sb/result.json", lambda d: d["facts"].update(bootc_image=["ghcr.io/someone/else:latest"]))
    found = failures(verify_results(load("fedora"), results, ["install"], expect_commit=COMMIT))
    assert any("a bootc system updating from 'ghcr.io/eric-ness/linuxlive/testos-fedora:latest'" in f for f in found)


def test_fedora_install_that_kept_kde_setup_user_fails(tmp_path):
    # Round 7: KDE's own first-boot setup logged in as plasma-setup instead of the user.
    results = make_results(tmp_path / "r", "fedora")
    edit_json(results / "modes/installed-uefi-sb/result.json", lambda d: d["facts"].update(session_user=["plasma-setup"]))
    assert any("logged in as 'tester' (session user 'plasma-setup')" in f for f in failures(verify_results(load("fedora"), results, ["install"], expect_commit=COMMIT)))


def test_fedora_build_without_a_published_image_has_no_install_to_check(tmp_path):
    results = make_results(tmp_path / "r", "fedora")
    edit_json(results / "meta.json", lambda d: d.pop("image"))
    for mode in ("install", "installed-uefi-sb", "installed-bios"):
        edit_json(results / f"modes/{mode}/result.json", lambda d: d.update(outcome="timeout"))
    outcomes = verify_results(load("fedora"), results, ["install"], expect_commit=COMMIT)
    assert [o for o in outcomes if o.check == "install"] == []


def test_fedora_live_desktop_needs_the_installer(tmp_path):
    results = make_results(tmp_path / "r", "fedora")
    edit_json(results / f"modes/{SESSION_MODE}/result.json", lambda d: d["facts"].update(installer_icon=["menu-only"]))
    assert any("the live desktop shows the installer" in f for f in failures(verify_results(load("fedora"), results, ["session"], expect_commit=COMMIT)))
