"""Persistence: saving files and settings back to the USB stick (Ubuntu's casper)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from linuxlive.compiler import build_context
from linuxlive.spec import SpecError, load_spec
from linuxlive.verify import CHECKS, verify_results
from tests.conftest import spec_data, write_spec
from tests.test_settings import example_results
from tests.test_verify import COMMIT, failures


def grub(ctx) -> str:
    return ctx.files()["iso/boot/grub/grub.cfg"][0].decode()


def test_persistence_saves_by_default_and_offers_a_clean_start(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(persistence=True))))
    menu = grub(ctx)
    entries = [line for line in menu.splitlines() if line.startswith("menuentry")]
    assert entries[0] == "menuentry 'Try TestOS 0.1, saving your changes on the USB stick' --id try {"
    assert entries[1] == "menuentry 'Try TestOS 0.1 without saving anything' --id try-fresh {"
    assert "linux /casper/vmlinuz boot=casper persistent quiet splash ---" in menu
    assert "linux /casper/vmlinuz boot=casper quiet splash ---" in menu  # the clean start
    # Installing never boots with persistence, so the install copies the clean image.
    assert "linux /casper/vmlinuz linuxlive.install boot=casper quiet splash ---" in menu
    assert json.loads(ctx.files()["linuxlive.json"][0])["iso"]["persistence"] is True


def test_without_persistence_the_menu_is_unchanged(workspace):
    menu = grub(build_context(load_spec(write_spec(workspace, spec_data())))[0])
    assert "persistent" not in menu and "try-fresh" not in menu and "menuentry 'Try TestOS 0.1' --id try {" in menu


def test_fedora_cannot_have_persistence_yet(workspace):
    with pytest.raises(SpecError) as err:
        load_spec(write_spec(workspace, spec_data(base="fedora", persistence=True)))
    assert any(p.startswith("persistence: only Ubuntu-based distros") for p in err.value.problems)


def test_persistence_is_data(workspace):
    spec = load_spec(write_spec(workspace, spec_data(persistence=True)))
    assert spec.persistence and not spec.runs_own_code


def test_boottest_boots_a_writable_stick(tmp_path, monkeypatch):
    import scripts.boottest as boottest

    monkeypatch.setattr(boottest, "first_existing", lambda paths: paths[0])
    monkeypatch.setattr(boottest.shutil, "copyfile", lambda src, dst: dst)
    stick = tmp_path / "stick.img"
    cmd = boottest.qemu_command(Path("x.iso"), "persist-1", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 2, False, disk=stick)
    assert f"if=none,id=stick,format=raw,file={stick}" in cmd and "usb-storage,bus=xhci.0,drive=stick,bootindex=0" in cmd
    assert not [a for a in cmd if "readonly=on" in a and "stick" in a]
    assert not [a for a in cmd if "x.iso" in a]  # the stick is the ISO, copied
    with pytest.raises(SystemExit):
        boottest.qemu_command(Path("x.iso"), "persist-2", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 2, False)


def test_persistence_is_a_check():
    assert "persistence" in CHECKS


def test_good_persistence_passes(tmp_path):
    spec, results = example_results(tmp_path / "r", "settings-ubuntu")
    outcomes = verify_results(spec, results, expect_commit=COMMIT)
    assert failures(outcomes) == []
    assert sum(o.check == "persistence" for o in outcomes) >= 9


@pytest.mark.parametrize(
    ("mode", "key", "value", "expected"),
    [
        ("persist-2", "persistence_seen", ["none"], "persist-2: the system kept what the first boot wrote"),
        ("persist-2", "persistence_home_seen", ["none"], "persist-2: the user's files were kept"),
        ("persist-1", "persistence_partition", ["none"], "persist-1: casper's writable partition is on the stick"),
        ("persist-1", "cmdline", ["boot=casper quiet splash ---"], "persist-1: the default boot entry saves changes"),
        ("persist-1", "persistence_seen", ["zzz-0"], "persist-1: a new stick has nothing saved yet"),
        ("persist-2", "boot_id", ["aaa-1"], "persist-2 is a second boot"),
    ],
)
def test_each_persistence_break_is_caught(tmp_path, mode, key, value, expected):
    spec, results = example_results(tmp_path / "r", "settings-ubuntu")
    path = results / "modes" / mode / "result.json"
    data = json.loads(path.read_text())
    data["facts"][key] = value
    path.write_text(json.dumps(data))
    assert any(expected in f for f in failures(verify_results(spec, results, expect_commit=COMMIT)))


def test_missing_persistence_boots_fail(tmp_path):
    spec, results = example_results(tmp_path / "r", "settings-ubuntu", persistence=False)
    assert any("persist-1: result.json is present" in f for f in failures(verify_results(spec, results, expect_commit=COMMIT)))
