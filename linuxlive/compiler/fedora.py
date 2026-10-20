"""Fedora build contexts: a bootc (Fedora Atomic) image booted live by dracut."""

from __future__ import annotations

from linuxlive.compiler import options
from linuxlive.compiler.common import add_grub_configs, add_install_launcher, header_comment
from linuxlive.compiler.context import BuildContext
from linuxlive.compiler.extras import build_step, packages_step
from linuxlive.compiler.plan import Plan


def add_base(ctx: BuildContext, plan: Plan) -> None:
    session = plan.variant.livesys_session
    ctx.add_text(
        "rootfs/etc/sysconfig/livesys",
        header_comment(plan)
        + "# Session type for livesys, which sets up the live user when booted from the ISO.\n"
        + f'livesys_session="{session}"\n',
    )
    add_install_launcher(ctx, plan)
    add_grub_configs(ctx, plan, kernel="/live/vmlinuz", initrd="/live/initrd.img", install=True)
    ctx.add_text("Containerfile", containerfile(plan))


def containerfile(plan: Plan) -> str:
    packages = " ".join(plan.base.system_packages)
    desktop = ""
    if plan.variant.packages:
        # GNOME and KDE come with their Atomic images; other desktops are
        # installed onto a desktop-less one.
        desktop = f"""
# Desktop: {plan.desktop.label}.
RUN set -eux; \\
    dnf -y install {' '.join(plan.variant.packages)}; \\
    dnf clean all
"""
    debrand = ""
    if plan.base.replaced_packages:
        swaps = "".join(
            f"    if rpm -q --quiet {old}; then dnf -y swap {old} {new}; fi; \\\n" for old, new in plan.base.replaced_packages
        )
        debrand = f"""
# Fedora's logos are Fedora's trademarks; a distro that isn't Fedora uses
# Fedora's neutral replacements instead.
RUN set -eux; \\
{swaps}    dnf clean all
"""
    installer = ""
    if plan.base.installer_packages:
        installer = f"""
# The installer's window (usr/libexec/linuxlive/installer); the install
# itself is bootc's, from the published image (install-to-disk).
RUN set -eux; \\
    dnf -y install {' '.join(plan.base.installer_packages)}; \\
    dnf clean all
"""
    return f"""{header_comment(plan)}
# {plan.desktop.label} on Fedora Atomic. This is a bootc image: installed
# systems take updates by pulling new builds of it.
FROM {plan.base_image}

# Live boot (dmsquash-live), live session setup (livesys) and the boot splash.
RUN set -eux; \\
    dnf -y install {packages}; \\
    dnf clean all
{desktop}{debrand}{installer}{options.packages_step(plan)}{packages_step(plan)}
COPY rootfs/ /

# Keep a copy of the signed Secure Boot chain where the ISO assembler looks for it.
RUN set -eux; \\
    mkdir -p /usr/lib/linuxlive/efi; \\
    for name in shim{plan.arch.efi}.efi grub{plan.arch.efi}.efi mm{plan.arch.efi}.efi; do \\
        found=$(find /usr/lib/efi /usr/lib/bootupd/updates /boot/efi -type f -name "$name" -path '*fedora*' 2>/dev/null | sort | head -n 1); \\
        test -n "$found" || {{ echo "missing $name in the base image" >&2; exit 1; }}; \\
        cp "$found" "/usr/lib/linuxlive/efi/$name"; \\
    done
{build_step(plan)}
# Branding, linuxlive's boot check and the initramfs. LL_BUILD_ID (optional)
# names this build in os-release's BUILD_ID.
ARG LL_BUILD_ID=""
RUN /bin/sh /usr/libexec/linuxlive/finalize

RUN bootc container lint
"""
