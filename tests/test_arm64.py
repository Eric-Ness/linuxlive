"""ARM64 ISOs, for virtual machines on Apple-silicon Macs: the spec's arches."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from linuxlive.compiler import CompileError, build_context
from linuxlive.spec import SpecError, load_spec
from linuxlive.verify import verify_results
from tests.conftest import spec_data, write_spec
from tests.test_verify import COMMIT, failures, make_results


def env(ctx, path: str) -> dict[str, str]:
    values = {}
    for line in ctx.files()[path][0].decode().splitlines():
        if line and not line.startswith("#"):
            key, _, raw = line.partition("=")
            values[key] = (shlex.split(raw) or [""])[0]
    return values


def test_arches_default_to_amd64(workspace):
    spec = load_spec(write_spec(workspace, spec_data()))
    assert spec.arches == ("amd64",)
    ctx, plan = build_context(spec)
    assert plan.arch.name == "amd64" and plan.iso_name.endswith("-ubuntu-amd64.iso")
    assert env(ctx, "iso.env")["LL_ARCH"] == "amd64" and env(ctx, "iso.env")["LL_BIOS"] == "1"


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_an_arm64_build(workspace, base):
    spec = load_spec(write_spec(workspace, spec_data(base=base, desktop="kde" if base == "fedora" else "gnome", arches=["amd64", "arm64"])))
    ctx, plan = build_context(spec, arch="arm64")
    assert plan.flatpak_arch == "aarch64" and plan.iso_name == f"testos-0.1-{base}-arm64.iso"
    iso = env(ctx, "iso.env")
    assert (iso["LL_ARCH"], iso["LL_EFI"], iso["LL_BIOS"]) == ("arm64", "aa64", "0")
    assert env(ctx, "rootfs/usr/lib/linuxlive/branding.env")["LL_ARCH"] == "arm64"
    containerfile = ctx.files()["Containerfile"][0].decode()
    for name in ("shimaa64.efi", "grubaa64.efi", "mmaa64.efi"):
        assert name in containerfile
    assert "x64.efi" not in containerfile
    if base == "ubuntu":
        assert "apt-get download shim-signed grub-efi-arm64-signed;" in containerfile
        assert "/x/usr/lib/grub/arm64-efi-signed/gcdaa64.efi.signed /out/grubaa64.efi" in containerfile
        assert "grub-efi-arm64 grub-efi-arm64-signed shim-signed efibootmgr mokutil;" in containerfile
        assert "grub-pc-bin" not in containerfile
    # The same spec still builds its PC ISO, unchanged.
    amd, amd_plan = build_context(spec, arch="amd64")
    assert "shimx64.efi" in amd.files()["Containerfile"][0].decode() and amd_plan.iso_name.endswith("-amd64.iso")


def test_an_arch_the_spec_does_not_ask_for_is_refused(workspace):
    spec = load_spec(write_spec(workspace, spec_data()))
    with pytest.raises(CompileError, match="builds for amd64, not arm64"):
        build_context(spec, arch="arm64")
    with pytest.raises(CompileError, match="no architecture 'riscv64'"):
        build_context(spec, arch="riscv64")


@pytest.mark.parametrize(("value", "message"), [(["arm"], "is not one of"), ([], "should be non-empty"), (["arm64", "arm64"], "non-unique")])
def test_bad_arches_are_refused(workspace, value, message):
    with pytest.raises(SpecError) as err:
        load_spec(write_spec(workspace, spec_data(arches=value)))
    assert any(message in p for p in err.value.problems), err.value.problems


def test_arm64_only_builds_arm64_by_default(workspace):
    _, plan = build_context(load_spec(write_spec(workspace, spec_data(arches=["arm64"]))))
    assert plan.arch.name == "arm64"


def test_the_installer_and_scripts_know_arm(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(arches=["arm64"]))))
    files = {p: d.decode() for p, (d, _m) in ctx.files().items() if p.startswith("rootfs/usr/libexec/")}
    backend = files["rootfs/usr/libexec/linuxlive/install-to-disk"]
    assert "arm64) efi_target=arm64-efi" in backend and 'if [ "$efi_target" = x86_64-efi ]; then' in backend
    for script in ("bootcheck", "autoinstall"):
        assert "aarch64) port=/dev/ttyAMA0" in files[f"rootfs/usr/libexec/linuxlive/{script}"]
    assemble = (Path(__file__).resolve().parents[1] / "scripts" / "assemble-iso.sh").read_text(encoding="utf-8")
    assert 'efi_boot=BOOT${LL_EFI^^}.EFI' in assemble and 'if [[ $LL_BIOS == 1 ]]; then' in assemble


def test_boottest_runs_arm_on_qemus_virt_machine(tmp_path, monkeypatch):
    import scripts.boottest as boottest

    monkeypatch.setattr(boottest, "first_existing", lambda paths: paths[0])
    monkeypatch.setattr(boottest.shutil, "copyfile", lambda src, dst: dst)
    cmd = boottest.qemu_command(Path("x.iso"), "uefi-cd", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 4, False, arch="arm64")
    assert cmd[0] == "qemu-system-aarch64" and cmd[cmd.index("-machine") + 1] == "virt"
    assert "tcg,thread=multi" in cmd and "max,pauth-impdef=on" in cmd
    assert "scsi-cd,bus=scsi.0,drive=boot,bootindex=0" in cmd and "virtio-gpu-pci" in cmd
    assert any("AAVMF_CODE.no-secboot.fd" in a for a in cmd)
    sb = boottest.qemu_command(Path("x.iso"), "uefi-sb-cd", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 4, True, arch="arm64")
    assert any("AAVMF_CODE.secboot.fd" in a for a in sb) and "host" in sb
    install = boottest.qemu_command(Path("x.iso"), "install", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 4, False,
                                    disk=tmp_path / "d.qcow2", arch="arm64")
    assert any(a.startswith("name=opt/linuxlive/autoinstall,") for a in install)
    with pytest.raises(SystemExit, match="no BIOS"):
        boottest.qemu_command(Path("x.iso"), "bios-cd", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 4, False, arch="arm64")


def arm_results(root: Path, base: str = "ubuntu") -> Path:
    """Good results of an arm64 build: no BIOS modes, AA64 boot files."""
    results = make_results(root, base)
    meta = json.loads((results / "meta.json").read_text())
    meta["arch"] = "arm64"
    (results / "meta.json").write_text(json.dumps(meta))
    iso = json.loads((results / "iso.json").read_text())
    for old, new in (("BOOTX64.EFI", "BOOTAA64.EFI"), ("grubx64.efi", "grubaa64.efi"), ("mmx64.efi", "mmaa64.efi")):
        iso["files"][f"EFI/BOOT/{new}"] = iso["files"].pop(f"EFI/BOOT/{old}")
    iso["files"].pop("boot/grub/i386-pc/eltorito.img")
    iso["el_torito"]["bios"] = False
    if iso.get("md5sum"):
        iso["md5sum"]["unlisted"] = ["boot.catalog"]
    (results / "iso.json").write_text(json.dumps(iso))
    for mode in ("bios-cd", "bios-usb", "installed-bios"):
        for f in (results / "modes" / mode).iterdir():
            f.unlink()
        (results / "modes" / mode).rmdir()
    return results


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_good_arm64_results_pass(workspace, tmp_path, base):
    spec = load_spec(write_spec(workspace, spec_data(base=base, desktop="kde" if base == "fedora" else "gnome", arches=["amd64", "arm64"])))
    results = arm_results(tmp_path / "r", base)
    outcomes = verify_results(spec, results, expect_commit=COMMIT)
    assert failures(outcomes) == []
    details = " ".join(o.detail for o in outcomes)
    assert "EFI/BOOT/BOOTAA64.EFI" in details and "bios-cd" not in details and "no BIOS entry in El Torito on arm64" in details


def test_an_arm64_iso_with_pc_boot_files_fails(workspace, tmp_path):
    spec = load_spec(write_spec(workspace, spec_data(arches=["arm64"])))
    results = arm_results(tmp_path / "r")
    iso = json.loads((results / "iso.json").read_text())
    iso["files"].pop("EFI/BOOT/BOOTAA64.EFI")
    (results / "iso.json").write_text(json.dumps(iso))
    assert any("EFI/BOOT/BOOTAA64.EFI is None" in f for f in failures(verify_results(spec, results, expect_commit=COMMIT)))


def test_amd64_results_still_need_bios(workspace, tmp_path):
    spec = load_spec(write_spec(workspace, spec_data(arches=["amd64", "arm64"])))
    results = make_results(tmp_path / "r")
    (results / "modes" / "bios-cd" / "result.json").unlink()
    assert any("bios-cd: result.json is missing" in f for f in failures(verify_results(spec, results, expect_commit=COMMIT)))
