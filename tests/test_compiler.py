from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

import pytest
import yaml

from linuxlive.catalog import DATA_DIR, CatalogError, load_catalog, parse_catalog
from linuxlive.compiler import CompileError, build_context, compile_spec
from linuxlive.compiler.plan import LOGO_NAME, nearest_gnome_accent, volume_id
from linuxlive.spec import load_spec
from tests.conftest import EXAMPLES, ROOT, spec_data, tiny_jpeg, write_spec

GOLDEN = ROOT / "tests" / "golden"
PAIRS = [(base, desktop) for base in ("ubuntu", "fedora") for desktop in ("gnome", "kde", "cinnamon")]
COMMON = [
    "Containerfile",
    "iso.env",
    "linuxlive.json",
    "iso/boot/grub/grub.cfg",
    "iso/bios-embed.cfg",
    "iso/efi-grub.cfg",
    "rootfs/usr/lib/linuxlive/branding.env",
    "rootfs/usr/lib/systemd/system/linuxlive-bootcheck.service",
    "rootfs/usr/libexec/linuxlive/bootcheck",
    "rootfs/usr/libexec/linuxlive/finalize",
    "rootfs/usr/libexec/linuxlive/ini-set",
    "rootfs/usr/share/backgrounds/linuxlive/wallpaper.png",
    "rootfs/usr/share/pixmaps/linuxlive-logo.png",
    "rootfs/usr/share/icons/hicolor/256x256/apps/linuxlive-logo.png",
    "rootfs/usr/share/plymouth/themes/linuxlive/linuxlive.plymouth",
    "rootfs/usr/share/plymouth/themes/linuxlive/watermark.png",
]
ONLY = {
    "ubuntu": [
        "rootfs/etc/casper.conf",
        "rootfs/etc/apt/preferences.d/linuxlive",
        "rootfs/usr/share/plymouth/themes/linuxlive-text/linuxlive-text.plymouth",
    ],
    "fedora": [
        "rootfs/etc/sysconfig/livesys",
        "rootfs/usr/lib/systemd/system/bootloader-update.service.d/linuxlive-live.conf",
        "rootfs/usr/lib/tmpfiles.d/linuxlive-livesys.conf",
    ],
    "gnome": ["rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override"],
    "kde": ["rootfs/etc/xdg/autostart/linuxlive-branding.desktop", "rootfs/usr/libexec/linuxlive/kde-apply-branding"],
    "cinnamon": [
        "rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override",
        "rootfs/etc/lightdm/lightdm.conf.d/90-linuxlive.conf",
    ],
}
FROM = {
    ("ubuntu", "gnome"): "docker.io/library/ubuntu:26.04",
    ("ubuntu", "kde"): "docker.io/library/ubuntu:26.04",
    ("fedora", "gnome"): "quay.io/fedora/fedora-silverblue:44",
    ("fedora", "kde"): "quay.io/fedora/fedora-kinoite:44",
    ("ubuntu", "cinnamon"): "docker.io/library/ubuntu:26.04",
    ("fedora", "cinnamon"): "quay.io/fedora-ostree-desktops/base-atomic:44",
}
EXAMPLE_NAMES = sorted(p.stem for p in EXAMPLES.glob("*.yaml"))


def text(ctx, path: str) -> str:
    return ctx.files()[path][0].decode("utf-8")


def parse_env(content: str) -> dict[str, str]:
    values = {}
    for line in content.splitlines():
        if line and not line.startswith("#"):
            key, _, raw = line.partition("=")
            (values[key],) = shlex.split(raw)
    return values


@pytest.mark.parametrize(("base", "desktop"), PAIRS)
def test_every_pair_compiles_to_a_complete_context(workspace, base, desktop):
    spec = load_spec(write_spec(workspace, spec_data(base=base, desktop=desktop)))
    ctx, plan = build_context(spec)
    paths = set(ctx.paths())
    expected = set(COMMON) | set(ONLY[base]) | set(ONLY[desktop])
    assert expected <= paths, sorted(expected - paths)
    # Nothing from the other base or the other desktops leaks in.
    other = {path for key, files in ONLY.items() if key not in (base, desktop) for path in files} - expected
    assert not (other & paths), sorted(other & paths)
    containerfile = text(ctx, "Containerfile")
    assert f"FROM {FROM[(base, desktop)]}" in containerfile
    assert "RUN /bin/sh /usr/libexec/linuxlive/finalize" in containerfile
    env = parse_env(text(ctx, "iso.env"))
    assert env["LL_BASE"] == base
    assert env["LL_VOLID"] == plan.volid == "TESTOS_0_1"
    assert env["LL_FLATPAKS"] == "org.mozilla.firefox org.videolan.VLC"
    assert env["LL_LIVE_LAYOUT"] == {"ubuntu": "casper", "fedora": "liveos"}[base]
    branding = parse_env(text(ctx, "rootfs/usr/lib/linuxlive/branding.env"))
    assert branding["LL_DESKTOP"] == desktop
    assert branding["LL_ACCENT_RGB"] == "194,24,91"


def test_compilation_is_deterministic(workspace):
    spec = load_spec(write_spec(workspace, spec_data()))
    first, _ = build_context(spec)
    second, _ = build_context(load_spec(write_spec(workspace, spec_data())))
    assert first.manifest() == second.manifest()
    assert first.files() == second.files()


def golden_view(spec_path: Path) -> dict[str, bytes]:
    ctx, _ = build_context(load_spec(spec_path))
    view = {"MANIFEST": ctx.manifest().encode()}
    view.update({p: data for p, (data, _mode) in ctx.files().items() if not p.endswith(".png")})
    return view


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_examples_match_golden_output(name):
    """Text output is pinned; images are pinned by hash through MANIFEST.

    After an intended change, regenerate with LINUXLIVE_UPDATE_GOLDEN=1 and
    review the diff before committing.
    """
    actual = golden_view(EXAMPLES / f"{name}.yaml")
    golden = GOLDEN / name
    if os.environ.get("LINUXLIVE_UPDATE_GOLDEN") == "1":
        shutil.rmtree(golden, ignore_errors=True)
        for rel, data in actual.items():
            (golden / rel).parent.mkdir(parents=True, exist_ok=True)
            (golden / rel).write_bytes(data)
    expected = {p.relative_to(golden).as_posix(): p.read_bytes() for p in golden.rglob("*") if p.is_file()}
    assert sorted(actual) == sorted(expected)
    for rel in actual:
        assert actual[rel] == expected[rel], f"{rel} differs from the golden copy"


def test_write_replaces_a_previous_context_but_not_other_directories(workspace, tmp_path):
    spec = load_spec(write_spec(workspace, spec_data()))
    out = tmp_path / "ctx"
    compile_spec(spec, out)
    (out / "stale.txt").write_text("left over")
    compile_spec(spec, out)
    assert not (out / "stale.txt").exists()
    assert (out / "Containerfile").is_file()
    assert (out / "MANIFEST").read_text().count("\n") == len(build_context(spec)[0].paths())

    foreign = tmp_path / "precious"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me")
    with pytest.raises(CompileError, match="refusing to overwrite"):
        compile_spec(spec, foreign)
    assert (foreign / "notes.txt").read_text() == "keep me"


def test_executables_are_marked_executable(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(desktop="kde"))))
    modes = {path: mode for path, (_data, mode) in ctx.files().items()}
    for path, mode in modes.items():
        expected = 0o755 if path.startswith("rootfs/usr/libexec/") else 0o644
        assert mode == expected, path


def test_volume_id_is_iso9660_safe():
    class S:  # only the fields volume_id reads
        id = "my-really-long-distro-name"
        version = "2026.10.1+beta~2"

    vol = volume_id(S())
    assert len(vol) <= 32
    assert vol.replace("_", "").isalnum() and vol.isupper()
    assert vol.startswith("MY_REALLY_LONG_DISTRO_NAME")


@pytest.mark.parametrize(
    ("colour", "accent"),
    [("#c2185b", "pink"), ("#1e88e5", "blue"), ("#43a047", "green"), ("#e65100", "orange"), ("#808080", "slate"), ("#7b1fa2", "purple")],
)
def test_accent_maps_to_nearest_gnome_accent(colour, accent):
    assert nearest_gnome_accent(colour) == accent


def test_gnome_override_targets_the_ubuntu_session_only_on_ubuntu(workspace):
    ubuntu, _ = build_context(load_spec(write_spec(workspace, spec_data(base="ubuntu"))))
    fedora, _ = build_context(load_spec(write_spec(workspace, spec_data(base="fedora"))))
    path = "rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override"
    assert "[org.gnome.desktop.background:ubuntu]" in text(ubuntu, path)
    assert ":ubuntu]" not in text(fedora, path)
    for ctx in (ubuntu, fedora):
        assert "picture-uri='file:///usr/share/backgrounds/linuxlive/wallpaper.png'" in text(ctx, path)
        assert "accent-color='pink'" in text(ctx, path)


def test_kernel_arguments_match_the_live_layout(workspace):
    ubuntu, _ = build_context(load_spec(write_spec(workspace, spec_data(base="ubuntu"))))
    fedora, _ = build_context(load_spec(write_spec(workspace, spec_data(base="fedora"))))
    assert "linux /casper/vmlinuz boot=casper quiet splash ---" in text(ubuntu, "iso/boot/grub/grub.cfg")
    assert "root=live:CDLABEL=TESTOS_0_1 rd.live.image" in text(fedora, "iso/boot/grub/grub.cfg")
    for ctx in (ubuntu, fedora):
        assert "--file /.disk/linuxlive-testos_0_1" in text(ctx, "iso/bios-embed.cfg")
        assert "configfile $prefix/grub.cfg" in text(ctx, "iso/efi-grub.cfg")


def test_names_with_spaces_are_quoted_for_the_shell(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(name="My Distro", id="my-distro"))))
    assert parse_env(text(ctx, "iso.env"))["LL_NAME"] == "My Distro"
    assert parse_env(text(ctx, "rootfs/usr/lib/linuxlive/branding.env"))["LL_NAME"] == "My Distro"
    assert "menuentry 'Try My Distro 0.1'" in text(ctx, "iso/boot/grub/grub.cfg")


def test_jpeg_wallpaper_keeps_its_format(workspace):
    tiny_jpeg(workspace / "assets" / "photo.png", 1920, 1080)  # wrong extension on purpose
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(branding={"wallpaper": "assets/photo.png"}))))
    assert plan.wallpaper_path == "/usr/share/backgrounds/linuxlive/wallpaper.jpg"
    assert "rootfs/usr/share/backgrounds/linuxlive/wallpaper.jpg" in ctx.paths()


def test_no_host_paths_leak_into_the_context(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data())))
    host = str(workspace)
    for path, (data, _mode) in ctx.files().items():
        if not path.endswith(".png"):
            assert host not in data.decode("utf-8"), path
            assert host.replace("\\", "/") not in data.decode("utf-8"), path


def test_ubuntu_text_splash_carries_the_spec_name(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base="ubuntu"))))
    splash = text(ctx, "rootfs/usr/share/plymouth/themes/linuxlive-text/linuxlive-text.plymouth")
    assert "ModuleName=ubuntu-text" in splash
    assert "title=TestOS 0.1" in splash
    assert "brown=0xc2185b" in splash
    assert "Ubuntu" not in splash


def test_snap_packages_are_blocked_on_ubuntu(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base="ubuntu"))))
    prefs = text(ctx, "rootfs/etc/apt/preferences.d/linuxlive")
    assert "Package: firefox thunderbird snapd dracut cloud-init" in prefs
    assert "Pin-Priority: -1" in prefs
    # The pin file must be in place before any package is installed.
    containerfile = text(ctx, "Containerfile")
    assert containerfile.index("COPY rootfs/etc/apt/preferences.d/linuxlive") < containerfile.index("apt-get install")


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_cinnamon_override_sets_wallpaper_and_menu_logo_in_both_scopes(workspace, base):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(base=base, desktop="cinnamon"))))
    override = text(ctx, "rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override")
    for scope in ("", ":X-Cinnamon"):
        assert f"[org.cinnamon.desktop.background{scope}]" in override
        assert f"[org.cinnamon{scope}]" in override
    assert override.count("picture-uri='file:///usr/share/backgrounds/linuxlive/wallpaper.png'") == 2
    assert override.count("app-menu-icon-name='linuxlive-logo'") == 2
    # The menu looks the logo up by icon name, so it must be in the hicolor theme.
    assert "rootfs/usr/share/icons/hicolor/256x256/apps/linuxlive-logo.png" in ctx.paths()
    assert "org.gnome" not in override and ":ubuntu]" not in override
    assert plan.gnome_accent is None


def test_cinnamon_logs_in_through_lightdm(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(desktop="cinnamon"))))
    conf = text(ctx, "rootfs/etc/lightdm/lightdm.conf.d/90-linuxlive.conf")
    assert "[Seat:*]" in conf
    for line in ("greeter-session=slick-greeter", "user-session=cinnamon", "autologin-session=cinnamon"):
        assert line in conf.splitlines()


def test_fedora_installs_cinnamon_onto_base_atomic(workspace):
    cinnamon, plan = build_context(load_spec(write_spec(workspace, spec_data(base="fedora", desktop="cinnamon"))))
    containerfile = text(cinnamon, "Containerfile")
    assert "FROM quay.io/fedora-ostree-desktops/base-atomic:44" in containerfile
    assert f"dnf -y install {' '.join(plan.variant.packages)};" in containerfile
    assert {"cinnamon", "lightdm", "slick-greeter"} <= set(plan.variant.packages)
    # The desktop goes in before the branding that configures it.
    assert containerfile.index("# Desktop: Cinnamon.") < containerfile.index("COPY rootfs/ /")
    assert 'livesys_session="cinnamon"' in text(cinnamon, "rootfs/etc/sysconfig/livesys")
    for desktop in ("gnome", "kde"):
        ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base="fedora", desktop=desktop))))
        assert "# Desktop:" not in text(ctx, "Containerfile"), desktop


def test_finalize_brands_every_catalog_desktop(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data())))
    finalize = text(ctx, "rootfs/usr/libexec/linuxlive/finalize")
    for desktop in load_catalog().desktops:
        assert f"\n{desktop}) brand_{desktop} ;;\n" in finalize, desktop
        assert f"\nbrand_{desktop}() {{\n" in finalize, desktop


def test_bootcheck_waits_for_every_catalog_desktop_shell(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data())))
    bootcheck = text(ctx, "rootfs/usr/libexec/linuxlive/bootcheck")
    (loop,) = [line for line in bootcheck.splitlines() if line.strip().startswith("for proc in")]
    shells = set(loop.split("in", 1)[1].rstrip("; do").split())
    assert {d.shell_process for d in load_catalog().desktops.values()} <= shells


@pytest.mark.parametrize("desktop", ["gnome", "kde", "cinnamon"])
def test_fedora_swaps_its_logos_for_generic_logos(workspace, desktop):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(base="fedora", desktop=desktop))))
    containerfile = text(ctx, "Containerfile")
    assert ("fedora-logos", "generic-logos") in plan.base.replaced_packages
    for old, new in plan.base.replaced_packages:
        assert f"if rpm -q --quiet {old}; then dnf -y swap {old} {new}; fi;" in containerfile
    # After the desktop's packages, which could bring the logos in, and before branding.
    assert containerfile.index("dnf -y swap fedora-logos") < containerfile.index("COPY rootfs/ /")
    if "# Desktop:" in containerfile:
        assert containerfile.index("# Desktop:") < containerfile.index("dnf -y swap fedora-logos")


def test_ubuntu_has_nothing_to_swap(workspace):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data())))
    assert plan.base.replaced_packages == ()
    assert "swap" not in text(ctx, "Containerfile")


def test_replaced_packages_must_map_names_to_names():
    raw = yaml.safe_load((DATA_DIR / "catalog.yaml").read_text(encoding="utf-8"))
    raw["bases"]["fedora"]["replaced_packages"] = ["fedora-logos"]
    with pytest.raises(CatalogError, match="replaced_packages must map"):
        parse_catalog(raw)


def test_branding_env_names_the_logo_icon(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base="fedora", desktop="kde"))))
    env = text(ctx, "rootfs/usr/lib/linuxlive/branding.env")
    assert f"LL_LOGO_ICON={LOGO_NAME}\n" in env or f"LL_LOGO_ICON='{LOGO_NAME}'\n" in env
    script = text(ctx, "rootfs/usr/libexec/linuxlive/kde-apply-branding")
    assert "writeConfig('icon', '$LL_LOGO_ICON')" in script
