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
branding:
  wallpaper: assets/wallpaper.png   # PNG or JPEG, at least 1280x720
  logo: assets/logo.png             # square PNG: 128, 256 or 512 px
  accent_color: "#c2185b"           # optional
```

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
   dmsquash-live.
4. **Boot it.** `scripts/boottest.py` boots the ISO in QEMU. Every image
   ships a small service, `linuxlive-bootcheck`, that reports to the serial
   port when (and only when) QEMU passes a test flag: OS name, firmware,
   Secure Boot state, installed Flatpaks, boot splash, desktop shell.
5. **Verify.** `python -m linuxlive verify-results` checks those reports and
   the screenshots against the spec. A run that boots the wrong name, the
   upstream boot splash or a desktop without the spec's wallpaper fails.

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

What it did not prove, or found broken:

- There is no installer yet; every ISO is live only. Fedora's KDE welcome
  window offers to launch an installer that the image does not contain.
- In Fedora live sessions `mcelog.service` fails because QEMU does not emulate
  machine-check hardware. Three of the seven green Fedora KDE runs so far
  logged one SELinux denial: systemd reading a uevent socket that was opened
  before the policy loaded. It looks like a harmless early-boot race but has
  not been confirmed. The one Fedora Cinnamon run so far logged none.
- Ubuntu live sessions run `casper-md5check`, which exits with an error when
  the medium has no `md5sum.txt`, and linuxlive's ISOs have none. It runs late
  at idle priority, so the boot check only sometimes sees it fail; the
  2026-10-11 run is the first to report it, on both Ubuntu builds. Writing
  `md5sum.txt` into Ubuntu ISOs would fix it.
- The Fedora Cinnamon live session reports `plymouth-start.service` failed.
  The cause is not known yet; the linuxlive splash is in its initramfs.
- The Ubuntu GNOME live session pops up a "Software updates available"
  notification. Fedora's live setup turns that off; Ubuntu's does not yet.
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
