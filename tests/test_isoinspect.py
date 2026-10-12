"""ISO inspection against small synthetic images built byte by byte."""

from __future__ import annotations

import hashlib
import struct
import uuid

import pytest

from linuxlive.isoinspect import ESP_GUID, IsoError, inspect_iso

SECTOR = 2048


def _dir_record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    length = 33 + len(name) + (len(name) + 1) % 2  # pad to an even length
    rec = bytearray(length)
    rec[0] = length
    rec[2:6] = struct.pack("<I", lba)
    rec[10:14] = struct.pack("<I", size)
    rec[25] = 0x02 if is_dir else 0x00
    rec[32] = len(name)
    rec[33 : 33 + len(name)] = name
    return bytes(rec)


def build_iso(path, *, files, bios=True, efi=True, gpt=True, volid="TESTOS_0_1", multi_extent=None):
    """Write a minimal hybrid ISO: MBR, GPT, PVD, El Torito, Joliet tree.

    A file is a size (a record pointing at no data) or its bytes. A
    multi_extent entry splits a file into parts, each a size or bytes.
    """
    sectors = {}
    next_free = [40]

    def alloc(count=1):
        next_free[0] += count
        return next_free[0] - count

    # Directory tree from the file paths.
    tree: dict = {}
    for path_name, size in files.items():
        node = tree
        *dirs, leaf = path_name.split("/")
        for d in dirs:
            node = node.setdefault(d, {})
        node[leaf] = size

    def write_dir(node, lba, parent_lba):
        records = [_dir_record(b"\0", lba, SECTOR, True), _dir_record(b"\1", parent_lba, SECTOR, True)]
        for name, child in sorted(node.items()):
            joliet = name.encode("utf-16-be")
            if isinstance(child, dict):
                child_lba = alloc()
                write_dir(child, child_lba, lba)
                records.append(_dir_record(joliet, child_lba, SECTOR, True))
            else:
                file_name = joliet + ";1".encode("utf-16-be")
                parts = multi_extent.get(name, [child]) if multi_extent else [child]
                for index, part in enumerate(parts):
                    if isinstance(part, bytes):
                        data_lba = alloc(max(1, -(-len(part) // SECTOR)))
                        sectors[data_lba] = part
                        rec = bytearray(_dir_record(file_name, data_lba, len(part), False))
                    else:
                        rec = bytearray(_dir_record(file_name, 0, part, False))
                    if index < len(parts) - 1:
                        rec[25] |= 0x80  # more extents follow
                    records.append(bytes(rec))
        data = b"".join(records)
        assert len(data) <= SECTOR
        sectors[lba] = data

    root_lba = alloc()
    write_dir(tree, root_lba, root_lba)

    image = bytearray(max(80, next_free[0]) * SECTOR)
    image[510:512] = b"\x55\xaa"
    if gpt:
        header = bytearray(92)
        header[0:8] = b"EFI PART"
        header[72:88] = struct.pack("<QII", 2, 4, 128)
        image[512 : 512 + 92] = header
        entry = bytearray(128)
        entry[0:16] = ESP_GUID.bytes_le
        entry[16:32] = uuid.uuid4().bytes_le
        entry[32:48] = struct.pack("<QQ", 100, 200)
        image[1024 : 1024 + 128] = entry

    def vd(kind):
        d = bytearray(SECTOR)
        d[0] = kind
        d[1:6] = b"CD001"
        d[6] = 1
        return d

    pvd = vd(1)
    pvd[40:72] = volid.encode().ljust(32)
    pvd[156:190] = _dir_record(b"\0", root_lba, SECTOR, True)[:34]
    image[16 * SECTOR : 17 * SECTOR] = pvd
    boot = vd(0)
    boot[7:39] = b"EL TORITO SPECIFICATION".ljust(32, b"\0")
    boot[71:75] = struct.pack("<I", 30)
    image[17 * SECTOR : 18 * SECTOR] = boot
    joliet = vd(2)
    joliet[88:91] = b"%/E"
    joliet[156:190] = _dir_record(b"\0", root_lba, SECTOR, True)[:34]
    image[18 * SECTOR : 19 * SECTOR] = joliet
    image[19 * SECTOR : 20 * SECTOR] = vd(255)

    catalog = bytearray(SECTOR)
    catalog[0] = 1
    catalog[30:32] = b"\x55\xaa"
    catalog[32] = 0x88 if bios else 0x00
    catalog[38:44] = struct.pack("<HI", 4, 50)
    if efi:
        catalog[64] = 0x91
        catalog[65] = 0xEF
        catalog[66:68] = struct.pack("<H", 1)
        catalog[96] = 0x88
        catalog[102:108] = struct.pack("<HI", 0, 60)
    image[30 * SECTOR : 31 * SECTOR] = catalog
    for lba, data in sectors.items():
        image[lba * SECTOR : lba * SECTOR + len(data)] = data
    path.write_bytes(bytes(image))
    return path


FILES = {
    ".disk/info": 40,
    "boot/grub/grub.cfg": 900,
    "casper/filesystem.squashfs": 3_000_000_000,
    "casper/vmlinuz": 15_000_000,
    "EFI/BOOT/BOOTX64.EFI": 1_000_000,
}


def test_reports_a_complete_hybrid_image(tmp_path):
    report = inspect_iso(build_iso(tmp_path / "ok.iso", files=FILES), paths=list(FILES) + ["LiveOS/squashfs.img"])
    assert report["volume_id"] == "TESTOS_0_1"
    assert report["el_torito"]["bios"] and report["el_torito"]["efi"]
    assert report["mbr"]["signature"]
    assert report["gpt"]["present"] and report["gpt"]["esp"]["first_lba"] == 100
    for name, size in FILES.items():
        assert report["files"][name] == size
    assert report["files"]["LiveOS/squashfs.img"] is None
    assert len(report["sha256"]) == 64


def test_detects_a_missing_efi_entry(tmp_path):
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=FILES, efi=False))
    assert report["el_torito"]["bios"] and not report["el_torito"]["efi"]


def test_detects_a_missing_bios_entry(tmp_path):
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=FILES, bios=False))
    assert not report["el_torito"]["bios"] and report["el_torito"]["efi"]


def test_detects_a_missing_gpt(tmp_path):
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=FILES, gpt=False))
    assert report["gpt"] == {"present": False, "esp": None, "partitions": 0}


def test_directories_are_not_reported_as_files(tmp_path):
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=FILES), paths=["casper", "boot/grub"])
    assert report["files"] == {"casper": None, "boot/grub": None}


def test_multi_extent_files_add_up(tmp_path):
    iso = build_iso(
        tmp_path / "x.iso",
        files={"LiveOS/squashfs.img": 0},
        multi_extent={"squashfs.img": [4_294_965_248, 1_000_000]},
    )
    assert inspect_iso(iso, paths=["LiveOS/squashfs.img"])["files"]["LiveOS/squashfs.img"] == 4_295_965_248


KERNEL = b"kernel " * 1000
INFO = b"TestOS 0.1\n"
BOOT_IMAGE = b"\xeb" * 3000


def md5sum_txt(entries: dict[str, bytes]) -> bytes:
    """md5sum.txt as `md5sum` writes it from the ISO's root."""
    return "".join(f"{hashlib.md5(data).hexdigest()}  ./{name}\n" for name, data in entries.items()).encode()


def test_checks_md5sum_txt_against_the_files(tmp_path):
    files = {".disk/info": INFO, "casper/vmlinuz": KERNEL, "boot/grub/i386-pc/eltorito.img": BOOT_IMAGE}
    files["md5sum.txt"] = md5sum_txt({".disk/info": INFO, "casper/vmlinuz": KERNEL})
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=files))
    assert report["md5sum"] == {"entries": 2, "mismatched": [], "missing": [], "unlisted": ["boot/grub/i386-pc/eltorito.img"]}


def test_a_wrong_checksum_is_a_mismatch(tmp_path):
    files = {".disk/info": INFO, "casper/vmlinuz": KERNEL}
    files["md5sum.txt"] = md5sum_txt({".disk/info": INFO, "casper/vmlinuz": KERNEL[:-1]})
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=files))
    assert report["md5sum"]["mismatched"] == ["casper/vmlinuz"]


def test_a_listed_file_that_is_not_there_is_missing(tmp_path):
    files = {".disk/info": INFO, "md5sum.txt": md5sum_txt({".disk/info": INFO, "casper/initrd": b"x"})}
    report = inspect_iso(build_iso(tmp_path / "x.iso", files=files))
    assert report["md5sum"]["missing"] == ["casper/initrd"]
    assert report["md5sum"]["mismatched"] == []


def test_no_md5sum_txt_is_reported_as_none(tmp_path):
    assert inspect_iso(build_iso(tmp_path / "x.iso", files={".disk/info": INFO}))["md5sum"] is None


def test_md5_of_a_multi_extent_file_covers_every_extent(tmp_path):
    head, tail = b"a" * (3 * SECTOR), b"b" * 100
    for listed, expected in ((head + tail, []), (head, ["LiveOS/squashfs.img"])):
        iso = build_iso(
            tmp_path / f"x{len(expected)}.iso",
            files={"LiveOS/squashfs.img": 0, "md5sum.txt": md5sum_txt({"LiveOS/squashfs.img": listed})},
            multi_extent={"squashfs.img": [head, tail]},
        )
        assert inspect_iso(iso)["md5sum"]["mismatched"] == expected


def test_rejects_files_that_are_not_iso9660(tmp_path):
    (tmp_path / "junk.iso").write_bytes(b"\0" * (40 * SECTOR))
    with pytest.raises(IsoError):
        inspect_iso(tmp_path / "junk.iso")
