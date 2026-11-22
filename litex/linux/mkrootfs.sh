#!/bin/sh
# Makes the Debian 13 root file system for the NaxRiscv SoC (litex/linux/README.md). It runs as root
# in an amd64 debian:trixie container. The host must run riscv64 binaries through qemu-user binfmt
# with the F flag.
#
#   docker run --rm --privileged -v /path/to/work:/work \
#       -v /path/to/modules-install:/work/modules:ro \
#       -v /path/to/ADP-XC7K160/litex/linux/rootfs:/work/overlay:ro debian:trixie sh /work/mkrootfs.sh
#
# Inputs in /work: key.pub (the SSH public key for root), modules/lib/modules (from
# "make modules_install INSTALL_MOD_PATH=..."), and overlay (litex/linux/rootfs of this repository:
# the login message and the unit of adp-7seg-temp). The overlay goes in with owner root and the
# modes 755 or 644, because the host umask can remove the read bits. The script empties the
# /etc/motd of Debian, so that the login message shows only the board. Outputs in /work:
#   rootfs.ext4  Debian 13 riscv64 (systemd, sshd, networkd with DHCP, timesyncd), 2 GiB, label rootfs
#   busybox      static riscv64 busybox, for the initramfs
# Root has no password on the console; SSH accepts only the key.
set -eux
export DEBIAN_FRONTEND=noninteractive
dpkg --add-architecture riscv64
apt-get update -q
apt-get install -y -q mmdebstrap e2fsprogs
cd /work
rm -rf rootfs
mmdebstrap --arch=riscv64 --mode=root --variant=minbase \
  --include=systemd-sysv,libpam-systemd,udev,systemd-resolved,systemd-timesyncd,openssh-server,iproute2,iputils-ping,ca-certificates,apt-utils,less,vim-tiny,procps,kmod,e2fsprogs,dosfstools,fdisk,cloud-guest-utils,wget,curl,sudo,htop,bash-completion,ethtool,linuxptp \
  --components=main \
  --customize-hook='echo adp-xc7k160 > "$1/etc/hostname"' \
  --customize-hook='printf "127.0.0.1 localhost\n127.0.1.1 adp-xc7k160\n" > "$1/etc/hosts"' \
  --customize-hook='chroot "$1" passwd -d root' \
  --customize-hook='mkdir -p "$1/root/.ssh" && cp /work/key.pub "$1/root/.ssh/authorized_keys" && chmod 700 "$1/root/.ssh" && chmod 600 "$1/root/.ssh/authorized_keys"' \
  --customize-hook='printf "[Match]\nName=eth0\n\n[Network]\nDHCP=yes\n" > "$1/etc/systemd/network/20-eth0.network"' \
  --customize-hook='chroot "$1" systemctl enable systemd-networkd systemd-resolved' \
  --customize-hook='mkdir -p "$1/boot/firmware" && printf "/dev/mmcblk0p2 / ext4 defaults,noatime 0 1\n/dev/mmcblk0p1 /boot/firmware vfat defaults 0 2\n" > "$1/etc/fstab"' \
  --customize-hook='cp -r /work/modules/lib/modules "$1/lib/" && chown -R 0:0 "$1/lib/modules" && find "$1/lib/modules" -type d -exec chmod 755 {} + && find "$1/lib/modules" -type f -exec chmod 644 {} +' \
  --customize-hook='tar -C /work/overlay --owner=0 --group=0 --mode=u=rwX,go=rX -cf - . | tar -C "$1" --no-overwrite-dir -xpf -' \
  --customize-hook=': > "$1/etc/motd"' \
  --customize-hook='printf "deb http://deb.debian.org/debian trixie main\ndeb http://deb.debian.org/debian trixie-updates main\ndeb http://security.debian.org/debian-security trixie-security main\n" > "$1/etc/apt/sources.list"' \
  trixie rootfs http://deb.debian.org/debian
du -sh rootfs
rm -f rootfs.ext4
mkfs.ext4 -q -L rootfs -d rootfs rootfs.ext4 2G
apt-get download busybox-static:riscv64
dpkg-deb -x busybox-static_*_riscv64.deb bb && cp bb/usr/bin/busybox busybox && rm -rf bb busybox-static_*.deb
ls -la rootfs.ext4 busybox
