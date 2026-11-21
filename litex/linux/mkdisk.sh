#!/bin/sh
# Makes an SD card image for the NaxRiscv SoC (litex/linux/README.md):
#   partition 1  FAT32, 256 MiB, label BOOT: boot.json, opensbi.bin, Image, linux.dtb
#   partition 2  ext4, the rootfs.ext4 of mkrootfs.sh (2 GiB); grow it on the board with growpart
#
#   sh mkdisk.sh BOOTDIR ROOTFS.ext4 OUT.img
#
# BOOTDIR has the four files; boot.json is boot-sd.json. The script needs sfdisk, mkfs.vfat and
# mtools, and no root. It writes OUT.img and OUT.img.gz, and nothing in the repository.
set -eu
[ $# -eq 3 ] || { echo "usage: sh mkdisk.sh BOOTDIR ROOTFS.ext4 OUT.img" >&2; exit 2; }
boot="$1"; rootfs="$2"; out="$3"
rootfs_mib=$(( $(stat -c %s "$rootfs") / 1048576 ))
p1="$out.p1"
rm -f "$out" "$p1"
mkfs.vfat -F 32 -n BOOT -C "$p1" $((256 * 1024)) >/dev/null
mcopy -i "$p1" "$boot/boot.json" "$boot/opensbi.bin" "$boot/Image" "$boot/linux.dtb" ::
truncate -s $((1 + 256 + rootfs_mib))M "$out"
printf 'label: dos\nstart=2048, size=%d, type=c, bootable\nstart=%d, size=%d, type=83\n' \
    $((256 * 2048)) $((2048 + 256 * 2048)) $((rootfs_mib * 2048)) | sfdisk -q "$out"
dd if="$p1" of="$out" bs=1M seek=1 conv=notrunc,sparse status=none
dd if="$rootfs" of="$out" bs=1M seek=257 conv=notrunc,sparse status=none
rm -f "$p1"
gzip -c "$out" > "$out.gz"
md5sum "$out"
