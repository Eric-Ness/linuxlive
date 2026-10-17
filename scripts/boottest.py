#!/usr/bin/env python3
"""Boot a linuxlive ISO in QEMU and record what the guest reports.

Images built by linuxlive ship linuxlive-bootcheck, which prints LINUXLIVE_*
lines to the first serial port when QEMU passes
-fw_cfg name=opt/linuxlive/bootcheck. This script boots the ISO in one
firmware and media mode, watches the serial log for those lines, optionally
waits for the desktop session and takes screenshots, and writes result.json
for `python -m linuxlive verify-results`.

    scripts/boottest.py --iso X.iso --mode uefi-usb --session --out results/modes/uefi-usb

Modes: bios-cd, uefi-cd, uefi-sb-cd (Secure Boot), bios-usb, uefi-usb; the
installer's install, installed-uefi-sb and installed-bios; and persist-1 and
persist-2, two boots of one writable stick image.
Needs qemu-system-x86_64 and OVMF (--arch arm64: qemu-system-aarch64 and
AAVMF, on QEMU's virt machine, which has no BIOS). Uses KVM when /dev/kvm is
usable; GitHub's arm64 runners have none, so ARM boots are emulated (TCG).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.png import read_ppm, write_png  # noqa: E402

MODES = {
    "bios-cd": ("bios", "cd"),
    "uefi-cd": ("uefi", "cd"),
    "uefi-sb-cd": ("uefi-sb", "cd"),
    "bios-usb": ("bios", "usb"),
    "uefi-usb": ("uefi", "usb"),
    # The installer (Ubuntu): boot the ISO from USB with a blank disk attached
    # and install onto it unattended, then boot that disk alone.
    "install": ("uefi-sb", "usb+disk"),
    "installed-uefi-sb": ("uefi-sb", "disk"),
    "installed-bios": ("bios", "disk"),
    # Persistence (Ubuntu): a USB stick with room after the ISO (--disk, a raw
    # copy of the ISO made bigger), booted twice; the first boot makes the
    # partition changes are saved to, the second must find them.
    "persist-1": ("uefi", "usb-rw"),
    "persist-2": ("uefi", "usb-rw"),
}
# What the unattended install (autoinstall, in the image) is told: the blank
# disk is the VM's first virtio disk. No time zone: the install keeps the live
# system's, which is the spec's settings.timezone when it has one. Separated by ";" because QEMU's -fw_cfg
# string may not contain commas.
AUTOINSTALL = "disk=/dev/vda;user=tester;password=linuxlive-test;hostname=linuxlive-test"
OVMF = {
    "uefi": (
        ["/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/edk2/ovmf/OVMF_CODE.fd"],
        ["/usr/share/OVMF/OVMF_VARS_4M.fd", "/usr/share/OVMF/OVMF_VARS.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"],
    ),
    "uefi-sb": (
        ["/usr/share/OVMF/OVMF_CODE_4M.secboot.fd", "/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd"],
        ["/usr/share/OVMF/OVMF_VARS_4M.ms.fd", "/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd"],
    ),
}
# ARM64's UEFI firmware (AAVMF, Debian's qemu-efi-aarch64), with Microsoft's keys for Secure Boot.
AAVMF = {
    "uefi": (
        ["/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd", "/usr/share/AAVMF/AAVMF_CODE.fd", "/usr/share/edk2/aarch64/QEMU_EFI-pflash.raw"],
        ["/usr/share/AAVMF/AAVMF_VARS.fd", "/usr/share/edk2/aarch64/vars-template-pflash.raw"],
    ),
    "uefi-sb": (
        ["/usr/share/AAVMF/AAVMF_CODE.secboot.fd", "/usr/share/AAVMF/AAVMF_CODE.ms.fd"],
        ["/usr/share/AAVMF/AAVMF_VARS.ms.fd"],
    ),
}
PREFIX = "LINUXLIVE_"


def first_existing(paths: list[str]) -> str:
    for p in paths:
        if os.path.exists(p):
            return p
    raise SystemExit(f"none of these firmware files exist: {', '.join(paths)} (install ovmf)")


def kvm_usable() -> bool:
    return os.path.exists("/dev/kvm") and os.access("/dev/kvm", os.R_OK | os.W_OK)


def guest_size(memory: int | None, cpus: int | None, kvm: bool) -> tuple[int, int]:
    """The guest's RAM (MiB) and CPUs, when not given: 4 GB and 2 CPUs under KVM.
    Emulated (TCG, multi-threaded), up to 4 of the host's CPUs and 8 GB: every
    page read back from the squashfs is decompressed by emulated code, which
    the kernel counts as memory stall: with 4 GB, systemd-oomd's pressure limit
    can end the user's whole session while apps start."""
    if kvm:
        return memory or 4096, cpus or 2
    return memory or 8192, cpus or max(2, min(4, os.cpu_count() or 2))


def qemu_command(iso: Path, mode: str, workdir: Path, serial: Path, qmp: Path, memory: int, cpus: int, kvm: bool,
                 disk: Path | None = None, autoinstall_image: str = "", autoinstall_autologin: str = "on", arch: str = "amd64") -> list[str]:
    firmware, media = MODES[mode]
    if arch == "arm64":
        return arm_command(iso, mode, workdir, serial, qmp, memory, cpus, kvm, disk, autoinstall_image, autoinstall_autologin)
    machine = "q35,smm=on" if firmware == "uefi-sb" else "q35"
    cmd = [
        "qemu-system-x86_64",
        "-machine", machine,
        "-m", str(memory),
        "-smp", str(cpus),
        "-display", "none",
        "-vga", "std",
        "-serial", f"file:{serial}",
        "-qmp", f"unix:{qmp},server=on,wait=off",
        "-fw_cfg", "name=opt/linuxlive/bootcheck,string=1",
        "-nic", "user,model=virtio-net-pci",
        "-no-reboot",
    ]
    cmd += ["-accel", "kvm", "-cpu", "host"] if kvm else ["-accel", "tcg", "-cpu", "max"]
    if firmware in OVMF:
        code_paths, vars_paths = OVMF[firmware]
        code = first_existing(code_paths)
        vars_copy = workdir / "OVMF_VARS.fd"
        shutil.copyfile(first_existing(vars_paths), vars_copy)
        if firmware == "uefi-sb":
            cmd += ["-global", "driver=cfi.pflash01,property=secure,value=on"]
        cmd += [
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
            "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
        ]
    if "disk" in media:
        if disk is None:
            raise SystemExit(f"--mode {mode} needs --disk")
        # The ISO (when attached) boots first; the installed disk boots alone.
        cmd += [
            "-drive", f"if=none,id=hd,format=qcow2,file={disk}",
            "-device", f"virtio-blk-pci,drive=hd,bootindex={1 if media == 'usb+disk' else 0}",
        ]
    if mode == "install":
        # Fedora installs from a registry image: the run's own, given here.
        settings = AUTOINSTALL + (f";image={autoinstall_image}" if autoinstall_image else "")
        # "spec": the installer's own default (the spec's settings.autologin) decides.
        if autoinstall_autologin == "spec":
            settings += ";autologin=spec"
        if "," in settings:
            raise SystemExit("autoinstall settings may not contain commas (QEMU's -fw_cfg string)")
        cmd += ["-fw_cfg", f"name=opt/linuxlive/autoinstall,string={settings}"]
    if media == "disk":
        return cmd
    if media == "usb-rw":
        if disk is None:
            raise SystemExit(f"--mode {mode} needs --disk (a raw stick image)")
        return cmd + [
            "-drive", f"if=none,id=stick,format=raw,file={disk}",
            "-device", "qemu-xhci,id=xhci",
            "-device", "usb-storage,bus=xhci.0,drive=stick,bootindex=0",
        ]
    if media == "cd":
        cmd += [
            "-drive", f"if=none,id=boot,media=cdrom,readonly=on,file={iso}",
            "-device", "ide-cd,drive=boot,bus=ide.0,bootindex=0",
        ]
    else:
        cmd += [
            "-drive", f"if=none,id=boot,format=raw,readonly=on,file={iso}",
            "-device", "qemu-xhci,id=xhci",
            "-device", "usb-storage,bus=xhci.0,drive=boot,bootindex=0",
        ]
    return cmd


def _autoinstall(mode: str, autoinstall_image: str, autoinstall_autologin: str) -> list[str]:
    if mode != "install":
        return []
    settings = AUTOINSTALL + (f";image={autoinstall_image}" if autoinstall_image else "")
    if autoinstall_autologin == "spec":
        settings += ";autologin=spec"
    if "," in settings:
        raise SystemExit("autoinstall settings may not contain commas (QEMU's -fw_cfg string)")
    return ["-fw_cfg", f"name=opt/linuxlive/autoinstall,string={settings}"]


def arm_command(iso: Path, mode: str, workdir: Path, serial: Path, qmp: Path, memory: int, cpus: int, kvm: bool,
                disk: Path | None, autoinstall_image: str, autoinstall_autologin: str) -> list[str]:
    """QEMU's virt machine for ARM64 ISOs: UEFI only, a virtio GPU for the
    desktop and its screenshots, USB keyboard and tablet, CDs on virtio-scsi."""
    firmware, media = MODES[mode]
    if firmware == "bios":
        raise SystemExit(f"--mode {mode}: ARM computers have no BIOS")
    code_paths, vars_paths = AAVMF[firmware]
    vars_copy = workdir / "AAVMF_VARS.fd"
    shutil.copyfile(first_existing(vars_paths), vars_copy)
    cmd = [
        "qemu-system-aarch64",
        "-machine", "virt",
        "-m", str(memory),
        "-smp", str(cpus),
        "-display", "none",
        "-device", "virtio-gpu-pci",
        "-device", "qemu-xhci,id=xhci",
        "-device", "usb-kbd,bus=xhci.0",
        "-device", "usb-tablet,bus=xhci.0",
        "-serial", f"file:{serial}",
        "-qmp", f"unix:{qmp},server=on,wait=off",
        "-fw_cfg", "name=opt/linuxlive/bootcheck,string=1",
        # No option ROM: the firmware needs none, and the runner's QEMU may lack it.
        "-netdev", "user,id=net0",
        "-device", "virtio-net-pci,netdev=net0,romfile=",
        "-no-reboot",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={first_existing(code_paths)}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_copy}",
    ]
    # Pointer authentication is slow to emulate exactly; QEMU's own variant is much faster.
    cmd += ["-accel", "kvm", "-cpu", "host"] if kvm else ["-accel", "tcg,thread=multi", "-cpu", "max,pauth-impdef=on"]
    if "disk" in media:
        if disk is None:
            raise SystemExit(f"--mode {mode} needs --disk")
        cmd += ["-drive", f"if=none,id=hd,format=qcow2,file={disk}",
                "-device", f"virtio-blk-pci,drive=hd,bootindex={1 if media == 'usb+disk' else 0}"]
    cmd += _autoinstall(mode, autoinstall_image, autoinstall_autologin)
    if media == "disk":
        return cmd
    if media == "usb-rw":
        if disk is None:
            raise SystemExit(f"--mode {mode} needs --disk (a raw stick image)")
        return cmd + ["-drive", f"if=none,id=stick,format=raw,file={disk}", "-device", "usb-storage,bus=xhci.0,drive=stick,bootindex=0"]
    if media == "cd":
        return cmd + ["-drive", f"if=none,id=boot,media=cdrom,readonly=on,file={iso}",
                      "-device", "virtio-scsi-pci,id=scsi", "-device", "scsi-cd,bus=scsi.0,drive=boot,bootindex=0"]
    return cmd + ["-drive", f"if=none,id=boot,format=raw,readonly=on,file={iso}", "-device", "usb-storage,bus=xhci.0,drive=boot,bootindex=0"]


class Qmp:
    """Minimal QMP client: enough for screendump and quit."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(str(path))
                break
            except OSError:
                self.sock.close()
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self.sock.settimeout(timeout)
        self.buffer = b""
        self._read()  # greeting
        self.command("qmp_capabilities")

    def _read(self) -> dict:
        while b"\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("QMP connection closed")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def command(self, name: str, **arguments) -> dict:
        message: dict = {"execute": name}
        if arguments:
            message["arguments"] = arguments
        self.sock.sendall(json.dumps(message).encode() + b"\n")
        while True:
            reply = self._read()
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                raise RuntimeError(f"QMP {name} failed: {reply['error']}")
            # anything else is an asynchronous event; skip it

    def close(self) -> None:
        self.sock.close()


class SerialLog:
    """Incrementally parses LINUXLIVE_* lines from the serial log."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.partial = b""
        self.markers: list[str] = []
        self.facts: dict[str, list[str]] = {}

    def poll(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
        self.offset += len(data)
        lines = (self.partial + data).split(b"\n")
        self.partial = lines.pop()
        for raw in lines:
            line = raw.decode("utf-8", errors="replace").strip("\r\x00 ")
            start = line.find(PREFIX)
            if start < 0:
                continue
            line = line[start:]
            if line.startswith("LINUXLIVE_FACT "):
                key, _, value = line[len("LINUXLIVE_FACT ") :].partition("=")
                self.facts.setdefault(key.strip(), []).append(value)
            else:
                self.markers.append(line.split()[0])


class Presence:
    """Moves the pointer a little every 30 seconds, as a user at the screen
    would. Emulated, the desktop takes so long to come up that its idle timers
    (KDE locks the screen after five minutes, then turns it off) run out
    before the screenshots; under KVM it does nothing."""

    def __init__(self, qmp: Qmp, active: bool) -> None:
        self.qmp = qmp
        self.active = active
        self.last = 0.0
        self.step = 0

    def tick(self) -> None:
        if not self.active or time.monotonic() - self.last < 30:
            return
        self.last = time.monotonic()
        self.step ^= 1
        # Middle of the screen (the tablet's range is 0-32767), a few pixels apart.
        x = 16384 + 40 * self.step
        try:
            self.qmp.command("input-send-event", events=[
                {"type": "abs", "data": {"axis": "x", "value": x}},
                {"type": "abs", "data": {"axis": "y", "value": 16384}},
            ])
        except Exception as exc:  # a missed nudge must not hide the boot result
            print(f"[boottest] pointer nudge failed: {exc}", flush=True)

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            self.tick()
            time.sleep(min(left, 5))


def screenshot(qmp: Qmp, workdir: Path, out: Path) -> bool:
    ppm = workdir / "screen.ppm"
    try:
        qmp.command("screendump", filename=str(ppm))
        write_png(out, read_ppm(ppm))
        return True
    except Exception as exc:  # a failed screenshot must not hide the boot result
        print(f"[boottest] screenshot failed: {exc}", flush=True)
        return False
    finally:
        ppm.unlink(missing_ok=True)


def run(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="boottest-"))
    serial = out / "serial.log"
    serial.unlink(missing_ok=True)
    qmp_path = workdir / "qmp.sock"
    kvm = kvm_usable() if args.accel == "auto" else args.accel == "kvm"
    scale = 1 if kvm else args.tcg_factor
    boot_timeout = args.boot_timeout * scale
    session_timeout = args.session_timeout * scale
    settle = args.settle * scale
    memory, cpus = guest_size(args.memory, args.cpus, kvm)
    cmd = qemu_command(Path(args.iso).resolve(), args.mode, workdir, serial, qmp_path, memory, cpus, kvm,
                       Path(args.disk).resolve() if args.disk else None, args.autoinstall_image or "", args.autoinstall_autologin, args.arch)
    print(f"[boottest] {args.mode}: {'KVM' if kvm else 'TCG (no KVM)'}; boot timeout {boot_timeout}s", flush=True)
    print("[boottest] " + " ".join(cmd), flush=True)

    result: dict = {
        "mode": args.mode,
        "arch": args.arch,
        "firmware": MODES[args.mode][0],
        "media": MODES[args.mode][1],
        "accel": "kvm" if kvm else "tcg",
        "iso": Path(args.iso).name,
        "outcome": "timeout",
        "timings": {},
        "screenshots": [],
    }
    started = time.monotonic()
    log = SerialLog(serial)
    with open(out / "qemu.log", "wb") as qemu_log:
        proc = subprocess.Popen(cmd, stdout=qemu_log, stderr=subprocess.STDOUT)
    qmp = None
    wanted = "LINUXLIVE_SESSION_READY" if args.session else "LINUXLIVE_BOOTED"
    if args.mode == "install":
        wanted = "LINUXLIVE_INSTALLED"
    install_deadline = False
    try:
        try:
            qmp = Qmp(qmp_path)
        except OSError as exc:
            result["outcome"] = "qemu-error"
            result["error"] = f"could not connect to QEMU's monitor: {exc}"
            raise
        deadline = started + boot_timeout
        presence = Presence(qmp, active=not kvm)
        while True:
            log.poll()
            if "LINUXLIVE_BOOTED" in log.markers:
                presence.tick()
            now = time.monotonic()
            for marker in log.markers:
                result["timings"].setdefault(marker, round(now - started, 1))
            if "LINUXLIVE_BOOTCHECK_START" in log.markers and deadline == started + boot_timeout:
                # Five minutes from the boot check to its first report, longer when emulated.
                deadline = now + session_timeout if args.session else now + 300 * scale
            if "LINUXLIVE_INSTALL_START" in log.markers and not install_deadline:
                deadline = now + args.install_timeout * scale
                install_deadline = True
            if wanted in log.markers or "LINUXLIVE_SESSION_TIMEOUT" in log.markers or "LINUXLIVE_INSTALL_FAILED" in log.markers:
                break
            if proc.poll() is not None:
                # The install powers the VM off right after its last line: read it first.
                log.poll()
                if wanted in log.markers or "LINUXLIVE_INSTALL_FAILED" in log.markers:
                    break
                result["outcome"] = "qemu-exited"
                break
            if now > deadline:
                break
            time.sleep(2)

        if "LINUXLIVE_BOOTED" in log.markers:
            result["outcome"] = "booted"
        if args.mode == "install":
            if "LINUXLIVE_INSTALLED" in log.markers:
                result["outcome"] = "installed"
            elif "LINUXLIVE_INSTALL_FAILED" in log.markers:
                result["outcome"] = "install-failed"
        elif args.session and "LINUXLIVE_SESSION_READY" in log.markers:
            result["outcome"] = "session"
            print(f"[boottest] session ready; waiting {settle}s for the desktop to settle", flush=True)
            presence.sleep(settle)
            for i in range(args.screenshots):
                name = f"screenshot-{i + 1}.png"
                if screenshot(qmp, workdir, out / name):
                    result["screenshots"].append(name)
                if i + 1 < args.screenshots:
                    presence.sleep(args.screenshot_interval)
            # The boot check's last look after login may still be running
            # (emulated, it is much slower): give it time to finish.
            checked_by = time.monotonic() + args.check_timeout * scale
            while "LINUXLIVE_CHECKED" not in log.markers and time.monotonic() < checked_by and proc.poll() is None:
                time.sleep(2)
                log.poll()
        elif wanted not in log.markers and proc.poll() is None:
            # Not where we wanted to be: keep a picture of where the boot got stuck.
            if screenshot(qmp, workdir, out / "stuck.png"):
                result["screenshots"].append("stuck.png")
    finally:
        log.poll()
        result["markers"] = log.markers
        result["facts"] = log.facts
        result["elapsed"] = round(time.monotonic() - started, 1)
        if qmp is not None:
            try:
                qmp.command("quit")
            except Exception:
                pass
            qmp.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        result["qemu_returncode"] = proc.returncode
        (out / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"[boottest] {args.mode}: outcome {result['outcome']} after {result['elapsed']}s", flush=True)
    for key in ("os_pretty_name", "firmware", "secureboot", "shell", "plymouth_theme", "selinux", "flatpak"):
        if key in log.facts:
            print(f"[boottest]   {key} = {', '.join(log.facts[key])}", flush=True)
    if args.mode == "install":
        reached = result["outcome"] == "installed"
    else:
        reached = result["outcome"] == "session" if args.session else result["outcome"] in ("booted", "session")
    return 0 if reached else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iso", required=True)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--out", required=True, help="directory for result.json, logs and screenshots")
    parser.add_argument("--session", action="store_true", help="wait for the desktop session and take screenshots")
    parser.add_argument("--accel", choices=("auto", "kvm", "tcg"), default="auto")
    parser.add_argument("--arch", choices=("amd64", "arm64"), default="amd64", help="the ISO's CPU (arm64: QEMU's virt machine)")
    parser.add_argument("--memory", type=int, help="guest RAM in MiB (default 4096; 8192 emulated)")
    parser.add_argument("--cpus", type=int, help="guest CPUs (default 2; emulated, up to 4 of the host's)")
    parser.add_argument("--boot-timeout", type=int, default=480, help="seconds until the boot check starts (KVM)")
    parser.add_argument("--session-timeout", type=int, default=600, help="seconds from boot check to desktop (KVM)")
    parser.add_argument("--settle", type=int, default=45, help="seconds to wait before the first screenshot (KVM)")
    parser.add_argument("--screenshots", type=int, default=3)
    parser.add_argument("--screenshot-interval", type=int, default=30)
    parser.add_argument("--check-timeout", type=int, default=120, help="seconds after the screenshots to wait for the boot check to finish (KVM)")
    parser.add_argument("--tcg-factor", type=int, default=5, help="multiply timeouts by this without KVM")
    parser.add_argument("--disk", help="qcow2 disk for the install and installed-* modes; a raw stick image for persist-*")
    parser.add_argument("--install-timeout", type=int, default=1800, help="seconds from the install's start to its end (KVM)")
    parser.add_argument("--autoinstall-image", help="registry image the unattended install installs from (Fedora)")
    parser.add_argument("--autoinstall-autologin", choices=("on", "spec"), default="on",
                        help="on: the install logs in automatically; spec: the spec's settings.autologin decides")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
