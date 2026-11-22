# Linux and Debian on the LiteX SoC

The SoC in U4 ([../README.md](../README.md)) boots OpenSBI, Linux 6.18.64 and Debian 13 from the SD
card. Linux brings up both NaxRiscv cores, and uses the UART, the Ethernet MAC, the SD card, the
XADC, the seven-segment digits and the AC97 codec. The software is built outside this repository.

| File | Contents |
|---|---|
| `adp.config` | The kernel config fragment |
| `patches/` | Patches for the kernel source |
| `prepare-kernel.sh` | Applies `patches/` to a kernel source tree, and copies the three drivers below into it |
| `litex-xadc-hwmon.c` | hwmon driver for the XADC CSRs |
| `adp-seven-seg.c` | The two seven-segment digits as a line display and two LEDs |
| `adp-ac97.c` | ALSA driver for the AC97 controller |
| `adp-7seg-temp.c` | A tool that shows the FPGA temperature on the digits |
| `mkdts.py` | Makes the DTS from `csr.json` |
| `boot-sd.json`, `boot-net.json` | The `boot.json` of the BIOS, for the SD card and for TFTP |
| `initramfs-init`, `initramfs-list` | The initramfs of the network boot, which writes the first card |
| `mkrootfs.sh` | Makes the Debian root file system in a container |
| `rootfs/` | Files that `mkrootfs.sh` copies into the root file system |
| `mkdisk.sh` | Makes the image of the card |

The start of each file gives its usage.

## Boot

The BIOS of the SoC reads `boot.json` from the card (or through TFTP), and loads the files into the
DDR3:

| Address | Contents |
|---|---|
| `0x40f00000` | OpenSBI (the DTB reserves 512 KiB) |
| `0x41000000` | Linux `Image` (about 25 MB) |
| `0x46000000` | DTB |
| `0x48000000` | initramfs, for the network boot only |

The BIOS gives `r1`, `r2` and `r3` of `boot.json` to `a0`, `a1` and `a2`. OpenSBI wants the hart
ID in `a0` and the DTB in `a1`. Thus `r1` is 0 and `r2` is the DTB address. With the DTB address
in `r1`, OpenSBI stops without output. Do not use the defaults of LiteX: they put the Image at
`0x40000000`, and an Image of 25 MB then overlaps OpenSBI.

## Build the software

- **OpenSBI** v1.9: `make PLATFORM=generic FW_TEXT_START=0x40f00000 FW_JUMP=y
  FW_JUMP_ADDR=0x41000000 FW_JUMP_FDT_ADDR=0x46000000`. Its console is the LiteX UART, through the
  DTB.
- **Kernel** 6.18.64, with `riscv64-linux-gnu-gcc` 16.2.1:
  1. Give `sh prepare-kernel.sh /path/to/linux`. It applies `patches/`, and copies the three
     drivers into the tree. `patches/0002` gives them the options `ADP_SEVEN_SEG`,
     `SENSORS_LITEX_XADC` and `SND_ADP_AC97`.
  2. Merge `adp.config` over the defconfig. The start of `adp.config` gives the commands. The
     kernel version is then `6.18.64-adp`.
  3. Build the `Image` and the modules.
- The drivers of the board are built into the kernel, so that they work before the root file
  system is mounted. So are `configfs`, `nls_iso8859_1`, the line display core and
  `snd-ac97-codec`.
- **DTB**: `python3 mkdts.py csr.json > linux.dts && dtc -O dtb -o linux.dtb linux.dts`, with the
  Python of the LiteX installation. `mkdts.py` runs `litex_json2dts_linux`, replaces the bootargs,
  and adds the nodes of the seven-segment digits and of the AC97 controller.
- **A DTB works only with the SoC that it was made from.** A new CSR moves the addresses of the
  others. The AC97 controller moved the MAC, the SD card, the digits and the XADC, for example.

The fragment `adp.config`:

- It removes the platforms of other vendors, PCI, USB and DRM. It keeps sound, for the AC97 codec.
  The `Image` is 25.2 MB; with the defconfig it was 27.4 MB.
- It adds F2FS with LZ4 compression, zswap (LZ4), WireGuard, overlayfs, squashfs, nftables, PSI,
  `HZ_100` and `PREEMPT_VOLUNTARY`. The console log level stays 7.
- `ARCH_STARFIVE` follows `SOC_STARFIVE`. Thus the fragment must clear `SOC_STARFIVE`.

## The card

| Partition | Size | Contents |
|---|---|---|
| 1 | 256 MiB | FAT32 `BOOT`: `boot.json` (from `boot-sd.json`), `opensbi.bin`, `Image`, `linux.dtb` |
| 2 | 28.5 GiB | F2FS `rootfs`, made with `mkfs.f2fs -O extra_attr,inode_checksum,sb_checksum,compression` |
| 3 | 1 GiB | swap, label `swap`, with zswap (LZ4) |

The bootargs and `/etc/fstab` give the F2FS options:

- bootargs: `root=/dev/mmcblk0p2 rootfstype=f2fs rootflags=lazytime,compress_algorithm=lz4,compress_extension=*,compress_chksum,atgc,gc_merge rw`
- fstab: `noatime` and the same F2FS options. `noatime` is not an option of F2FS, so it is not in
  `rootflags`.
- F2FS refuses to switch `atgc` at a remount. Without `rootflags`, `systemd-remount-fs` fails, and
  the root stays without compression. Thus the options must be in `rootflags`.

The root file system:

- Debian 13 riscv64 with systemd, sshd, systemd-networkd (DHCP on eth0), systemd-resolved and
  systemd-timesyncd. Root has no password on the console; SSH accepts only the key of `key.pub`.
- The board has no clock with a battery. Without timesyncd, `apt` refuses the signatures of the
  archive, because the date is wrong. `fake-hwclock` keeps the last time over a reboot.
- `f2fs-tools` is installed. journald keeps its files in RAM (`Storage=volatile`,
  `RuntimeMaxUse=32M`). `apt-daily.timer` and `apt-daily-upgrade.timer` are masked.
- `rootfs/etc/update-motd.d/20-adp-xc7k160` is the login message. It gives the board, the CPU, the
  caches, the DDR3 and the swap. Then it lists the SoC parts that have a driver, and the XADC
  values. It reads sysfs and starts only one program (`od`).

**Give explicit owners and modes to each file that you copy from a host to the card.** With the
umask of the host, a `tar` stream once gave mode 0740 to `/etc` and `/usr`. Then systemd-networkd,
systemd-resolved and systemd-timesyncd could not start. `mkrootfs.sh` installs `rootfs/` with owner
root and the modes 755 or 644.

### Make a card

`mkrootfs.sh` and `mkdisk.sh` make a card with ext4 on partition 2, and no swap partition. The
present card was converted from such a card on the host:

1. Mount partition 2 of the old card read-only, and copy it with `rsync -aHAX --numeric-ids`.
2. In the copy, through `chroot` and qemu-user: install `f2fs-tools` and `fake-hwclock`, set
   journald and the apt timers, and change `/etc/fstab` to F2FS and the swap.
3. Make the three partitions, `mkfs.f2fs` and `mkswap`.
4. Mount the F2FS with the compression options, and copy the files back with `rsync`.
5. Put the `Image` and the `linux.dtb` of this kernel on partition 1.

To write the first card through the network:

1. Run `../tftpd.py` on the host with `Image`, `opensbi.bin`, a DTB with the initramfs, the
   initramfs and `boot-net.json` as `boot.json`. A firewall on the host must permit UDP from the
   board to that port.
2. Load the bitstream. The BIOS tries the card, then the network. If the card has a `boot.json`,
   push Q at "Press Q or ESC" and give the command `netboot`.
3. In the shell of the initramfs, give `nc -l -p 5000 | gunzip | dd of=/dev/mmcblk0 bs=1M`.
4. On the host, send the image: `ncat --send-only A.B.C.D 5000 < disk.img.gz`.
5. Load the bitstream again. The BIOS boots from the card.
6. In Debian, give `growpart /dev/mmcblk0 2` and `resize2fs /dev/mmcblk0p2`.

Result **[measured, 2026-11-21]**: the board wrote an image of 2.3 GiB in this way, and `md5sum` on
the board agreed with the host. `gunzip` used most of the CPU time.

## Drivers

### XADC

`litex_json2dts_linux` adds a node with compatible `litex,hwmon-xadc` for the XADC CSRs. Linux 6.18
has no driver for that node: the `xilinx-xadc` driver is for the AXI XADC and the XADC of Zynq,
which have a different register map. `litex-xadc-hwmon.c` (GPL-2.0 or MIT) only reads the CSRs:

- `temp1_input` (label `fpga`): raw × 503975 / 4096 − 273150, in millidegree Celsius.
- `in0_input` to `in2_input` (labels `vccint`, `vccaux`, `vccbram`): raw × 3000 / 4096, in mV.

Result **[measured, 2026-11-21]**: `sensors` showed VCCINT 1.04 V, VCCAUX 1.85 V, VCCBRAM 1.04 V
and 49.8 °C. Five readings of `temp1_input`, one each second, were 49.7 to 50.3 °C. These agree
with the XADC readings of [docs/fpga.md](../../docs/fpga.md).

### Seven-segment digits

`adp-seven-seg.c` (GPL-2.0 or MIT) makes the `seven_seg` CSR a line display of two characters and
two LEDs. It uses the line display core of the kernel (`drivers/auxdisplay/line-display.c`), which
maps ASCII to seven segments.

- Write text to `/sys/bus/platform/drivers/adp-seven-seg/*/linedisp.*/message`. The left digit
  (7SEG2) shows the first character. A text of more than two characters scrolls.
- The decimal points are the LEDs `adp:dp-left` and `adp:dp-right`, with any LED trigger. By
  default the left point has the trigger `mmc0` (the SD card) and the right point has `heartbeat`.
- `mkdts.py` adds the node, with the compatible `adp-xc7k160,seven-seg`.
- The kernel needs the LED class, the LED triggers and the line display core. `adp.config` sets
  the first two; `ADP_SEVEN_SEG` selects `LINEDISP`, which has no prompt.
- `adp-7seg-temp.c` shows the FPGA temperature of the XADC on the digits. It keeps the two sysfs
  files open, writes only a changed value, and does not fork. Build it with
  `riscv64-linux-gnu-gcc -O2 -static`, and install it as `/usr/local/sbin/adp-7seg-temp`.
  `rootfs/etc/systemd/system/adp-7seg-temp.service` starts it.

Result **[measured, 2026-11-21]**: the service wrote `45` while `sensors` showed 45.5 °C. The owner
saw `45` and `46` on the digits, the heartbeat on the right point, and the left point during
accesses to the SD card. The tool used 552 KiB of memory (RSS).

### AC97

`adp-ac97.c` (GPL-2.0 or MIT) is an ALSA driver with one PCM device: playback and capture, 48 kHz,
S16_LE, two channels. `snd-ac97-codec` gives the mixer of the codec.

- `SND_ADP_AC97` selects `SND_AC97_CODEC`, which has no prompt.
- `mkdts.py` adds the node, with the compatible `adp-xc7k160,ac97`. The driver uses fixed CSR
  offsets. `mkdts.py` compares them with `csr.json` and stops if they differ.

Results **[measured, 2026-11-21]**:

- `/proc/asound/cards` showed `ADP-XC7K160 AC97 (ALC203)`.
- A loop test played 6 s of a tone: 1 kHz on the left, 1.5 kHz on the right. `arecord` recorded
  `Mix` at the same time, with the `Capture` gain at 0 dB. `aplay` took 6.14 s, and the recording
  had 6.000 s of signal.
- The peak frequency was 1000.00 Hz on the left and 1500.00 Hz on the right.
- A sine fit on each block of 10 ms (579 blocks on each side) left 5 LSB or less. Thus no sample was
  lost or repeated. The same test during two SSH transfers in opposite directions gave the same
  result.
- The owner heard the tone in headphones.

### Ethernet

The `liteeth` driver of Linux 6.18.64 can stop its TX queue for good. `liteeth_start_xmit()` stops
the queue when the MAC is busy, and the TX interrupt wakes the queue only if it is stopped. If the
interrupt of the last frame comes between the check and the stop, nothing wakes the queue. On
2026-11-21 this occurred after about 30 minutes of `apt`: eth0 received frames and sent none, and
the kernel log showed `LITEETH_READER_READY not ready`.

`patches/0001-net-liteeth-do-not-stall-the-tx-queue.patch` reads the state again after the stop. It
also adds a TX timeout handler that wakes the queue.

The `dropped` count of `ip -s link` is not a count of lost frames:

- The driver counts each of its own drops also in `rx_errors`.
- LiteEth has no address filter. The network stack discards the frames of other stations, and
  counts them in `dropped`.
- The MAC counts the frames that it drops for lack of a free slot in the CSR
  `ethmac_sram_writer_errors`. Linux does not read it. It counts from the configuration, also
  before Linux opens eth0.

Results **[measured, 2026-11-21]**, with the patch and 8 RX slots:

- In 60 s without load, eth0 received 1286 frames. `ethmac_sram_writer_errors` did not change.
- SSH (`aes128-ctr`) sent 64 MiB from the host to the board in 56 s, and from the board to the host
  in 51 s. The CPU limits this speed. `ethmac_sram_writer_errors`, `tx_dropped` and `rx_errors` did
  not change.
- During transfers in both directions at the same time, with the AC97 test, the MAC dropped 13
  frames. The transfers completed.

## Performance counters

NaxRiscv has `mcycle`, `minstret` and four event counters, `mhpmcounter3` to 6. Its events
(`PerformanceCounterService` in `Service.scala` of NaxRiscv):

| Event | Meaning |
|---|---|
| 1 | I$ refill |
| 2 | D$ refill |
| 3 | D$ writeback |
| 4 | Branch miss |

OpenSBI gives the counters to Linux only through a `riscv,pmu` node in the DTB. `mkdts.py` adds it
for a NaxRiscv SoC. Then `perf` (Debian package `linux-perf`) counts them:

```sh
perf stat -e cycles,instructions,r1,r2 -- command
```

Limits **[measured, 2026-11-22]**:

- `cycles` and `instructions` work only if the node maps them to `mcycle` and `minstret`.
- The raw row of the node must have a mask that is not 0. With a mask of 0, OpenSBI takes the row
  as an overlap of the other rows and drops it.
- Event 4 (`branch-misses` or `r4`) always gave 0, also for a loop that misses half of its branches.
  Its signal in NaxRiscv is not the one that branch misses use.
- Event 3 always gave almost the same count as event 2. It is not a count of dirty lines.
- NaxRiscv has no Sscofpmf. The counters cannot separate user and kernel, and `perf record` does not
  work. A count includes the kernel and all processes.

## Results

**[measured, 2026-11-21]**, two cores, 512 KiB L2:

- OpenSBI found 2 harts, and Linux brought up 2 CPUs. Linux shows the ISA
  `rv64imafdc_zicntr_zaamo_zalrsc_zca_zcd` and the MMU `sv39`.
- From the F2FS card, `systemd-analyze` gave 16.8 s (kernel) + 86.6 s (userspace), and no unit
  failed.
- F2FS compressed 12,167 files of the root file system and saved 33,123 blocks of 4 KiB.
- With the 128 KiB L2, `head -c 32M /dev/zero | sha256sum` took 13.6 s. Two of them in parallel
  took 14.8 s.
