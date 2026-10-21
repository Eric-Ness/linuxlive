"""The boot check's splash facts, from the lines it really runs.

bootcheck lists the initramfs with lsinitramfs (Ubuntu: bare paths) or lsinitrd
(Fedora: ls -l lines) and picks out the linuxlive theme's files and Plymouth's
plugins. These tests run those exact lines, taken from the script, on both
listing formats with sh.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tests.conftest import ROOT

BOOTCHECK = ROOT / "linuxlive" / "data" / "rootfs" / "common" / "usr" / "libexec" / "linuxlive" / "bootcheck"
SH = shutil.which("sh")

UBUNTU_LISTING = """usr/share/plymouth/themes/linuxlive
usr/share/plymouth/themes/linuxlive/linuxlive.plymouth
usr/share/plymouth/themes/linuxlive/linuxlive.script
usr/share/plymouth/themes/linuxlive/watermark.png
usr/share/plymouth/themes/linuxlive/dart.png
usr/share/plymouth/themes/linuxlive-text/linuxlive-text.plymouth
usr/lib/x86_64-linux-gnu/plymouth/script.so
usr/lib/x86_64-linux-gnu/plymouth/text.so
usr/lib/x86_64-linux-gnu/plymouth/renderers/drm.so
"""

FEDORA_LISTING = """drwxr-xr-x   2 root     root            0 Oct 21 12:00 usr/share/plymouth/themes/linuxlive
-rw-r--r--   1 root     root          213 Oct 21 12:00 usr/share/plymouth/themes/linuxlive/linuxlive.plymouth
-rw-r--r--   1 root     root         2324 Oct 21 12:00 usr/share/plymouth/themes/linuxlive/linuxlive.script
-rw-r--r--   1 root     root        28959 Oct 21 12:00 usr/share/plymouth/themes/linuxlive/watermark.png
-rw-r--r--   1 root     root          241 Oct 21 12:00 usr/share/plymouth/themes/linuxlive/dart.png
-rwxr-xr-x   1 root     root        97112 Oct 21 12:00 usr/lib64/plymouth/script.so
-rwxr-xr-x   1 root     root        31744 Oct 21 12:00 usr/lib64/plymouth/two-step.so
-rwxr-xr-x   1 root     root        48760 Oct 21 12:00 usr/lib64/plymouth/renderers/drm.so
"""


def bootcheck_line(name: str) -> str:
    """The assignment of ``name`` exactly as bootcheck writes it."""
    lines = [l.strip() for l in BOOTCHECK.read_text(encoding="utf-8").splitlines() if re.match(rf"\s*{name}=\$\(", l)]
    assert len(lines) == 1, lines
    return lines[0]


def run(listing: str) -> tuple[str, str]:
    script = f"""listing=$(cat)
{bootcheck_line('splash_files')}
{bootcheck_line('plugins')}
printf '%s|%s' "$splash_files" "$plugins"
"""
    out = subprocess.run([SH, "-c", script], input=listing, capture_output=True, text=True, check=True).stdout
    files, plugins = out.split("|")
    return files.split(), plugins.split()


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
@pytest.mark.parametrize("listing", [UBUNTU_LISTING, FEDORA_LISTING], ids=["lsinitramfs", "lsinitrd"])
def test_splash_files_and_plugins_are_read_from_either_listing(listing):
    files, plugins = run(listing)
    assert files == ["dart.png", "linuxlive.plymouth", "linuxlive.script", "watermark.png"]
    # Renderers are not splash plugins, and the text theme is not linuxlive's.
    assert "script" in plugins and "drm" not in plugins
    assert all("/" not in f for f in files + plugins)


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh")
def test_an_initramfs_without_the_theme_reports_nothing():
    files, plugins = run("usr/share/plymouth/themes/spinner/throbber-0001.png\nusr/lib64/plymouth/two-step.so\n")
    assert files == [] and plugins == ["two-step"]
