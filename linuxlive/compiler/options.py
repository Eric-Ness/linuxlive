"""Settings and no-code options: language, keyboard, time zone, computer name,
automatic login, hidden menu entries, Firefox's policies and kiosk mode.

All of them are data. The compiler turns them into settings files and a few
values in branding.env; finalize (usr/libexec/linuxlive/finalize) applies the
ones that need the finished image, such as generating the locale. Nothing here
runs code a spec brings, so a spec with only these is still one linuxlive.com
builds for people ("Build it for me").
"""

from __future__ import annotations

import json

from linuxlive.compiler.context import BuildContext
from linuxlive.compiler.plan import Plan

# Flathub's Firefox reads system policies from this unmanaged Flatpak
# extension (Mozilla bug 1814897). It lives in /var, so a tmpfiles entry
# links it to the copy in the image at every boot, live or installed.
FIREFOX_EXTENSION = "/var/lib/flatpak/extension/org.mozilla.firefox.systemconfig"
FIREFOX_SYSTEMCONFIG = "/usr/lib/linuxlive/firefox"
KIOSK_USER = "linuxlive-kiosk"
KIOSK_HOME = f"/var/lib/{KIOSK_USER}"
# Display managers kiosk mode keeps from starting (only the installer's boot
# entry, linuxlive.install, starts one).
DISPLAY_MANAGERS = ("gdm.service", "sddm.service", "plasmalogin.service", "lightdm.service")


def uses_options(plan: Plan) -> bool:
    s = plan.spec
    o = s.settings
    return bool(o.language or o.keyboard or o.timezone or o.hostname or o.autologin or o.hidden_apps or s.firefox or s.kiosk)


def language_packages(plan: Plan) -> list[str]:
    """The base's packages for the spec's language: locale data and translations."""
    lang = plan.language
    if lang is None:
        return []
    if plan.base.id == "ubuntu":
        names = [f"language-pack-{lang.ubuntu}"]
        if plan.desktop.id in ("gnome", "cinnamon"):
            names.append(f"language-pack-gnome-{lang.ubuntu}")
        if plan.desktop.id == "kde":
            names.append(f"language-pack-kde-{lang.ubuntu}")
        if plan.desktop.id == "cinnamon":
            names.append("cinnamon-l10n")
        return names + list(lang.ubuntu_fonts)
    return [f"langpacks-{lang.fedora}"]


def option_packages(plan: Plan) -> list[str]:
    names = language_packages(plan)
    if plan.spec.kiosk:
        names += [n for n in plan.kiosk_packages if n not in names]
    return names


def packages_step(plan: Plan) -> str:
    """The Containerfile step installing what the options need, or nothing."""
    names = option_packages(plan)
    if not names:
        return ""
    if plan.base.id == "ubuntu":
        run = ("RUN set -eux; \\\n    apt-get update; \\\n    apt-get install -y " + " ".join(names)
               + "; \\\n    apt-get clean; \\\n    rm -rf /var/lib/apt/lists/*")
    else:
        run = f"RUN set -eux; \\\n    dnf -y install {' '.join(names)}; \\\n    dnf clean all"
    return f"\n# Settings: the language's translations{' and kiosk mode' if plan.spec.kiosk else ''}.\n{run}\n"


def firefox_policies(plan: Plan) -> dict | None:
    ff = plan.spec.firefox
    if ff is None:
        return None
    policies: dict = {}
    if ff.homepage:
        policies["Homepage"] = {"URL": ff.homepage, "StartPage": "homepage", "Locked": False}
        # Start on the home page, not Firefox's welcome or what's-new pages.
        policies["OverrideFirstRunPage"] = ""
        policies["OverridePostUpdatePage"] = ""
    if ff.bookmarks:
        policies["Bookmarks"] = [{"Title": b.name, "URL": b.url, "Placement": "toolbar"} for b in ff.bookmarks]
        policies["DisplayBookmarksToolbar"] = "always"
    policies["DontCheckDefaultBrowser"] = True
    return {"policies": policies}


def firefox_policies_text(plan: Plan) -> str | None:
    policies = firefox_policies(plan)
    return None if policies is None else json.dumps(policies, indent=2, ensure_ascii=False) + "\n"


def kiosk_command(plan: Plan) -> list[str]:
    app = plan.kiosk_app
    return ["flatpak", "run", app.flatpak, *app.kiosk_args]


def add_options(ctx: BuildContext, plan: Plan) -> None:
    spec = plan.spec
    if plan.keyboard and plan.desktop.id == "kde":
        # Plasma reads its layouts from kxkbrc; without one it ignores the system's.
        ctx.add_text("rootfs/etc/xdg/kxkbrc", f"[Layout]\nLayoutList={plan.keyboard}\nUse=true\n")
    policies = firefox_policies_text(plan)
    if policies is not None:
        ctx.add_text(f"rootfs{FIREFOX_SYSTEMCONFIG}/policies/policies.json", policies)
        ctx.add_text(
            "rootfs/usr/lib/tmpfiles.d/linuxlive-firefox.conf",
            "# Firefox's system policies (the spec's firefox), for Flathub's Firefox.\n"
            f"L+ {FIREFOX_EXTENSION}/{plan.flatpak_arch}/stable - - - - {FIREFOX_SYSTEMCONFIG}\n",
        )
    if spec.kiosk:
        add_kiosk(ctx, plan)


def add_kiosk(ctx: BuildContext, plan: Plan) -> None:
    app = plan.kiosk_app
    command = " ".join(kiosk_command(plan))
    ctx.add_text("rootfs/usr/libexec/linuxlive/kiosk-app", "\n".join([
        "#!/bin/sh",
        f"# Kiosk mode: {app.name}, full screen in Cage (linuxlive-kiosk.service).",
        f"exec {command}",
    ]), mode=0o755)
    ctx.add_text("rootfs/usr/lib/sysusers.d/linuxlive-kiosk.conf",
                 f'# The user kiosk mode runs {app.name} as.\nu {KIOSK_USER} - "Kiosk" {KIOSK_HOME} /bin/sh\n')
    ctx.add_text("rootfs/usr/lib/tmpfiles.d/linuxlive-kiosk.conf",
                 f"# The kiosk user's home, where {app.name} keeps its settings.\nd {KIOSK_HOME} 0700 {KIOSK_USER} {KIOSK_USER} - -\n")
    ctx.add_text("rootfs/usr/lib/systemd/system/linuxlive-kiosk.service", f"""[Unit]
Description=Kiosk: {app.name} full screen
Documentation=https://github.com/Eric-Ness/linuxlive
# The installer's boot entry starts the desktop instead, so the installer can run.
ConditionKernelCommandLine=!linuxlive.install
After=systemd-user-sessions.service plymouth-quit-wait.service systemd-logind.service dbus.socket
Wants=dbus.socket systemd-logind.service
Conflicts=getty@tty1.service
After=getty@tty1.service

[Service]
User={KIOSK_USER}
PAMName=login
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_SESSION_TYPE=wayland
{f"Environment=XKB_DEFAULT_LAYOUT={plan.keyboard}" + chr(10) if plan.keyboard else ""}# -d: no window decorations; -s: the consoles stay reachable (Ctrl+Alt+F2).
ExecStart=/usr/bin/cage -d -s -- /usr/libexec/linuxlive/kiosk-app
Restart=always
RestartSec=2

[Install]
WantedBy=graphical.target
""")
    for unit in DISPLAY_MANAGERS:
        ctx.add_text(f"rootfs/usr/lib/systemd/system/{unit}.d/linuxlive-kiosk.conf",
                     "# Kiosk mode: no login screen, except from the installer's boot entry.\n"
                     "[Unit]\nConditionKernelCommandLine=linuxlive.install\n")


def branding_values(plan: Plan) -> dict[str, str]:
    """The options' values in branding.env, for finalize, the installer and the boot check."""
    s = plan.spec.settings
    return {
        "LL_HOSTNAME": plan.hostname,
        "LL_LANGUAGE": s.language or "",
        "LL_KEYBOARD": plan.keyboard or "",
        "LL_TIMEZONE": s.timezone or "",
        "LL_AUTOLOGIN_DEFAULT": "1" if s.autologin else "0",
        "LL_HIDDEN_APPS": " ".join(s.hidden_apps),
        "LL_KIOSK_APP": plan.kiosk_app.flatpak if plan.kiosk_app else "",
        "LL_KIOSK_USER": KIOSK_USER if plan.kiosk_app else "",
    }


def as_dict(plan: Plan) -> dict:
    spec = plan.spec
    s = spec.settings
    out: dict = {
        "language": s.language,
        "keyboard": s.keyboard,
        "timezone": s.timezone,
        "hostname": plan.hostname,
        "autologin": s.autologin,
        "hidden_apps": list(s.hidden_apps),
        "packages": option_packages(plan),
    }
    if spec.firefox:
        out["firefox"] = firefox_policies(plan)
    if plan.kiosk_app:
        out["kiosk"] = {"app": plan.kiosk_app.id, "flatpak": plan.kiosk_app.flatpak, "command": kiosk_command(plan), "user": KIOSK_USER}
    return out
