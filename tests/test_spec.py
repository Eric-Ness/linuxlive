from __future__ import annotations

import pytest

from linuxlive.catalog import load_catalog
from linuxlive.spec import SpecError, derive_id, load_schema, load_spec
from tests.conftest import EXAMPLES, solid_png, spec_data, tiny_jpeg, write_spec


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_lawn_darts_examples_are_valid(base):
    spec = load_spec(EXAMPLES / f"lawndarts-{base}.yaml")
    assert spec.name == "Lawn Darts Linux"
    assert spec.id == "lawn-darts-linux"  # derived from the name
    assert (spec.base, spec.desktop) == (base, "cinnamon")
    assert spec.branding.wallpaper.parent.name == "lawndarts"
    # The example splash, which CI boots on both bases.
    assert spec.branding.splash.script.name == "lawndarts.script"
    assert [p.name for p in spec.branding.splash.images] == ["dart.png", "bar.png", "bar-bg.png"]


def test_splash_is_optional(workspace):
    assert load_spec(write_spec(workspace, spec_data())).branding.splash is None


def splash_workspace(workspace, images=("a.png",)):
    folder = workspace / "assets" / "splash"
    folder.mkdir()
    (folder / "s.script").write_text("Window.SetBackgroundTopColor(0, 0, 0);\n", encoding="utf-8")
    for name in images:
        solid_png(folder / name, 8, 8)
    return {"script": "assets/splash/s.script", "images": [f"assets/splash/{n}" for n in images]}


def test_splash_is_read_from_the_spec_folder(workspace):
    splash = splash_workspace(workspace, ("glow.png", "bar.png"))
    spec = load_spec(write_spec(workspace, spec_data(branding={"splash": splash})))
    assert spec.branding.splash.script == (workspace / "assets" / "splash" / "s.script").resolve()
    assert [p.name for p in spec.branding.splash.images] == ["glow.png", "bar.png"]


def _splash_problems(workspace, change):
    splash = splash_workspace(workspace)
    change(workspace, splash)
    with pytest.raises(SpecError) as info:
        load_spec(write_spec(workspace, spec_data(branding={"splash": splash})))
    return " | ".join(info.value.problems)


def _too_big(ws, s):
    # Noise does not compress: about 9 MB of PNG, over the 8 MB limit.
    import os
    from linuxlive.png import Image, write_png
    write_png(ws / "assets" / "splash" / "big.png", Image(2000, 1500, os.urandom(2000 * 1500 * 3)), level=1)
    s["images"].append("assets/splash/big.png")


SPLASH_INVALID = {
    "missing script": (lambda ws, s: s.update(script="assets/splash/none.script"), "branding/splash/script: file 'assets/splash/none.script' not found"),
    "script outside the folder": (lambda ws, s: s.update(script="../outside.script"), "branding/splash/script: '../outside.script' must be inside the spec's folder"),
    "binary script": (lambda ws, s: (ws / "assets" / "splash" / "s.script").write_bytes(b"\x00\x01\x02"), "branding/splash/script: 'assets/splash/s.script' is not a text file"),
    "jpeg image": (lambda ws, s: (tiny_jpeg(ws / "assets" / "splash" / "photo.png", 8, 8), s["images"].append("assets/splash/photo.png")), "branding/splash/images/1: 'assets/splash/photo.png' must be a PNG"),
    "two images with one name": (lambda ws, s: ((ws / "assets" / "other").mkdir(), solid_png(ws / "assets" / "other" / "a.png", 8, 8), s["images"].append("assets/other/a.png")), "two images are called a.png"),
    "image called watermark.png": (lambda ws, s: (solid_png(ws / "assets" / "splash" / "watermark.png", 8, 8), s["images"].append("assets/splash/watermark.png")), "watermark.png is the logo's name in the splash"),
    "image with a bad name": (lambda ws, s: (solid_png(ws / "assets" / "splash" / "my image.png", 8, 8), s["images"].append("assets/splash/my image.png")), "an image's name must be letters, digits"),
    "too many images": (lambda ws, s: s.update(images=s["images"] * 25), "branding/splash/images: ["),
    "too many bytes": (_too_big, "a boot splash can be at most 8 MB"),
}


@pytest.mark.parametrize("case", sorted(SPLASH_INVALID))
def test_bad_splash_is_refused(workspace, case):
    change, message = SPLASH_INVALID[case]
    problems = _splash_problems(workspace, change)
    assert message in problems, problems


@pytest.mark.parametrize("name", ["testos-ubuntu", "testos-fedora"])
def test_example_specs_are_valid(name):
    spec = load_spec(EXAMPLES / f"{name}.yaml")
    assert spec.name == "TestOS"
    assert spec.id == "testos"
    assert spec.base == name.split("-")[1]
    assert spec.apps == ("firefox", "vlc")
    assert spec.branding.wallpaper.is_file() and spec.branding.logo.is_file()
    assert spec.branding.accent_color == "#c2185b"


def test_schema_enums_match_catalog():
    schema = load_schema()
    catalog = load_catalog()
    assert set(schema["properties"]["base"]["enum"]) == set(catalog.bases)
    assert set(schema["properties"]["desktop"]["enum"]) == set(catalog.desktops)


def test_every_desktop_is_available_on_every_base():
    catalog = load_catalog()
    for desktop in catalog.desktops.values():
        assert set(desktop.variants) == set(catalog.bases), desktop.id


def test_id_is_derived_from_name_when_omitted(workspace):
    spec = load_spec(write_spec(workspace, spec_data(id=None, name="My Distro 2")))
    assert spec.id == "my-distro-2"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("TestOS", "testos"), ("My  Cool--Distro", "my-cool-distro"), ("2Cool", "os-2cool"), ("a", "a")],
)
def test_derive_id(name, expected):
    assert derive_id(name) == expected


def test_accent_colour_is_optional(workspace):
    spec = load_spec(write_spec(workspace, spec_data(branding={"accent_color": None})))
    assert spec.branding.accent_color is None


def test_jpeg_wallpaper_is_accepted(workspace):
    tiny_jpeg(workspace / "assets" / "photo.jpg", 1920, 1080)
    spec = load_spec(write_spec(workspace, spec_data(branding={"wallpaper": "assets/photo.jpg"})))
    assert spec.branding.wallpaper.name == "photo.jpg"


INVALID = {
    "missing name": (dict(name=None), "'name' is a required property"),
    "unknown base": (dict(base="arch"), "base: 'arch' is not one of"),
    "unknown desktop": (dict(desktop="xfce"), "desktop: 'xfce' is not one of"),
    "unknown app": (dict(apps=["firefox", "notepad"]), "unknown app 'notepad'"),
    "duplicate app": (dict(apps=["vlc", "vlc"]), "non-unique elements"),
    "bad accent": (dict(branding={"accent_color": "#12345"}), "branding/accent_color"),
    "bad id": (dict(id="1bad"), "id: '1bad' does not match"),
    "id not derivable": (dict(id=None, name="a"), "could not derive a valid id"),
    "bad version": (dict(version="0.1 beta"), "version: '0.1 beta' does not match"),
    "bad name": (dict(name='Evil"Name'), "name: 'Evil\"Name' does not match"),
    "unknown setting": (dict(colour="red"), "Additional properties are not allowed ('colour' was unexpected)"),
    "wrong schema version": (dict(schema_version=2), "schema_version: 1 was expected"),
    "missing wallpaper": (dict(branding={"wallpaper": "assets/missing.png"}), "file 'assets/missing.png' not found"),
    "missing logo": (dict(branding={"logo": None}), "'logo' is a required property"),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_invalid_specs_are_rejected(workspace, case):
    changes, message = INVALID[case]
    with pytest.raises(SpecError) as info:
        load_spec(write_spec(workspace, spec_data(**changes)))
    assert any(message in p for p in info.value.problems), info.value.problems


def test_wallpaper_must_be_an_image(workspace):
    (workspace / "assets" / "notes.png").write_text("not an image")
    with pytest.raises(SpecError, match="not a PNG or JPEG"):
        load_spec(write_spec(workspace, spec_data(branding={"wallpaper": "assets/notes.png"})))


def test_wallpaper_must_be_large_enough(workspace):
    solid_png(workspace / "assets" / "small.png", 640, 480)
    with pytest.raises(SpecError, match="640x480; it must be at least 1280x720"):
        load_spec(write_spec(workspace, spec_data(branding={"wallpaper": "assets/small.png"})))


@pytest.mark.parametrize(("width", "height"), [(256, 128), (300, 300), (64, 64)])
def test_logo_must_be_square_and_a_supported_size(workspace, width, height):
    solid_png(workspace / "assets" / "odd.png", width, height)
    with pytest.raises(SpecError, match="it must be square, 128, 256, 512 pixels"):
        load_spec(write_spec(workspace, spec_data(branding={"logo": "assets/odd.png"})))


def test_logo_must_be_png(workspace):
    tiny_jpeg(workspace / "assets" / "logo.jpg", 256, 256)
    with pytest.raises(SpecError, match="must be a PNG"):
        load_spec(write_spec(workspace, spec_data(branding={"logo": "assets/logo.jpg"})))


def test_every_problem_is_reported_at_once(workspace):
    data = spec_data(apps=["notepad"], branding={"logo": "assets/nope.png"})
    with pytest.raises(SpecError) as info:
        load_spec(write_spec(workspace, data))
    problems = " | ".join(info.value.problems)
    assert "unknown app 'notepad'" in problems
    assert "file 'assets/nope.png' not found" in problems


def test_top_level_must_be_a_mapping(workspace):
    with pytest.raises(SpecError, match="must be a mapping"):
        load_spec(write_spec(workspace, "- just\n- a list\n"))


def test_yaml_syntax_errors_are_reported(workspace):
    with pytest.raises(SpecError, match="not valid YAML"):
        load_spec(write_spec(workspace, "name: [unclosed\n"))


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(SpecError, match="file not found"):
        load_spec(tmp_path / "nope.yaml")
