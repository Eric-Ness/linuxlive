"""Inspect a hybrid ISO without mounting it or calling external tools.

Reports what makes an image bootable: the El Torito boot catalog (BIOS and
EFI entries), the MBR and GPT written for USB boot (including the EFI system
partition), and whether given files exist, found through the Joliet tree.
When the ISO carries an md5sum.txt (Ubuntu's medium check reads it), every
checksum in it is checked against the file's bytes in the image.
"""

from __future__ import annotations

import hashlib
import re
import struct
import uuid
from pathlib import Path

SECTOR = 2048
ESP_GUID = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
JOLIET_ESCAPES = (b"%/@", b"%/C", b"%/E")
MD5SUMS = "md5sum.txt"
MD5_LINE = re.compile(r"([0-9a-f]{32})\s+(\S.*)")

# Files the verifier may ask about, for either live layout.
DEFAULT_PATHS = (
    ".disk/info",
    "boot/grub/grub.cfg",
    "boot/grub/i386-pc/eltorito.img",
    "EFI/BOOT/BOOTX64.EFI",
    "EFI/BOOT/grubx64.efi",
    "EFI/BOOT/mmx64.efi",
    "casper/filesystem.squashfs",
    "casper/vmlinuz",
    "casper/initrd",
    "LiveOS/squashfs.img",
    "live/vmlinuz",
    "live/initrd.img",
)


class IsoError(ValueError):
    """The file is not an ISO 9660 image this module can read."""


def _read(fh, offset: int, size: int) -> bytes:
    fh.seek(offset)
    data = fh.read(size)
    if len(data) != size:
        raise IsoError(f"unexpected end of file at offset {offset}")
    return data


def _mbr(fh) -> dict:
    sector = _read(fh, 0, 512)
    partitions = []
    for i in range(4):
        entry = sector[446 + 16 * i : 462 + 16 * i]
        status, ptype = entry[0], entry[4]
        start, count = struct.unpack("<II", entry[8:16])
        if ptype:
            partitions.append({"bootable": status == 0x80, "type": ptype, "start": start, "sectors": count})
    return {"signature": sector[510:512] == b"\x55\xaa", "partitions": partitions}


def _gpt(fh) -> dict:
    header = _read(fh, 512, 92)
    if header[0:8] != b"EFI PART":
        return {"present": False, "esp": None, "partitions": 0}
    entries_lba, count, entry_size = struct.unpack("<QII", header[72:88])
    if entry_size < 128 or count > 1024:
        raise IsoError("implausible GPT partition table")
    table = _read(fh, entries_lba * 512, count * entry_size)
    esp = None
    used = 0
    for i in range(count):
        entry = table[i * entry_size : (i + 1) * entry_size]
        type_guid = uuid.UUID(bytes_le=entry[0:16])
        if type_guid.int == 0:
            continue
        used += 1
        first, last = struct.unpack("<QQ", entry[32:48])
        if type_guid == ESP_GUID and esp is None:
            esp = {"index": i + 1, "first_lba": first, "last_lba": last}
    return {"present": True, "esp": esp, "partitions": used}


def _volume_descriptors(fh) -> dict:
    found: dict = {}
    for n in range(16, 64):
        vd = _read(fh, n * SECTOR, SECTOR)
        kind, ident = vd[0], vd[1:6]
        if ident != b"CD001":
            raise IsoError("no ISO 9660 volume descriptor set")
        if kind == 255:
            break
        if kind == 0 and vd[7:39].rstrip(b"\0") == b"EL TORITO SPECIFICATION":
            found["boot_catalog"] = struct.unpack("<I", vd[71:75])[0]
        elif kind == 1:
            found["pvd"] = vd
        elif kind == 2 and vd[88:91] in JOLIET_ESCAPES:
            found["joliet"] = vd
    if "pvd" not in found:
        raise IsoError("no primary volume descriptor")
    return found


def _el_torito(fh, catalog_lba: int | None) -> dict:
    result = {"present": catalog_lba is not None, "bios": False, "efi": False, "entries": []}
    if catalog_lba is None:
        return result
    catalog = _read(fh, catalog_lba * SECTOR, SECTOR)
    if catalog[0] != 1 or catalog[30:32] != b"\x55\xaa":
        raise IsoError("El Torito boot catalog has no valid validation entry")
    platform = catalog[1]
    entries = []

    def entry(raw: bytes, platform_id: int) -> dict:
        sectors, lba = struct.unpack("<HI", raw[6:12])
        return {"platform": platform_id, "bootable": raw[0] == 0x88, "lba": lba, "sectors": sectors}

    entries.append(entry(catalog[32:64], platform))
    pos = 64
    while pos + 32 <= SECTOR:
        header = catalog[pos : pos + 32]
        if header[0] not in (0x90, 0x91):
            break
        platform = header[1]
        (count,) = struct.unpack("<H", header[2:4])
        pos += 32
        for _ in range(count):
            if pos + 32 > SECTOR:
                break
            entries.append(entry(catalog[pos : pos + 32], platform))
            pos += 32
        if header[0] == 0x91:
            break
    result["entries"] = entries
    result["bios"] = any(e["platform"] == 0x00 and e["bootable"] for e in entries)
    result["efi"] = any(e["platform"] == 0xEF and e["bootable"] for e in entries)
    return result


def _dir_records(fh, lba: int, size: int):
    data = _read(fh, lba * SECTOR, size)
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0:  # records never cross a sector boundary
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        record = data[pos : pos + length]
        pos += length
        name_len = record[32]
        raw_name = record[33 : 33 + name_len]
        if raw_name in (b"\0", b"\1"):
            continue
        yield {
            "name": raw_name,
            "lba": struct.unpack("<I", record[2:6])[0],
            "size": struct.unpack("<I", record[10:14])[0],
            "flags": record[25],
        }


def _joliet_name(raw: bytes) -> str:
    name = raw.decode("utf-16-be", errors="replace")
    return name.split(";", 1)[0]


def _lookup(fh, root: bytes, path: str) -> int | None:
    """Size of the file at ``path`` in the Joliet tree, or None if missing."""
    lba = struct.unpack("<I", root[2:6])[0]
    size = struct.unpack("<I", root[10:14])[0]
    parts = [p for p in path.split("/") if p]
    for depth, part in enumerate(parts):
        last = depth == len(parts) - 1
        total = None
        next_dir = None
        for rec in _dir_records(fh, lba, size):
            if _joliet_name(rec["name"]) != part:
                continue
            is_dir = bool(rec["flags"] & 0x02)
            if last and not is_dir:
                total = (total or 0) + rec["size"]  # multi-extent files repeat the name
            elif not last and is_dir:
                next_dir = rec
                break
        if last:
            return total
        if next_dir is None:
            return None
        lba, size = next_dir["lba"], next_dir["size"]
    return None


def _tree(fh, lba: int, size: int, prefix: str = "", seen: set[int] | None = None) -> dict[str, list[tuple[int, int]]]:
    """Every file under a Joliet directory: path -> its extents, in order."""
    seen = set() if seen is None else seen
    if lba in seen:
        raise IsoError("directory tree loops back on itself")
    seen.add(lba)
    files: dict[str, list[tuple[int, int]]] = {}
    for rec in _dir_records(fh, lba, size):
        path = prefix + _joliet_name(rec["name"])
        if rec["flags"] & 0x02:
            files.update(_tree(fh, rec["lba"], rec["size"], path + "/", seen))
        else:
            files.setdefault(path, []).append((rec["lba"], rec["size"]))
    return files


def _content(fh, extents: list[tuple[int, int]]):
    for lba, size in extents:
        fh.seek(lba * SECTOR)
        left = size
        while left:
            block = fh.read(min(left, 1 << 20))
            if not block:
                raise IsoError(f"file data at sector {lba} runs past the end of the image")
            left -= len(block)
            yield block


def _check_md5sums(fh, files: dict[str, list[tuple[int, int]]]) -> dict | None:
    """md5sum.txt against the image, the way casper-md5check reads it."""
    if MD5SUMS not in files:
        return None
    listed: dict[str, str] = {}
    text = b"".join(_content(fh, files[MD5SUMS])).decode("utf-8", errors="replace")
    for line in text.splitlines():
        m = MD5_LINE.fullmatch(line.strip())
        if m:
            listed[m.group(2).removeprefix("./")] = m.group(1)
    mismatched, missing = [], []
    for name, want in sorted(listed.items()):
        if name not in files:
            missing.append(name)
            continue
        digest = hashlib.md5(usedforsecurity=False)
        for block in _content(fh, files[name]):
            digest.update(block)
        if digest.hexdigest() != want:
            mismatched.append(name)
    return {
        "entries": len(listed),
        "mismatched": mismatched,
        "missing": missing,
        "unlisted": sorted(set(files) - set(listed) - {MD5SUMS}),
    }


def inspect_iso(path: Path | str, paths=DEFAULT_PATHS, *, checksum: bool = True) -> dict:
    path = Path(path)
    with open(path, "rb") as fh:
        descriptors = _volume_descriptors(fh)
        pvd = descriptors["pvd"]
        report = {
            "file": path.name,
            "size": path.stat().st_size,
            "volume_id": pvd[40:72].decode("ascii", errors="replace").rstrip(),
            "mbr": _mbr(fh),
            "gpt": _gpt(fh),
            "el_torito": _el_torito(fh, descriptors.get("boot_catalog")),
            "joliet": "joliet" in descriptors,
            "files": {},
            "md5sum": None,
        }
        if "joliet" in descriptors:
            root = descriptors["joliet"][156:190]
            report["files"] = {p: _lookup(fh, root, p) for p in paths}
            tree = _tree(fh, struct.unpack("<I", root[2:6])[0], struct.unpack("<I", root[10:14])[0])
            report["md5sum"] = _check_md5sums(fh, tree)
    if checksum:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
        report["sha256"] = digest.hexdigest()
    return report
