#!/usr/bin/env bash
# Turns a built linuxlive container image into a hybrid live ISO that boots
# on BIOS and UEFI (including Secure Boot), from optical media or a USB stick.
#
#   sudo scripts/assemble-iso.sh BUILD_CONTEXT IMAGE OUTPUT.iso
#
# BUILD_CONTEXT is the directory `python -m linuxlive compile` wrote. IMAGE is
# the podman image built from it. Needs root, podman, squashfs-tools, xorriso,
# GRUB's i386-pc files (grub-pc-bin), mtools and dosfstools.
#
# Environment:
#   LL_SQUASHFS_LEVEL   zstd level for the root filesystem (default 15)
#   LL_RMI_AFTER        1 = delete IMAGE once exported, to free disk space
#   LL_KEEP_WORK        1 = keep the work directory for debugging
# shellcheck disable=SC2154 # LL_* settings come from the context's iso.env
set -euo pipefail

log() { printf '[assemble-iso] %s\n' "$*"; }
die() {
	printf '[assemble-iso] error: %s\n' "$*" >&2
	exit 1
}

[[ $# -eq 3 ]] || {
	echo "usage: $0 BUILD_CONTEXT IMAGE OUTPUT.iso" >&2
	exit 2
}
ctx=$(realpath "$1")
image=$2
out=$(realpath -m "$3")
[[ $EUID -eq 0 ]] || die "must run as root"
[[ -f $ctx/iso.env ]] || die "$ctx/iso.env not found; run 'python -m linuxlive compile' first"
# LL_* settings come from the compiled context.
# shellcheck source=/dev/null
source "$ctx/iso.env"

for tool in podman mksquashfs xorriso mkfs.vfat mmd mcopy; do
	command -v "$tool" >/dev/null || die "$tool is not installed"
done
# The CPU (amd64 when an older context does not say): its UEFI file names,
# and whether it boots on BIOS too, which needs GRUB's i386-pc files.
LL_ARCH=${LL_ARCH:-amd64} LL_EFI=${LL_EFI:-x64} LL_BIOS=${LL_BIOS:-1}
efi_boot=BOOT${LL_EFI^^}.EFI
grub_mkimage="" grub_pc=""
if [[ $LL_BIOS == 1 ]]; then
	grub_mkimage=$(command -v grub-mkimage || command -v grub2-mkimage) || die "grub-mkimage is not installed"
	for dir in /usr/lib/grub/i386-pc /usr/lib/grub2/i386-pc; do
		if [[ -z $grub_pc && -f $dir/boot_hybrid.img ]]; then grub_pc=$dir; fi
	done
	[[ -n $grub_pc ]] || die "GRUB i386-pc files not found (install grub-pc-bin)"
fi

work=$(mktemp -d "${TMPDIR:-/var/tmp}/linuxlive-iso.XXXXXX")
root=$work/rootfs
iso=$work/iso
mkdir -p "$root" "$iso"
container=""

cleanup() {
	if [[ -n $container ]]; then
		podman umount "$container" >/dev/null 2>&1 || true
		podman rm -f "$container" >/dev/null 2>&1 || true
	fi
	if [[ ${LL_KEEP_WORK:-0} == 1 ]]; then
		log "work directory kept at $work"
	else
		rm -rf "$work"
	fi
}
trap cleanup EXIT

# 1. Copy the image's filesystem out, keeping ownership, modes, ACLs and xattrs
#    (file capabilities live in xattrs).
log "exporting $image"
container=$(podman create "$image" /bin/true)
mnt=$(podman mount "$container")
tar -C "$mnt" --numeric-owner --xattrs --xattrs-include='*' --acls -cpf - . |
	tar -C "$root" --numeric-owner --xattrs --xattrs-include='*' --acls -xpf -
podman umount "$container" >/dev/null
podman rm "$container" >/dev/null
container=""
log "root filesystem: $(du -sh "$root" | cut -f1)"

# Run a command from the image itself with the exported tree mounted. A
# privileged container gives Flatpak's bubblewrap and setfiles what they need
# without a hand-rolled chroot.
in_image() {
	podman run --rm --privileged --network host --entrypoint /bin/sh \
		--volume "$root:/target" "$image" -c "$1"
}

# 2. Preinstall the spec's Flatpak apps into the live system, with the
#    translations of the spec's language (settings.language), if it has one.
if [[ -n ${LL_FLATPAKS:-} ]]; then
	log "installing Flatpaks: $LL_FLATPAKS"
	mkdir -p "$root/var/lib/flatpak"
	podman run --rm --privileged --network host \
		--volume "$root/var/lib/flatpak:/var/lib/flatpak" "$image" /bin/sh -euc "
		flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
		flatpak remote-modify --system --enable --no-filter flathub
		${LL_FLATPAK_LANGUAGES:+flatpak config --system --set languages $LL_FLATPAK_LANGUAGES}
		flatpak install --system --noninteractive --assumeyes flathub $LL_FLATPAKS
		flatpak list --system --app --columns=application"
fi

# 3. Make the tree boot cleanly on any machine.
log "normalizing the root filesystem"
rm -f "$root/.dockerenv" "$root/run/.containerenv"
for dir in run tmp var/tmp var/log; do
	if [[ -d $root/$dir ]]; then find "$root/$dir" -mindepth 1 -delete; fi
done
mkdir -p "$root"/{proc,sys,dev,run,tmp}
chmod 1777 "$root/tmp"
: >"$root/etc/machine-id" # empty: systemd generates one per boot
if [[ $LL_BASE == ubuntu ]]; then
	mkdir -p "$root/var/lib/dbus"
	ln -sfn /etc/machine-id "$root/var/lib/dbus/machine-id"
	rm -f "$root"/var/cache/apt/*.bin
fi
printf '%s\n' "${LL_HOSTNAME:-$LL_ID}" >"$root/etc/hostname"
printf '127.0.0.1\tlocalhost\n::1\tlocalhost ip6-localhost ip6-loopback\n' >"$root/etc/hosts"
if [[ -x $root/usr/lib/systemd/systemd-resolved ]]; then
	ln -sfn ../run/systemd/resolve/stub-resolv.conf "$root/etc/resolv.conf"
fi

# 4. Label the tree for SELinux, which Fedora enforces in the live session.
#    This runs last so every file written above gets a label too. The ostree
#    repository under /sysroot hard-links the same files as /usr, so labelling
#    /usr covers them; skipping it avoids one warning per object.
if [[ -d $root/etc/selinux/targeted ]]; then
	log "relabelling for SELinux"
	in_image '
		set -eu
		policy=$(ls /etc/selinux/targeted/policy/policy.* | sort -V | tail -n 1)
		setfiles -m -F -r /target -c "$policy" -e /target/sysroot \
			/etc/selinux/targeted/contexts/files/file_contexts /target'
fi

if [[ ${LL_RMI_AFTER:-0} == 1 ]]; then
	log "removing $image to free disk space"
	podman rmi "$image" >/dev/null || true
fi

# 5. Kernel, initramfs and the compressed root filesystem.
level=${LL_SQUASHFS_LEVEL:-15}
squash() {
	log "compressing the root filesystem (zstd level $level)"
	mksquashfs "$root" "$1" -noappend -comp zstd -Xcompression-level "$level" -b 1M -xattrs -quiet
}
# Every installed package with its version and the source package it was built
# from, published beside the ISO so anyone can find the matching source.
case $LL_LIVE_LAYOUT in
casper) chroot "$root" dpkg-query -W --showformat='${binary:Package} ${Version} ${source:Package}=${source:Version}\n' ;;
liveos) chroot "$root" rpm -qa --qf '%{NAME}.%{ARCH} %{EPOCHNUM}:%{VERSION}-%{RELEASE} %{SOURCERPM}\n' ;;
esac | LC_ALL=C sort >"$out.packages"
[[ -s $out.packages ]] || die "no package list"
# Code the spec built in from GitHub (repos), at the commit it was built from,
# with the address of that commit's source archive.
if [[ -f $root/usr/lib/linuxlive/repos.txt ]]; then
	while read -r url commit; do
		printf '%s %s %s/archive/%s.tar.gz\n' "${url#https://}" "$commit" "$url" "$commit"
	done <"$root/usr/lib/linuxlive/repos.txt" >>"$out.packages"
fi
case $LL_LIVE_LAYOUT in
casper)
	mkdir -p "$iso/casper"
	kernel=$(find "$root/boot" -maxdepth 1 -name 'vmlinuz-*' -printf '%f\n' | sort -V | tail -n 1)
	[[ -n $kernel ]] || die "no kernel in /boot"
	kver=${kernel#vmlinuz-}
	[[ -f $root/boot/initrd.img-$kver ]] || die "no initrd for kernel $kver"
	cp "$root/boot/vmlinuz-$kver" "$iso/casper/vmlinuz"
	cp "$root/boot/initrd.img-$kver" "$iso/casper/initrd"
	chroot "$root" dpkg-query -W --showformat='${Package} ${Version}\n' >"$iso/casper/filesystem.manifest"
	du -sx --block-size=1 "$root" | cut -f1 >"$iso/casper/filesystem.size"
	squash "$iso/casper/filesystem.squashfs"
	;;
liveos)
	mkdir -p "$iso/live" "$iso/LiveOS"
	mapfile -t kernels < <(find "$root/usr/lib/modules" -mindepth 1 -maxdepth 1 -printf '%f\n')
	[[ ${#kernels[@]} -eq 1 ]] || die "expected one kernel in /usr/lib/modules, found ${#kernels[@]}"
	kver=${kernels[0]}
	cp "$root/usr/lib/modules/$kver/vmlinuz" "$iso/live/vmlinuz"
	cp "$root/usr/lib/modules/$kver/initramfs.img" "$iso/live/initrd.img"
	squash "$iso/LiveOS/squashfs.img"
	;;
*) die "unknown live layout $LL_LIVE_LAYOUT" ;;
esac
log "kernel $kver"

# 6. Disk identity files. GRUB finds the ISO through the marker file;
#    Ubuntu's CD GRUB image looks for .disk/info.
mkdir -p "$iso/.disk"
printf '%s %s "based on %s" - %s (%s)\n' "$LL_NAME" "$LL_VERSION" "$LL_BASE_LABEL" "$LL_ARCH" "$(date -u +%Y%m%d)" >"$iso/.disk/info"
: >"$iso/$LL_MARKER"

# 7. GRUB for UEFI: the image's signed shim and GRUB in a FAT image that is
#    both the El Torito EFI entry and the GPT EFI system partition.
efi_src=$root/usr/lib/linuxlive/efi
for f in "shim$LL_EFI.efi" "grub$LL_EFI.efi" "mm$LL_EFI.efi"; do
	[[ -s $efi_src/$f ]] || die "missing $efi_src/$f; the image build should have provided it"
done
mkdir -p "$iso/EFI/BOOT" "$iso/boot/grub"
cp "$efi_src/shim$LL_EFI.efi" "$iso/EFI/BOOT/$efi_boot"
cp "$efi_src/grub$LL_EFI.efi" "$efi_src/mm$LL_EFI.efi" "$iso/EFI/BOOT/"
cp "$ctx/iso/efi-grub.cfg" "$iso/EFI/BOOT/grub.cfg"
cp "$ctx/iso/boot/grub/grub.cfg" "$iso/boot/grub/grub.cfg"
efi_kib=$(($(du -sk --apparent-size "$iso/EFI" | cut -f1) + 4096))
efi_kib=$(((efi_kib + 1023) / 1024 * 1024))
efi_img=$work/efiboot.img
mkfs.vfat -C "$efi_img" "$efi_kib" >/dev/null
mmd -i "$efi_img" ::/EFI ::/EFI/BOOT
mcopy -i "$efi_img" "$iso"/EFI/BOOT/* ::/EFI/BOOT/

# 8. GRUB for BIOS (PCs): an El Torito core image that finds the ISO by its
#    marker file, with the module directory alongside for anything else it loads.
bios_image=boot/grub/i386-pc/eltorito.img
if [[ $LL_BIOS == 1 ]]; then
	mkdir -p "$iso/boot/grub/i386-pc"
	cp "$grub_pc"/*.mod "$grub_pc"/*.lst "$iso/boot/grub/i386-pc/"
	"$grub_mkimage" -d "$grub_pc" -O i386-pc-eltorito -p /boot/grub -c "$ctx/iso/bios-embed.cfg" \
		-o "$iso/$bios_image" \
		biosdisk iso9660 part_msdos part_gpt search search_fs_file normal configfile linux echo test ls
fi

# 9. Checksums for Ubuntu's medium check. casper-md5check reads md5sum.txt in
#    every live session, and its service fails when the file is missing.
#    xorriso patches a boot info table into the El Torito image as it writes
#    the ISO, so that file's checksum could never match and is left out.
if [[ $LL_LIVE_LAYOUT == casper ]]; then
	log "writing md5sum.txt"
	(cd "$iso" && find . -type f ! -path ./md5sum.txt ! -path "./$bios_image" -print0 |
		LC_ALL=C sort -z | xargs -0 md5sum >md5sum.txt)
fi

# 10. The hybrid image: El Torito for optical media, MBR and GPT for USB sticks.
#     The layout follows Ubuntu's own desktop ISOs; ARM's has no BIOS parts.
log "writing $out"
mkdir -p "$(dirname "$out")"
bios=()
if [[ $LL_BIOS == 1 ]]; then
	bios=(--grub2-mbr "$grub_pc/boot_hybrid.img" --mbr-force-bootable)
fi
eltorito=(-e '--interval:appended_partition_2:all::' -no-emul-boot)
if [[ $LL_BIOS == 1 ]]; then
	eltorito=(-b "/$bios_image" -no-emul-boot -boot-load-size 4 -boot-info-table --grub2-boot-info -eltorito-alt-boot "${eltorito[@]}")
fi
xorriso -as mkisofs \
	-iso-level 3 -full-iso9660-filenames -joliet -joliet-long -rational-rock \
	-volid "$LL_VOLID" \
	-output "$out.partial" \
	"${bios[@]}" \
	--protective-msdos-label \
	-partition_offset 16 \
	-append_partition 2 28732ac11ff8d211ba4b00a0c93ec93b "$efi_img" \
	-appended_part_as_gpt \
	-iso_mbr_part_type a2a0d0ebe5b9334487c068b6b72699c7 \
	-c /boot.catalog \
	"${eltorito[@]}" \
	"$iso"
mv "$out.partial" "$out"
(cd "$(dirname "$out")" && sha256sum "$(basename "$out")" >"$(basename "$out").sha256")
log "done: $out ($(du -h "$out" | cut -f1))"
