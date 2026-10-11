from __future__ import annotations

import copy
import shutil
import struct
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from linuxlive.png import Image, write_png  # noqa: E402

EXAMPLES = ROOT / "examples"
PINK = (194, 24, 91)

BASE_SPEC = {
    "schema_version": 1,
    "name": "TestOS",
    "id": "testos",
    "version": "0.1",
    "base": "ubuntu",
    "desktop": "gnome",
    "apps": ["firefox", "vlc"],
    "branding": {"wallpaper": "assets/wallpaper.png", "logo": "assets/logo.png", "accent_color": "#c2185b"},
}


def solid_png(path: Path, width: int, height: int, rgb=PINK) -> Path:
    write_png(path, Image(width, height, bytes(rgb) * (width * height)))
    return path


def tiny_jpeg(path: Path, width: int, height: int) -> Path:
    """Just enough JPEG for header parsing: SOI, a baseline SOF0 segment, EOI."""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")
    return path


def spec_data(**changes) -> dict:
    """BASE_SPEC with top-level changes; branding changes merge; None deletes."""
    data = copy.deepcopy(BASE_SPEC)
    for key, value in changes.items():
        if key == "branding" and isinstance(value, dict):
            for bkey, bvalue in value.items():
                if bvalue is None:
                    data["branding"].pop(bkey, None)
                else:
                    data["branding"][bkey] = bvalue
        elif value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def write_spec(directory: Path, data, name: str = "spec.yaml") -> Path:
    path = directory / name
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A temp directory holding a copy of the example assets."""
    shutil.copytree(EXAMPLES / "assets", tmp_path / "assets")
    return tmp_path
