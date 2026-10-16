# linuxlive

Build your own Linux distro from a short spec file. Pick a base, a desktop,
apps and branding; linuxlive compiles that into a container build and turns
the result into a live ISO that boots on BIOS and UEFI machines, Secure Boot
included, from a DVD or a USB stick.

This is the open-source build pipeline behind linuxlive.com. It works end to
end for two bases, Ubuntu and Fedora, with three desktops.

**To make your own distro, you don't need this repository.** Start from the
template [Eric-Ness/linuxlive-distro](https://github.com/Eric-Ness/linuxlive-distro):
click **Use this template**, edit `linuxlive.yaml`, swap in your art and push.
GitHub Actions builds the ISO, boots it five ways and checks it, through this
repository's reusable workflow. The
[wiki](https://github.com/Eric-Ness/linuxlive/wiki) walks through it.

## A spec

```yaml
schema_version: 1
name: TestOS            # shown in the boot menu, About screen and os-release
id: testos              # optional; derived from name
version: "0.1"
base: ubuntu            # ubuntu (26.04 LTS) or fedora (44)
desktop: gnome          # gnome, kde or cinnamon
apps: [firefox, vlc]    # catalog IDs, installed from Flathub
flathub: [org.gnome.Chess]  # any other Flathub app, by its ID (since v0.6.0)
branding:
  wallpaper: assets/wallpaper.png   # PNG or JPEG, at least 1280x720
  logo: assets/logo.png             # square PNG: 128, 256 or 512 px
  accent_color: "#c2185b"           # optional
# Optional extras (since v0.4.0): the distro's own additions.
packages: [htop]                    # from the base's own archive
files:
  - {src: files/motd, dest: /etc/motd}   # under /etc or /usr
scripts:
  build: [scripts/build.sh]              # root, in the image build
  first-boot: [scripts/first-boot.sh]    # root, once, on an installed system
  first-login: [scripts/first-login.sh]  # each user, once
repos:                                   # GitHub code at an exact commit
  - url: https://github.com/owner/name
    commit: 3f2a9c1e5b7d4e8a9c217d4e5f6a8b904c1d2e3f
    run: make install PREFIX=/usr
# Optional settings (since v0.5.0): data only, nothing that runs.
settings:
  language: de_DE                   # a catalog locale: translations included
  keyboard: de                      # a catalog XKB layout
  timezone: Europe/Berlin
  hostname: lab-pc                  # defaults to the id
  autologin: true                   # installed systems log straight in
  hidden_apps: [yelp]               # menu entries to hide (desktop file names)
firefox:                            # needs firefox in apps
  homepage: https://example.org/
  bookmarks:
    - {name: Example, url: https://example.org/}
kiosk:
  app: firefox                      # one of apps: starts full screen, no desktop
```

Apps in `flathub` are checked against Flathub's API by `validate --online`,
which the build workflow runs first: each must exist, be an app (not a
runtime) and be built for the ISO's CPU. Flathub's verified mark is shown,
not required. A Flathub app can be the kiosk app too.

Settings, Firefox's policies and kiosk mode are data, so a spec with only
those (no scripts or repos) is one linuxlive.com's "Build it for me" builds.
Kiosk mode runs the app in [Cage](https://github.com/cage-kiosk/cage) on the
first console as the user `linuxlive-kiosk`; the boot menu's Install entry
still starts the desktop, so the installer works. Hidden menu entries stay
installed: on Ubuntu the package's file is diverted, so upgrades keep it
hidden. Firefox's policies go where Flathub's Firefox reads them
(`org.mozilla.firefox.systemconfig`).

Extras run before linuxlive's own last build step (finalize), so branding,
the installer and the boot checks still come last; CI checks every extra in
the live session and on the installed disk.

The full format is the JSON Schema in
[linuxlive/data/spec.schema.json](linuxlive/data/spec.schema.json), so a web
form can validate against the same file. Apps, desktops and base images come
from [linuxlive/data/catalog.yaml](linuxlive/data/catalog.yaml).

```bash
python -m linuxlive validate examples/testos-ubuntu.yaml
```

## How a spec becomes an ISO

1. **Compile.** `python -m linuxlive compile SPEC -o build/ctx` writes a build
   context: a Containerfile, a `rootfs/` overlay with the branding, GRUB
   configs and `iso.env`. Output is deterministic, so image layers cache well.
2. **Build the image.** `podman build build/ctx`. Ubuntu starts from the
   `ubuntu:26.04` container image and installs the desktop with apt. Fedora
   starts from the official Fedora Atomic image for the desktop
   (Silverblue for GNOME, Kinoite for KDE), so the result is also a bootc
   image that installed systems can update from. Cinnamon has no Atomic
   image, so it is installed with dnf onto `base-atomic`, the image
   Silverblue and Kinoite are themselves built from.
3. **Assemble the ISO.** `scripts/assemble-iso.sh` exports the image's
   filesystem, preinstalls the Flatpak apps, labels it for SELinux on Fedora,
   compresses it to squashfs and writes a hybrid ISO: El Torito for optical
   media, MBR and GPT for USB sticks, the base's own signed shim and GRUB for
   Secure Boot. Ubuntu boots live through casper, Fedora through dracut's
   dmsquash-live. Ubuntu ISOs also carry `md5sum.txt` for casper's medium
   check.
4. **Boot it.** `scripts/boottest.py` boots the ISO in QEMU. Every image
   ships a small service, `linuxlive-bootcheck`, that reports to the serial
   port when (and only when) QEMU passes a test flag: OS name, firmware,
   Secure Boot state, installed Flatpaks, boot splash, desktop shell, failed
   system services and update notifiers.
5. **Verify.** `python -m linuxlive verify-results` checks those reports and
   the screenshots against the spec. A run that boots the wrong name, the
   upstream boot splash, a failed system service or a desktop without the
   spec's wallpaper fails.

When booted from the ISO, a live-only service, `linuxlive-live`, turns off
update notifiers (nothing installed in a live session survives shutdown) and
stops KDE's welcome window offering an installer the image doesn't have.

Branding covers the OS name in os-release, `IMAGE_ID` and `IMAGE_VERSION`, the
wallpaper (desktop, lock screen and, on KDE and Cinnamon, the login screen),
the Plymouth boot splash built from the logo (and, on Ubuntu, its text-mode
fallback), the GRUB menu, the accent colour on GNOME and KDE, the login logo
on GNOME and the menu button on Cinnamon. Cinnamon has no accent setting; its
theme fixes the colours. ID and VERSION_ID stay upstream's so package tools
keep working.

## Examples

| Spec | Distro | Base | Desktop |
| --- | --- | --- | --- |
| `examples/testos-ubuntu.yaml` | TestOS | Ubuntu 26.04 | GNOME |
| `examples/testos-fedora.yaml` | TestOS | Fedora 44 | KDE Plasma |
| `examples/lawndarts-ubuntu.yaml` | Lawn Darts Linux | Ubuntu 26.04 | Cinnamon |
| `examples/lawndarts-fedora.yaml` | Lawn Darts Linux | Fedora 44 | Cinnamon |
| `examples/settings-ubuntu.yaml` | Aula Linux (Spanish, every setting) | Ubuntu 26.04 | GNOME |
| `examples/settings-fedora.yaml` | Werkbank Linux (German) | Fedora 44 | KDE Plasma |
| `examples/settings-cinnamon.yaml` | Varanda Linux (Brazilian Portuguese) | Fedora 44 | Cinnamon |
| `examples/kiosk-ubuntu.yaml` | Borne Linux (a Firefox kiosk, French) | Ubuntu 26.04 | GNOME underneath |
| `examples/kiosk-fedora.yaml` | Gioco Linux (a GCompris kiosk, Italian) | Fedora 44 | KDE underneath |

CI builds and boots all nine; it publishes only TestOS and Lawn Darts, which
the site offers for download. The other five prove the settings in running
systems: the session's language and translations, the layout each desktop
types with, the time zone, hidden entries, Firefox's open page and bookmarks,
and automatic login on the installed disk.

The TestOS art is drawn by `scripts/make_test_assets.py`. The Lawn Darts
wallpaper is a public domain photo of a lawn with an orange drain grate set
into it, and its logo is a lawn dart in a target ring; both come from
`scripts/make_lawndarts_assets.py`, which needs a headless Chrome or Chromium
and names its photo sources in `examples/assets/lawndarts/CREDITS.md`.

## Building locally

The build needs Linux with root, podman, `squashfs-tools`, `xorriso`,
`grub-pc-bin`, `mtools` and `dosfstools`; boot tests need `qemu-system-x86`
and `ovmf`. On Windows, use WSL2. Disk use peaks around 30 GB for Fedora.

```bash
python -m pip install -r requirements.txt
python -m linuxlive compile examples/testos-ubuntu.yaml -o build/ctx
sudo podman build -t localhost/testos build/ctx
sudo bash scripts/assemble-iso.sh build/ctx localhost/testos build/testos.iso
python scripts/boottest.py --iso build/testos.iso --mode uefi-usb --session --out build/results/modes/uefi-usb
```

## CI

[.github/workflows/spike.yml](.github/workflows/spike.yml) runs on every push
to `main`. The `unit` job runs the tests and shellcheck and compiles every
example. The `build` job then builds each example and boots it five ways:
UEFI from USB (waiting for the desktop and taking screenshots), UEFI with
Secure Boot from CD, UEFI from CD, BIOS from CD and BIOS from USB. Its
`results-<example>` artifact holds the logs, screenshots and per-boot
`result.json` files. Start the workflow by hand with `keep_iso` to download
the ISOs too.

The build job is the reusable workflow
[.github/workflows/build.yml](.github/workflows/build.yml), the same one the
template calls. spike runs it with the pipeline at its own commit; a caller
pinned to a release tag gets that release's pipeline, because the workflow's
`linuxlive-ref` input defaults to the tag. When you bump the version in
`pyproject.toml`, change that default to match (a test checks it), then tag
the release commit `v<version>`.

Commits that change only README.md do not start a run.

`scripts/gates/ci-gate.mjs` re-checks a finished run from your own machine:
it downloads that run's artifacts for the commit you have checked out and
re-runs the verifier locally, rather than trusting a green CI badge. It needs
the GitHub CLI.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The tests cover spec validation, the compiler (with golden copies of every
example build context in `tests/golden`), PNG decoding, ISO inspection, the
verifier, and the Lawn Darts art against its brief. After an intended change
to compiler output, regenerate the golden copies with
`LINUXLIVE_UPDATE_GOLDEN=1 python -m pytest tests/test_compiler.py` and
review the diff.

## Spike results

All four example specs build and boot on GitHub's hosted runners, which
expose KVM. The builds run in parallel. A run on 2026-10-11 took
39 minutes; Fedora with Cinnamon was the slowest build at 38 minutes, and
each Ubuntu build took about 17.

What the spike proved, for Ubuntu 26.04 with GNOME, Fedora 44 with KDE, and
Cinnamon on both:

- A spec compiles to a build context, podman builds it, and the assembler
  turns the image into one hybrid ISO. In that run the ISOs were
  2.93 GiB (TestOS, Ubuntu), 4.45 GiB (TestOS, Fedora), 2.72 GiB (Lawn Darts,
  Ubuntu) and 3.99 GiB (Lawn Darts, Fedora). Fedora's are larger mostly
  because of its base image and the Flatpak runtimes.
- Every ISO boots in QEMU from USB under UEFI, from CD under UEFI with Secure
  Boot, from CD under UEFI, from CD under BIOS and from USB under BIOS. With
  Secure Boot firmware the guest reports Secure Boot on, so the signed shim,
  GRUB and kernel chain from each base works.
- Booting from USB under UEFI, the desktop session is up about 30 seconds
  after QEMU starts on Ubuntu and about 90 seconds on Fedora. It reports the
  spec's OS name and IMAGE_ID, the linuxlive boot splash, and both Flatpak
  apps (Firefox and VLC). Fedora runs with SELinux enforcing. Cinnamon runs as
  an X11 session on both bases.
- Screenshots show the spec's wallpaper on all three desktops. On Fedora's
  KDE the accent colour shows too, and KDE's welcome window greets the user
  with the spec's name and logo. On KDE the logo is the application launcher,
  and on Cinnamon the menu button. Ubuntu's GNOME screenshot has nothing
  accent-coloured on screen, so its accent setting is only checked in the
  compiled output.
- Ubuntu's and Fedora's logos, which are their trademarks, are replaced.
  Fedora images swap `fedora-logos` for Fedora's own neutral `generic-logos`.
  KDE's launcher shows the spec's logo instead of Fedora's, Ubuntu's dock
  shows GNOME's grid icon instead of the Ubuntu logo for Show Apps, and
  Ubuntu's `/etc/issue` names the spec's distro. The boot check reports each
  of these and the verifier checks them.
- No system service fails in any live session, at login or 30 seconds later,
  and CI fails if one does. Run 38249801807 (v0.1.2) showed it on all four
  examples. Ubuntu's own medium check (`casper-md5check`) read the 297
  checksums in each Ubuntu ISO's `md5sum.txt` and passed. `mcelog`, which
  failed in 46 of the 51 Fedora boots CI recorded before v0.1.2, no longer
  starts in virtual machines.
- Live sessions start no update notifiers, and GNOME Software doesn't check
  for updates. On Fedora's KDE the welcome window no longer offers to start
  an installer.
- Since v0.1.3 every ISO comes with `<name>.iso.packages`: each installed
  package, its version and the source package it was built from (Ubuntu's
  `source=version`, Fedora's source RPM), so the matching source can be found
  in Ubuntu's and Fedora's archives. The boot check also watches the desktop
  shell until the test ends, and the verifier fails if it crashes late.
- Since v0.2.0 every Ubuntu-based ISO can install itself: an "Install
  <distro>" icon on the live desktop and an "Install" boot-menu entry open a
  small installer that erases one whole disk (after you type erase) and
  installs the system with your user, booting under UEFI with Secure Boot
  and under BIOS. CI installs both Ubuntu examples unattended in QEMU and
  boots the installed disks to the desktop under UEFI with Secure Boot and
  under BIOS, checking that casper and the live user are gone, the user and
  the apps are there, and no upstream first-login wizard opens.
- Since v0.3.0 Fedora-based ISOs install too, with the same icon, boot entry
  and installer. With `publish-image: true` (and `packages: write`) the build
  pushes its bootc image to ghcr.io, tagged `:latest` once verified; the
  installer runs `bootc install to-disk` from it, so installing needs the
  internet, and installed systems follow `:latest` with `bootc upgrade`. A
  first-boot service creates the user before the login screen starts. CI
  installs the Fedora examples the same way and checks the installed system
  runs the published image from xfs.
- Since v0.4.0 a spec can add its own packages, files, build, first-boot and
  first-login scripts, and GitHub repos built in at an exact commit. The
  TestOS examples use every kind; the boot check reports each one and
  `verify-results` checks them in the live session and on the installed disk.
  Repos and their commits are listed in the ISO's package list.

What it did not prove, or found broken:

- The installer only erases a whole disk: no dual boot, no encryption.
  Fedora-based ISOs install only while online, and only when their image was
  published; without it the installer says it cannot install this build.
  It has been tested in QEMU, not yet on real hardware.
- Three of the seven green Fedora KDE runs up to 2026-10-11 logged one
  SELinux denial: systemd reading a uevent socket that was opened before the
  policy loaded. It looks like a harmless early-boot race but has not been
  confirmed. Of the six Fedora Cinnamon runs whose results are still kept,
  one boot (run 38174303533) logged a denial and the rest logged none.
- An earlier Fedora Cinnamon run reported `plymouth-start.service` failed.
  It hasn't happened in the four runs since, and CI now fails if it does.
  The cause is not known.
- Fedora's initramfs is about 265 MB because one image serves both live and
  installed boots without host-only trimming.
- CI boot-tests GNOME on Ubuntu, KDE on Fedora and Cinnamon on both. GNOME on
  Fedora and KDE on Ubuntu compile and are covered by unit tests but are not
  built in CI.
- CI boots everything in QEMU. Only the TestOS Ubuntu ISO has been tried in
  VirtualBox (2026-10-10 and 2026-10-11), and nothing on real hardware yet.
- Presets, Xfce, kernel and init choice, ISO hosting, the web UI and
  try-in-browser are later work.

To get an ISO to try, start the spike workflow from the Actions tab with
`keep_iso` checked (in your own fork if you are not a maintainer). The ISOs
are kept for one day.

## Contributing

Bug reports, fixes and new catalog entries are welcome.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how to run the tests and what a
pull request needs. Report security problems privately, as described in
[SECURITY.md](SECURITY.md).

## License

linuxlive is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version (GPL-3.0-or-later). See [LICENSE](LICENSE).

The Lawn Darts example art is built from public domain (CC0) photos listed in
[CREDITS.md](examples/assets/lawndarts/CREDITS.md). The ISOs you build contain
Ubuntu or Fedora software under those projects' own licences.
