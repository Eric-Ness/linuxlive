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
        if base == "fedora":  # TestOS on Fedora is KDE
            facts["kde_launcher_icon"] = ["linuxlive-logo"]
        markers.append("LINUXLIVE_SESSION_READY")
    return {
        "mode": mode,
        "outcome": "session" if session else "booted",
        "markers": markers,
        "facts": facts,
        "screenshots": ["screenshot-1.png"] if session else [],
    }


def make_results(root: Path, base: str = "ubuntu") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(json.dumps({"commit": COMMIT, "base": base, "spec": f"examples/testos-{base}.yaml"}))
    (root / "iso.json").write_text(json.dumps(iso_report("casper" if base == "ubuntu" else "liveos")))
    for mode in BOOT_MODES:
        mode_dir = root / "modes" / mode
        mode_dir.mkdir(parents=True)
        session = mode == SESSION_MODE
        (mode_dir / "result.json").write_text(json.dumps(boot_result(mode, base, session=session)))
        if session:
            # A screenshot filled with the wallpaper colour.
            solid_png(mode_dir / "screenshot-1.png", 160, 100, PINK)
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
    "results from another commit": ("meta.json", lambda d: d.update(commit="f" * 40), "results come from commit"),
    "results for another base": ("meta.json", lambda d: d.update(base="fedora"), "results are for base"),
    "wrong volume id": ("iso.json", lambda d: d.update(volume_id="OTHER"), "volume ID is 'OTHER'"),
    "no EFI boot entry": ("iso.json", lambda d: d["el_torito"].update(efi=False), "bootable EFI entry"),
    "no BIOS boot entry": ("iso.json", lambda d: d["el_torito"].update(bios=False), "bootable BIOS entry"),
    "no MBR signature": ("iso.json", lambda d: d["mbr"].update(signature=False), "MBR boot signature"),
    "no EFI partition": ("iso.json", lambda d: d["gpt"].update(esp=None), "GPT with an EFI system partition"),
    "squashfs missing": ("iso.json", lambda d: d["files"].pop("casper/filesystem.squashfs"), "casper/filesystem.squashfs is None"),
    "squashfs too small": ("iso.json", lambda d: d["files"].update({"casper/filesystem.squashfs": 1000}), "casper/filesystem.squashfs is 1000 bytes"),
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
    "Ubuntu logo on the dock": (*_set_fact(SESSION_MODE, "dock_show_apps_icon", ["ubuntu"]), "dock's Show Apps icon is 'ubuntu'"),
    "dock icon not reported": (*_set_fact(SESSION_MODE, "dock_show_apps_icon", []), "dock's Show Apps icon is None"),
    "/etc/issue names Ubuntu": (*_set_fact(SESSION_MODE, "issue", ["Ubuntu 26.04 LTS \\n \\l"]), "/etc/issue begins 'Ubuntu 26.04 LTS"),
    "no screenshots": (f"modes/{SESSION_MODE}/result.json", lambda d: d.update(screenshots=[]), "measured no screenshots"),
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
    assert sum("result.json is missing" in f for f in found) == len(BOOT_MODES) + 1


def test_unknown_check_names_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown checks: bogus"):
        verify_results(load(), make_results(tmp_path / "r"), ["iso", "bogus"])
