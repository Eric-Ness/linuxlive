"""Settings and no-code options: language, keyboard, time zone, computer name,
automatic login, hidden menu entries, Firefox's policies and kiosk mode."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

import pytest

from linuxlive.catalog import load_catalog
from linuxlive.compiler import build_context
from linuxlive.compiler.options import KIOSK_USER, firefox_policies_text
from linuxlive.png import Image, write_png
from linuxlive.spec import SpecError, load_spec
from linuxlive.verify import SESSION_MODE, verify_results
from tests.conftest import EXAMPLES, ROOT, spec_data, write_spec
from tests.test_verify import COMMIT, failures, make_results

SETTINGS_EXAMPLES = ["settings-ubuntu", "settings-fedora", "settings-cinnamon", "kiosk-ubuntu", "kiosk-fedora"]
FULL = {
    "language": "de_DE",
    "keyboard": "de",
    "timezone": "Europe/Berlin",
    "hostname": "lab-pc",
    "autologin": True,
    "hidden_apps": ["yelp"],
}


def text(ctx, path: str) -> str:
    return ctx.files()[path][0].decode("utf-8")


def env(ctx, path: str) -> dict[str, str]:
    values = {}
    for line in text(ctx, path).splitlines():
        if line and not line.startswith("#"):
            key, _, raw = line.partition("=")
            (values[key],) = shlex.split(raw) or [""]
    return values


def problems(workspace, **changes) -> str:
    with pytest.raises(SpecError) as err:
        load_spec(write_spec(workspace, spec_data(**changes)))
    return " | ".join(err.value.problems)


# ---- The spec ----------------------------------------------------------------


def test_every_option_loads(workspace):
    spec = load_spec(write_spec(workspace, spec_data(settings=FULL, firefox={"homepage": "https://example.org/", "bookmarks": [{"name": "Ex", "url": "https://example.org/a"}]}, kiosk={"app": "firefox"})))
    s = spec.settings
    assert (s.language, s.keyboard, s.timezone, s.hostname, s.autologin, s.hidden_apps) == ("de_DE", "de", "Europe/Berlin", "lab-pc", True, ("yelp",))
    assert spec.firefox.homepage == "https://example.org/" and spec.firefox.bookmarks[0].name == "Ex"
    assert spec.kiosk == "firefox"
    # Options are data: Build it for me still accepts the spec.
    assert not spec.runs_own_code


def test_a_spec_without_options_has_none(workspace):
    spec = load_spec(write_spec(workspace, spec_data()))
    assert spec.settings == type(spec.settings)() and spec.firefox is None and spec.kiosk is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"settings": {"language": "xx_XX"}}, "settings/language: 'xx_XX' is not one linuxlive knows"),
        ({"settings": {"language": "german"}}, "settings/language: 'german' does not match"),
        ({"settings": {"keyboard": "qq"}}, "settings/keyboard: 'qq' is not one linuxlive knows"),
        ({"settings": {"timezone": "Europe/Atlantis"}}, "settings/timezone: 'Europe/Atlantis' is not a time zone"),
        ({"settings": {"timezone": "../../etc/passwd"}}, "settings/timezone: '../../etc/passwd' does not match"),
        ({"settings": {"hostname": "Lab_PC"}}, "settings/hostname: 'Lab_PC' does not match"),
        ({"settings": {"hostname": "-lab"}}, "settings/hostname: '-lab' does not match"),
        ({"settings": {"autologin": "yes"}}, "settings/autologin: 'yes' is not of type 'boolean'"),
        ({"settings": {"hidden_apps": ["linuxlive-install"]}}, "settings/hidden_apps: 'linuxlive-install' is linuxlive's own"),
        ({"settings": {"hidden_apps": ["../../etc/x"]}}, "settings/hidden_apps/0: '../../etc/x' does not match"),
        ({"settings": {"wallpaper": "x"}}, "settings: Additional properties are not allowed"),
        ({"apps": ["vlc"], "firefox": {"homepage": "https://example.org/"}}, "firefox: the home page and bookmarks are Firefox's; add firefox to apps"),
        ({"firefox": {"homepage": "javascript:alert(1)"}}, "firefox/homepage: 'javascript:alert(1)' does not match"),
        ({"firefox": {"homepage": 'https://x.org/"><script>'}}, "firefox/homepage"),
        ({"firefox": {"bookmarks": [{"name": "a\nb", "url": "https://x.org/"}]}}, "firefox/bookmarks/0/name"),
        ({"firefox": {"bookmarks": [{"url": "https://x.org/"}]}}, "'name' is a required property"),
        ({"kiosk": {"app": "steam"}}, "kiosk/app: 'steam' must be one of the spec's apps"),
        ({"kiosk": {}}, "kiosk: 'app' is a required property"),
    ],
)
def test_bad_options_are_refused(workspace, changes, message):
    assert message in problems(workspace, **changes)


def test_every_catalog_language_and_layout_is_accepted(workspace):
    catalog = load_catalog()
    assert len(catalog.languages) >= 20 and len(catalog.keyboards) >= 20
    # YAML 1.1 reads a bare `no` as false; Norwegian must survive as a layout name.
    assert "no" in catalog.keyboards
    for lang in catalog.languages:
        load_spec(write_spec(workspace, spec_data(settings={"language": lang})))
    for layout in catalog.keyboards:
        load_spec(write_spec(workspace, spec_data(settings={"keyboard": layout})))


# ---- The compiler --------------------------------------------------------------


@pytest.mark.parametrize(
    ("base", "desktop", "packages"),
    [
        ("ubuntu", "gnome", "language-pack-de language-pack-gnome-de"),
        ("ubuntu", "kde", "language-pack-de language-pack-kde-de"),
        ("ubuntu", "cinnamon", "language-pack-de language-pack-gnome-de cinnamon-l10n"),
        ("fedora", "gnome", "langpacks-de"),
        ("fedora", "kde", "langpacks-de"),
        ("fedora", "cinnamon", "langpacks-de"),
    ],
)
def test_the_language_brings_its_translations(workspace, base, desktop, packages):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(base=base, desktop=desktop, settings=FULL))))
    containerfile = text(ctx, "Containerfile")
    install = "apt-get install -y " if base == "ubuntu" else "dnf -y install --exclude=PackageKit "
    assert f"{install}{packages};" in containerfile
    # Before the image's own files and finalize, which generates the locale.
    assert containerfile.index(packages) < containerfile.index("COPY rootfs/ /")
    branding = env(ctx, "rootfs/usr/lib/linuxlive/branding.env")
    assert (branding["LL_LANGUAGE"], branding["LL_KEYBOARD"], branding["LL_TIMEZONE"]) == ("de_DE", "de", "Europe/Berlin")
    assert (branding["LL_HOSTNAME"], branding["LL_AUTOLOGIN_DEFAULT"], branding["LL_HIDDEN_APPS"]) == ("lab-pc", "1", "yelp")
    iso = env(ctx, "iso.env")
    assert iso["LL_HOSTNAME"] == "lab-pc" and iso["LL_FLATPAK_LANGUAGES"] == "de"
    resolved = json.loads(text(ctx, "linuxlive.json"))["settings"]
    assert resolved["packages"] == packages.split() and resolved["hostname"] == "lab-pc"


def test_cjk_languages_bring_fonts_on_ubuntu(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(desktop="kde", settings={"language": "ja_JP"}))))
    assert "apt-get install -y language-pack-ja language-pack-kde-ja fonts-noto-cjk;" in text(ctx, "Containerfile")


def test_without_options_nothing_changes(workspace):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data())))
    assert "# Settings:" not in text(ctx, "Containerfile")
    assert "settings" not in json.loads(text(ctx, "linuxlive.json"))
    branding = env(ctx, "rootfs/usr/lib/linuxlive/branding.env")
    assert branding["LL_HOSTNAME"] == "testos" and branding["LL_LANGUAGE"] == "" and branding["LL_KIOSK_APP"] == ""
    assert env(ctx, "iso.env")["LL_FLATPAK_LANGUAGES"] == ""
    assert not [p for p in ctx.paths() if "kiosk" in p or "firefox" in p or p.endswith("kxkbrc")]
    assert "input-sources" not in text(ctx, "rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override")


def test_the_computer_name_reaches_casper(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(settings={"hostname": "lab-pc"}))))
    assert 'export HOST="lab-pc"' in text(ctx, "rootfs/etc/casper.conf")


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_gnome_types_with_the_spec_layout(workspace, base):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base=base, settings={"keyboard": "fr"}))))
    override = text(ctx, "rootfs/usr/share/glib-2.0/schemas/zz_linuxlive.gschema.override")
    scopes = ["", ":ubuntu"] if base == "ubuntu" else [""]
    for scope in scopes:
        assert f"[org.gnome.desktop.input-sources{scope}]\nsources=[('xkb', 'fr')]" in override
    assert "rootfs/etc/xdg/kxkbrc" not in ctx.paths()


def test_plasma_types_with_the_spec_layout(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(desktop="kde", settings={"keyboard": "fr"}))))
    assert text(ctx, "rootfs/etc/xdg/kxkbrc") == "[Layout]\nLayoutList=fr\nUse=true\n"


def test_firefox_policies(workspace):
    ff = {"homepage": "https://example.org/", "bookmarks": [{"name": "Schule \"A\"", "url": "https://example.org/a"}]}
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(firefox=ff))))
    policies = json.loads(text(ctx, "rootfs/usr/lib/linuxlive/firefox/policies/policies.json"))["policies"]
    assert policies["Homepage"] == {"URL": "https://example.org/", "StartPage": "homepage", "Locked": False}
    assert policies["OverrideFirstRunPage"] == ""
    assert policies["Bookmarks"] == [{"Title": 'Schule "A"', "URL": "https://example.org/a", "Placement": "toolbar"}]
    assert policies["NoDefaultBookmarks"] is True
    assert policies["SkipTermsOfUse"] is True and policies["DisableProfileImport"] is True
    assert policies["UserMessaging"]["SkipOnboarding"] is True
    tmpfiles = text(ctx, "rootfs/usr/lib/tmpfiles.d/linuxlive-firefox.conf")
    assert "L+ /var/lib/flatpak/extension/org.mozilla.firefox.systemconfig/x86_64/stable - - - - /usr/lib/linuxlive/firefox\n" in tmpfiles
    # Only bookmarks: no home page policy.
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(firefox={"bookmarks": ff["bookmarks"]}))))
    assert "Homepage" not in json.loads(text(ctx, "rootfs/usr/lib/linuxlive/firefox/policies/policies.json"))["policies"]


@pytest.mark.parametrize("base", ["ubuntu", "fedora"])
def test_kiosk_mode(workspace, base):
    spec = load_spec(write_spec(workspace, spec_data(base=base, kiosk={"app": "firefox"}, settings={"keyboard": "de"})))
    ctx, plan = build_context(spec)
    x11 = "--nosocket=wayland --socket=x11 " if base == "ubuntu" else ""  # Ubuntu's Cage 0.2.1: see catalog kiosk_x11
    assert text(ctx, "rootfs/usr/libexec/linuxlive/kiosk-app").endswith(f"exec flatpak run {x11}org.mozilla.firefox --kiosk\n")
    assert ctx.files()["rootfs/usr/libexec/linuxlive/kiosk-app"][1] == 0o755
    unit = text(ctx, "rootfs/usr/lib/systemd/system/linuxlive-kiosk.service")
    for line in (f"User={KIOSK_USER}", "PAMName=login", "TTYPath=/dev/tty1", "ConditionKernelCommandLine=!linuxlive.install",
                 "Environment=XDG_SESSION_TYPE=wayland", "Environment=XKB_DEFAULT_LAYOUT=de", "Environment=WLR_RENDERER_ALLOW_SOFTWARE=1",
                 "ExecStart=/usr/bin/cage -d -s -- /usr/libexec/linuxlive/kiosk-app", "WantedBy=graphical.target",
                 "ExecStartPre=-+/usr/bin/plymouth quit", "Conflicts=plymouth-quit.service"):
        assert line in unit.splitlines(), line
    # Never after plymouth-quit-wait: with the login screen skipped, nothing would end it.
    assert not [line for line in unit.splitlines() if "plymouth-quit-wait" in line and not line.startswith("#")]
    for dm in ("gdm", "sddm", "plasmalogin", "lightdm"):
        assert "ConditionKernelCommandLine=linuxlive.install" in text(ctx, f"rootfs/usr/lib/systemd/system/{dm}.service.d/linuxlive-kiosk.conf")
    assert f"u {KIOSK_USER} - " in text(ctx, "rootfs/usr/lib/sysusers.d/linuxlive-kiosk.conf")
    assert f"d /var/lib/{KIOSK_USER} 0700 {KIOSK_USER} {KIOSK_USER}" in text(ctx, "rootfs/usr/lib/tmpfiles.d/linuxlive-kiosk.conf")
    packages = {"ubuntu": "cage xwayland", "fedora": "cage xorg-x11-server-Xwayland"}[base]
    assert packages in text(ctx, "Containerfile")
    branding = env(ctx, "rootfs/usr/lib/linuxlive/branding.env")
    assert branding["LL_KIOSK_APP"] == "org.mozilla.firefox" and branding["LL_KIOSK_USER"] == KIOSK_USER


def test_kiosk_apps_without_kiosk_args_run_plain(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data(base="fedora", desktop="kde", apps=["gcompris"], kiosk={"app": "gcompris"}))))
    assert text(ctx, "rootfs/usr/libexec/linuxlive/kiosk-app").endswith("exec flatpak run org.kde.gcompris\n")


def test_finalize_applies_every_setting(workspace):
    ctx, _ = build_context(load_spec(write_spec(workspace, spec_data())))
    finalize = text(ctx, "rootfs/usr/libexec/linuxlive/finalize")
    for call in ("apply_language", "apply_keyboard", "apply_timezone", 'hide_menu_entry "$entry"', "apply_kiosk"):
        assert call in finalize
    # After the icon cache, before the desktop's branding compiles the GSettings overrides.
    assert finalize.index("\napply_settings\n") < finalize.index('case "$LL_DESKTOP" in\ngnome) brand_gnome')


def test_the_installer_offers_the_spec_defaults():
    installer = (ROOT / "linuxlive/data/rootfs/common/usr/libexec/linuxlive/installer").read_text(encoding="utf-8")
    assert "Gtk.Entry(text=self.default_hostname)" in installer
    assert 'Gtk.CheckButton(label="Log in automatically", active=self.default_autologin)' in installer
    for base in ("ubuntu", "fedora"):
        backend = (ROOT / f"linuxlive/data/rootfs/{base}/usr/libexec/linuxlive/install-to-disk").read_text(encoding="utf-8")
        assert "autologin=${LL_AUTOLOGIN_DEFAULT:-0}" in backend and "--no-autologin) autologin=0" in backend


def test_boottest_can_leave_autologin_to_the_spec(tmp_path, monkeypatch):
    import scripts.boottest as boottest

    monkeypatch.setattr(boottest, "first_existing", lambda paths: paths[0])  # no OVMF needed to build the command
    monkeypatch.setattr(boottest.shutil, "copyfile", lambda src, dst: dst)

    cmd = boottest.qemu_command(Path("x.iso"), "install", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 2, False,
                                disk=tmp_path / "d.qcow2", autoinstall_autologin="spec")
    (settings,) = [a for a in cmd if a.startswith("name=opt/linuxlive/autoinstall,")]
    assert settings.endswith(";autologin=spec")
    cmd = boottest.qemu_command(Path("x.iso"), "install", tmp_path, tmp_path / "s", tmp_path / "q", 4096, 2, False, disk=tmp_path / "d.qcow2")
    assert not [a for a in cmd if "autologin=" in a]


# ---- verify-results --------------------------------------------------------------

SHELLS = {"gnome": "gnome-shell", "kde": "plasmashell", "cinnamon": "cinnamon"}


def busy_png(path: Path) -> None:
    """A screenshot with something on it: many colours, none filling the screen."""
    w, h = 160, 100
    data = bytearray()
    for y in range(h):
        for x in range(w):
            data += bytes(((x * 7) % 256, (y * 11) % 256, ((x + y) * 5) % 256))
    write_png(path, Image(w, h, bytes(data)))


def example_results(root: Path, name: str, persistence: bool = True) -> tuple[object, Path]:
    """Good results for one of the settings examples, from the TestOS fixture."""
    spec = load_spec(EXAMPLES / f"{name}.yaml")
    from linuxlive.compiler.plan import plan_build

    plan = plan_build(spec, load_catalog())
    results = make_results(root, spec.base)
    s = spec.settings
    kiosk = spec.kiosk is not None
    shell = "cage" if kiosk else SHELLS[spec.desktop]
    pretty = f"{spec.name} {spec.version} (based on {plan.base.label})"

    def edit(path: Path, change) -> None:
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data))

    edit(results / "iso.json", lambda d: d.update(volume_id=plan.volid))
    for result in results.glob("modes/*/result.json"):
        mode = result.parent.name
        installed = mode.startswith("installed")

        def change(d, installed=installed, mode=mode):
            f = d["facts"]
            if not f:
                return
            f.update(os_name=[spec.name], os_pretty_name=[pretty], image_id=[spec.id], image_version=[spec.version],
                     flatpak=[a.flatpak for a in plan.apps], hostname=[plan.hostname])
            if spec.base == "ubuntu":
                f["issue"] = [f"{spec.name} {spec.version} \\n \\l"]
            for key in ("extra_package", "extra_file", "user_build", "first_login_script", "first_boot_script", "kde_launcher_icon", "plasma_welcome_installer"):
                f.pop(key, None)
            if spec.desktop == "cinnamon":
                f["printer_commands_missing"] = ["none"]
            if d.get("outcome") != "session":
                return
            f.update(shell=[shell], shell_after_settle=[shell], shell_watch=[shell], flatpak_runs=[f"{a.flatpak}=ok" for a in plan.apps])
            if spec.desktop == "kde" and not kiosk:
                f.update(kde_launcher_icon=["linuxlive-logo"], plasma_welcome_installer=["none"])
            if s.language:
                f["system_locale"] = [f"{s.language}.UTF-8"]
                f["session_lang"] = [f"{s.language}.UTF-8"]
                f["shell_translation"] = ["none" if kiosk else f"/usr/share/locale/{s.language[:2]}/LC_MESSAGES/{shell}.mo"]
            if s.timezone:
                f["timezone"] = [s.timezone]
            if s.keyboard:
                f["keyboard_system"] = [s.keyboard]
                f["keyboard_session"] = [s.keyboard]
            f["hidden_app"] = [f"{name}=hidden" for name in s.hidden_apps]
            policies = firefox_policies_text(plan)
            if policies:
                f["firefox_policies"] = [hashlib.sha256(policies.encode()).hexdigest()]
            f["desktop_shells"] = ["none" if kiosk else SHELLS[spec.desktop]]
            if kiosk:
                f["session_user"] = [KIOSK_USER]
                f["kiosk_apps"] = [plan.kiosk_app.flatpak]
                if spec.firefox:
                    f["firefox_open"] = [spec.firefox.homepage]
                    f["firefox_bookmark"] = [b.url for b in spec.firefox.bookmarks]
            if installed and s.autologin:
                f["autologin_users"] = ["tester"]

        edit(result, change)
    if kiosk:
        busy_png(results / "modes" / SESSION_MODE / "screenshot-1.png")
    if spec.persistence and persistence:
        persist_results(results)
    return spec, results


def persist_results(results: Path) -> None:
    """Good persistence boots: the second sees the first's markers."""
    live = json.loads((results / "modes" / "uefi-usb" / "result.json").read_text())
    for mode, boot_id, seen in (("persist-1", "aaa-1", "none"), ("persist-2", "bbb-2", "aaa-1")):
        data = json.loads(json.dumps(live))
        data["mode"] = mode
        data["facts"].update(cmdline=["BOOT_IMAGE=/casper/vmlinuz boot=casper persistent quiet splash ---"], boot_id=[boot_id],
                             persistence_partition=["/dev/sda3"], persistence_seen=[seen], persistence_home_seen=[seen])
        (results / "modes" / mode).mkdir(parents=True)
        (results / "modes" / mode / "result.json").write_text(json.dumps(data))



@pytest.mark.parametrize("name", SETTINGS_EXAMPLES)
def test_settings_examples_good_results_pass(tmp_path, name):
    spec, results = example_results(tmp_path / "r", name)
    outcomes = verify_results(spec, results, expect_commit=COMMIT)
    assert failures(outcomes) == []
    details = " ".join(o.detail for o in outcomes)
    if spec.settings.language:
        assert f"the session runs in '{spec.settings.language}.UTF-8'" in details
    if spec.kiosk:
        assert "kiosk mode runs no desktop shell" in details and "the kiosk's screen shows something" in details


def _fact(results: Path, mode: str, key: str, value) -> None:
    path = results / "modes" / mode / "result.json"
    data = json.loads(path.read_text())
    data["facts"][key] = value
    path.write_text(json.dumps(data))


@pytest.mark.parametrize(
    ("name", "mode", "key", "value", "expected"),
    [
        ("settings-ubuntu", SESSION_MODE, "session_lang", ["en_US.UTF-8"], "the session runs in 'en_US.UTF-8', expected 'es_ES.UTF-8'"),
        ("settings-ubuntu", SESSION_MODE, "system_locale", ["C.UTF-8"], "system locale is 'C.UTF-8'"),
        ("settings-ubuntu", SESSION_MODE, "shell_translation", ["missing"], "the desktop shell has its words in es_ES"),
        ("settings-ubuntu", SESSION_MODE, "keyboard_session", ["us"], "the session types with layout 'us', expected 'es'"),
        ("settings-ubuntu", SESSION_MODE, "keyboard_system", ["us"], "system keyboard layout is 'us'"),
        ("settings-ubuntu", SESSION_MODE, "timezone", ["Etc/UTC"], "time zone is 'Etc/UTC', expected 'Europe/Madrid'"),
        ("settings-ubuntu", SESSION_MODE, "hostname", ["borne-linux"], "computer name is 'borne-linux', expected 'aula-1'"),
        ("settings-ubuntu", SESSION_MODE, "hidden_app", ["org.gnome.Yelp=shown", "org.gnome.Ptyxis=hidden"], "menu entry org.gnome.Yelp is not shown ('shown')"),
        ("settings-ubuntu", SESSION_MODE, "firefox_policies", ["0" * 64], "Firefox's system policies are the spec's"),
        ("settings-ubuntu", "installed-uefi-sb", "autologin_users", ["none"], "the spec's automatic login is on for 'tester'"),
        ("settings-ubuntu", "installed-uefi-sb", "session_lang", ["en_US.UTF-8"], "installed-uefi-sb: the session runs in 'en_US.UTF-8'"),
        ("settings-fedora", SESSION_MODE, "keyboard_session", ["us"], "the session types with layout 'us', expected 'de'"),
        ("settings-cinnamon", SESSION_MODE, "keyboard_session", [""], "the session types with layout '', expected 'br'"),
        (
            "settings-cinnamon", SESSION_MODE, "printer_commands_missing", ["lpstat system-config-printer"],
            "programs Cinnamon's printers applet runs, missing: 'lpstat system-config-printer', expected 'none'",
        ),
        ("settings-cinnamon", SESSION_MODE, "printer_commands_missing", [], "programs Cinnamon's printers applet runs, missing: None"),
        ("kiosk-ubuntu", SESSION_MODE, "desktop_shells", ["gnome-shell"], "kiosk mode runs no desktop shell (running: 'gnome-shell')"),
        ("kiosk-ubuntu", SESSION_MODE, "kiosk_apps", ["none"], "kiosk mode shows org.mozilla.firefox"),
        ("kiosk-ubuntu", SESSION_MODE, "shell", ["gnome-shell"], "desktop shell is 'gnome-shell', expected 'cage'"),
        ("kiosk-ubuntu", SESSION_MODE, "session_user", ["live"], f"kiosk mode runs as {KIOSK_USER}"),
        ("kiosk-ubuntu", SESSION_MODE, "firefox_open", ["about:welcome"], "Firefox opened the home page https://linuxlive.com/"),
        ("kiosk-ubuntu", SESSION_MODE, "firefox_bookmark", ["https://linuxlive.com/library/"], "Firefox has the bookmark Learn"),
        ("kiosk-ubuntu", "installed-uefi-sb", "session_user", ["tester"], f"installed-uefi-sb: logged in as '{KIOSK_USER}'"),
        ("kiosk-fedora", SESSION_MODE, "kiosk_apps", ["org.mozilla.firefox"], "kiosk mode shows org.kde.gcompris"),
    ],
)
def test_each_settings_break_is_caught(tmp_path, name, mode, key, value, expected):
    spec, results = example_results(tmp_path / "r", name)
    _fact(results, mode, key, value)
    found = failures(verify_results(spec, results, expect_commit=COMMIT))
    assert any(expected in f for f in found), found


def test_a_blank_kiosk_screen_fails(tmp_path):
    from tests.conftest import solid_png

    spec, results = example_results(tmp_path / "r", "kiosk-fedora")
    solid_png(results / "modes" / SESSION_MODE / "screenshot-1.png", 160, 100, (255, 255, 255))
    found = failures(verify_results(spec, results, expect_commit=COMMIT))
    assert any("the kiosk's screen shows something" in f and "100% one colour" in f for f in found), found
