"""A spec's own additions: packages, files, scripts and repos."""

from __future__ import annotations

import json

import pytest

from linuxlive.compiler import CompileError, build_context
from linuxlive.compiler.extras import script_name
from linuxlive.spec import SpecError, load_spec
from tests.conftest import EXAMPLES, spec_data, write_spec

COMMIT = "7de84ed49850a29bc2fce32d66d0d83509b99daf"
REPO = {"url": "https://github.com/Eric-Ness/linuxlive", "commit": COMMIT, "run": "make install"}


def extras_spec(workspace, base="ubuntu", **changes):
    (workspace / "extras").mkdir(exist_ok=True)
    (workspace / "extras" / "motd").write_bytes(b"hello\n")
    (workspace / "extras" / "setup.sh").write_bytes(b"#!/bin/sh\r\necho built\r\n")
    (workspace / "extras" / "boot.sh").write_bytes(b"#!/bin/sh\necho boot\n")
    (workspace / "extras" / "login.sh").write_bytes(b"#!/bin/sh\necho login\n")
    data = spec_data(
        base=base,
        desktop="gnome" if base == "ubuntu" else "kde",
        packages=["htop", "vim"],
        files=[{"src": "extras/motd", "dest": "/etc/motd"}, {"src": "extras/motd", "dest": "/usr/share/hello/motd", "mode": "0755"}],
        scripts={"build": ["extras/setup.sh"], "first-boot": ["extras/boot.sh"], "first-login": ["extras/login.sh"]},
        repos=[REPO],
    )
    data.update(changes)
    return load_spec(write_spec(workspace, data))


def problems(workspace, **changes):
    with pytest.raises(SpecError) as info:
        extras_spec(workspace, **changes)
    return " | ".join(info.value.problems)


def test_a_spec_with_every_kind_of_addition_loads(workspace):
    spec = extras_spec(workspace)
    assert spec.packages == ("htop", "vim")
    assert [f.dest for f in spec.files] == ["/etc/motd", "/usr/share/hello/motd"]
    assert [f.mode for f in spec.files] == [0o644, 0o755]
    assert spec.repos[0].slug == "Eric-Ness/linuxlive"
    assert spec.runs_own_code


def test_a_spec_with_only_packages_and_files_runs_no_code(workspace):
    (workspace / "motd").write_bytes(b"hello\n")
    spec = load_spec(write_spec(workspace, spec_data(packages=["htop"], files=[{"src": "motd", "dest": "/etc/motd"}])))
    assert spec.packages == ("htop",) and not spec.runs_own_code


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"files": [{"src": "../outside", "dest": "/etc/x"}]}, "must be inside the spec's folder"),
        ({"files": [{"src": "extras/none", "dest": "/etc/x"}]}, "file 'extras/none' not found"),
        ({"files": [{"src": "extras/motd", "dest": "/var/lib/x"}]}, "does not match"),
        ({"files": [{"src": "extras/motd", "dest": "/etc/../usr/x"}]}, "must be a plain path"),
        ({"files": [{"src": "extras/motd", "dest": "/usr/lib/linuxlive/branding.env"}]}, "where linuxlive keeps its own files"),
        ({"files": [{"src": "extras/motd", "dest": "/etc/os-release"}]}, "where linuxlive keeps its own files"),
        ({"files": [{"src": "extras/motd", "dest": "/etc/a"}, {"src": "extras/motd", "dest": "/etc/a"}]}, "is used twice"),
        ({"files": [{"src": "extras/motd", "dest": "/etc/a", "mode": "4755"}]}, "does not match"),
        ({"packages": ["-o APT::x"]}, "does not match"),
        ({"scripts": {"build": ["extras/missing.sh"]}}, "file 'extras/missing.sh' not found"),
        ({"scripts": {"every-boot": ["extras/boot.sh"]}}, "Additional properties"),
        ({"repos": [{**REPO, "commit": "main"}]}, "does not match"),
        ({"repos": [{**REPO, "url": "https://gitlab.com/a/b"}]}, "does not match"),
        ({"repos": [REPO, REPO]}, "is listed twice"),
    ],
)
def test_bad_additions_are_rejected(workspace, changes, expected):
    assert expected in problems(workspace, **changes)


def test_usr_local_is_refused_on_fedora_only(workspace):
    files = [{"src": "extras/motd", "dest": "/usr/local/share/motd"}]
    assert "not part of a Fedora Atomic image" in problems(workspace, base="fedora", files=files)
    assert extras_spec(workspace, files=files).files[0].dest == "/usr/local/share/motd"


def test_scripts_must_be_text(workspace):
    (workspace / "extras").mkdir(exist_ok=True)
    (workspace / "extras" / "bin.sh").write_bytes(b"\x7fELF\0\0")
    assert "is not a text file" in problems(workspace, scripts={"build": ["extras/bin.sh"]})


def test_files_are_limited_to_20_mb(workspace):
    (workspace / "extras").mkdir(exist_ok=True)
    (workspace / "extras" / "big").write_bytes(b"\0" * (21 << 20))
    assert "the limit is 20 MB" in problems(workspace, files=[{"src": "extras/big", "dest": "/usr/share/big"}])


def test_script_names_keep_their_order():
    from pathlib import Path

    assert script_name(0, Path("a/setup.sh")) == "10-setup.sh"
    assert script_name(1, Path("b/my script!.sh")) == "20-my-script-.sh"


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_compiled_context_places_every_addition(workspace, base):
    ctx, plan = build_context(extras_spec(workspace, base=base))
    files = ctx.files()
    assert files["rootfs/etc/motd"] == (b"hello\n", 0o644)
    assert files["rootfs/usr/share/hello/motd"][1] == 0o755
    # Scripts get LF line ends and run in their listed order.
    assert files["extras/build-scripts/10-setup.sh"] == (b"#!/bin/sh\necho built\n", 0o755)
    assert files["extras/repos/10.repo"][0] == f"{REPO['url']}\n{COMMIT}\n".encode()
    assert files["extras/repos/10.run"][0] == b"make install\n"
    assert "rootfs/usr/lib/linuxlive/first-boot.d/10-boot.sh" in files
    assert "rootfs/usr/lib/linuxlive/first-login.d/10-login.sh" in files
    assert b"first-login-scripts" in files["rootfs/etc/xdg/autostart/linuxlive-first-login.desktop"][0]
    manifest = json.loads(files["rootfs/usr/lib/linuxlive/extras.json"][0])
    assert manifest == {"packages": ["htop", "vim"], "files": ["/etc/motd", "/usr/share/hello/motd"]}
    resolved = json.loads(files["linuxlive.json"][0])["extras"]
    assert resolved["scripts"]["build"] == ["10-setup.sh"] and resolved["repos"] == [{"url": REPO["url"], "commit": COMMIT}]

    containerfile = files["Containerfile"][0].decode()
    packages = containerfile.index("htop vim")
    copy_rootfs = containerfile.index("COPY rootfs/ /")
    build = containerfile.index("user-build /tmp/linuxlive-extras")
    finalize = containerfile.index("RUN /bin/sh /usr/libexec/linuxlive/finalize")
    # Packages, then files, then the build steps, and linuxlive's finalize last.
    assert packages < copy_rootfs < build < finalize
    if base == "ubuntu":
        # Repos are fetched over HTTPS, which Ubuntu's base image has no tool for.
        assert "apt-get install -y htop vim curl ca-certificates" in containerfile


def test_specs_without_additions_compile_as_before(workspace):
    ctx, _plan = build_context(load_spec(write_spec(workspace, spec_data())))
    containerfile = ctx.files()["Containerfile"][0].decode()
    assert "user-build" not in containerfile and "own packages" not in containerfile
    assert not any(p.startswith("extras/") for p in ctx.paths())
    assert "extras" not in json.loads(ctx.files()["linuxlive.json"][0])


def test_a_file_cannot_replace_one_linuxlive_writes(workspace):
    spec = extras_spec(workspace, files=[{"src": "extras/motd", "dest": "/etc/casper.conf"}])
    with pytest.raises(CompileError, match="linuxlive writes itself"):
        build_context(spec)


def test_examples_exercise_every_kind_of_addition():
    for base in ("ubuntu", "fedora"):
        spec = load_spec(EXAMPLES / f"testos-{base}.yaml")
        s = spec.scripts
        assert spec.packages and spec.files and s.build and s.first_boot and s.first_login and spec.repos
