"""Any Flathub app: the spec's flathub list, and the check against Flathub's API."""

from __future__ import annotations

import json
import shlex

import pytest

from linuxlive import cli, flathub
from linuxlive.compiler import build_context
from linuxlive.spec import SpecError, load_spec
from tests.conftest import spec_data, write_spec

FAKE = {
    "/appstream/org.gnome.Chess": {"id": "org.gnome.Chess", "name": "GNOME Chess", "type": "desktop-application"},
    "/summary/org.gnome.Chess": {"arches": ["aarch64", "x86_64"], "metadata": {"runtimeIsEol": False}},
    "/verification/org.gnome.Chess/status": {"verified": True},
    "/appstream/com.example.OnlyArm": {"id": "com.example.OnlyArm", "name": "Only Arm", "type": "desktop-application"},
    "/summary/com.example.OnlyArm": {"arches": ["aarch64"]},
    "/verification/com.example.OnlyArm/status": {"verified": False},
    "/appstream/org.freedesktop.Platform": {"id": "org.freedesktop.Platform", "name": "Freedesktop Platform", "type": "runtime"},
    "/summary/org.freedesktop.Platform": {"arches": ["x86_64"]},
    "/verification/org.freedesktop.Platform/status": {"verified": True},
}


@pytest.fixture
def fake_flathub(monkeypatch):
    asked = []

    def get(path, attempts=4):
        asked.append(path)
        return FAKE.get(path)

    monkeypatch.setattr(flathub, "_get", get)
    return asked


def test_flathub_apps_load_and_compile(workspace):
    spec = load_spec(write_spec(workspace, spec_data(flathub=["org.gnome.Chess", "com.spotify.Client"])))
    assert spec.flathub == ("org.gnome.Chess", "com.spotify.Client")
    ctx, plan = build_context(spec)
    assert [a.flatpak for a in plan.apps] == ["org.mozilla.firefox", "org.videolan.VLC", "org.gnome.Chess", "com.spotify.Client"]
    env = {}
    for line in ctx.files()["iso.env"][0].decode().splitlines():
        if line and not line.startswith("#"):
            key, _, raw = line.partition("=")
            env[key] = (shlex.split(raw) or [""])[0]
    assert env["LL_FLATPAKS"] == "org.mozilla.firefox org.videolan.VLC org.gnome.Chess com.spotify.Client"
    resolved = json.loads(ctx.files()["linuxlive.json"][0])
    assert {"id": "org.gnome.Chess", "name": "org.gnome.Chess", "flatpak": "org.gnome.Chess"} in resolved["apps"]
    # Flathub apps are data: Build it for me still builds the spec.
    assert not spec.runs_own_code


def test_a_flathub_app_can_be_the_kiosk(workspace):
    ctx, plan = build_context(load_spec(write_spec(workspace, spec_data(flathub=["org.gnome.Chess"], kiosk={"app": "org.gnome.Chess"}))))
    assert plan.kiosk_app.flatpak == "org.gnome.Chess"
    # On Ubuntu through Xwayland (catalog kiosk_x11).
    assert ctx.files()["rootfs/usr/libexec/linuxlive/kiosk-app"][0].decode().endswith("exec flatpak run --nosocket=wayland --socket=x11 org.gnome.Chess\n")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (["chess"], "flathub/0: 'chess' does not match"),
        (["org/../etc"], "does not match"),
        (["org.mozilla.firefox"], "flathub/0: org.mozilla.firefox is in the catalog; list it in apps as 'firefox'"),
        (["org.gnome.Chess", "org.gnome.Chess"], "has non-unique elements"),
        ([f"org.example.App{i}" for i in range(51)], "is too long"),
    ],
)
def test_bad_flathub_lists_are_refused(workspace, value, message):
    with pytest.raises(SpecError) as err:
        load_spec(write_spec(workspace, spec_data(flathub=value)))
    assert any(message in p for p in err.value.problems), err.value.problems


def test_the_kiosk_app_must_be_listed(workspace):
    with pytest.raises(SpecError) as err:
        load_spec(write_spec(workspace, spec_data(kiosk={"app": "org.gnome.Chess"})))
    assert any("must be one of the spec's apps or flathub apps" in p for p in err.value.problems)


def test_flathub_check(fake_flathub):
    problems, found = flathub.check_apps(("org.gnome.Chess", "org.example.Nope", "com.example.OnlyArm", "org.freedesktop.Platform"))
    assert [a.id for a in found] == ["org.gnome.Chess", "com.example.OnlyArm", "org.freedesktop.Platform"]
    assert found[0].verified and found[0].name == "GNOME Chess"
    assert problems == [
        "flathub/1: Flathub has no app 'org.example.Nope'",
        "flathub/2: com.example.OnlyArm is not built for x86_64 on Flathub (only aarch64)",
        "flathub/3: org.freedesktop.Platform is a runtime on Flathub, not an app",
    ]
    assert flathub.check_apps(("com.example.OnlyArm",), arch="aarch64")[0] == []


def test_validate_online_asks_flathub(workspace, fake_flathub, capsys):
    good = write_spec(workspace, spec_data(flathub=["org.gnome.Chess"]))
    assert cli.main(["validate", "--online", str(good)]) == 0
    out = capsys.readouterr().out
    assert "flathub: org.gnome.Chess (GNOME Chess) verified, aarch64, x86_64" in out and "1 from Flathub" in out
    bad = write_spec(workspace, spec_data(flathub=["org.example.Nope"]), "bad.yaml")
    assert cli.main(["validate", "--online", str(bad)]) == 2
    assert "Flathub has no app 'org.example.Nope'" in capsys.readouterr().err
    # Offline, only the IDs' shape is checked: no request at all.
    fake_flathub.clear()
    assert cli.main(["validate", str(bad)]) == 0
    assert fake_flathub == []


def test_an_unreachable_flathub_is_an_error_not_a_pass(workspace, monkeypatch, capsys):
    def down(path, attempts=4):
        raise flathub.FlathubError(f"Flathub's API did not answer for {path} (timed out)")

    monkeypatch.setattr(flathub, "_get", down)
    spec = write_spec(workspace, spec_data(flathub=["org.gnome.Chess"]))
    assert cli.main(["validate", "--online", str(spec)]) == 2
    assert "did not answer" in capsys.readouterr().err


def test_an_app_on_an_end_of_life_runtime_is_flagged(workspace, monkeypatch, capsys):
    fake = {
        "/appstream/org.example.Old": {"id": "org.example.Old", "name": "Old", "type": "desktop-application"},
        "/summary/org.example.Old": {"arches": ["x86_64"], "metadata": {"runtimeIsEol": True}},
        "/verification/org.example.Old/status": {"verified": True},
    }
    monkeypatch.setattr(flathub, "_get", lambda path, attempts=4: fake.get(path))
    assert flathub.lookup("org.example.Old").eol
    spec = write_spec(workspace, spec_data(flathub=["org.example.Old"]))
    assert cli.main(["validate", "--online", str(spec)]) == 0
    assert "warning: org.example.Old is built on a runtime that gets no more updates" in capsys.readouterr().out
