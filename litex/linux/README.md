# Linux and Debian on the LiteX SoC

The reference SoC ([../README.md](../README.md)) boots OpenSBI, Linux 6.18.64 and Debian 13 from the
SD card. Linux brings up both NaxRiscv cores, and uses the UART, the Ethernet MAC, the SD card, the
XADC, the seven-segment digits and the AC97 codec. The software is built outside this repository.

| File | Contents |
|---|---|
| `adp.config` | The kernel config fragment |
| `patches/` | Patches for the kernel source |
| `prepare-kernel.sh` | Applies `patches/` to a kernel source tree, and copies the four drivers below into it |
| `litex-xadc-hwmon.c` | hwmon driver for the XADC CSRs |
| `adp-seven-seg.c` | The two seven-segment digits as a line display and two LEDs |
| `adp-ac97.c` | ALSA driver for the AC97 controller |
| `adp-ethmac.c` | Driver for the Ethernet MAC with DMA of `adp_ethmac.py` |
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
  1. Give `sh prepare-kernel.sh /path/to/linux`. It applies `patches/`, and copies the four
     drivers into the tree. `patches/0002` gives them the options `ADP_SEVEN_SEG`,
     `SENSORS_LITEX_XADC`, `SND_ADP_AC97` and `ADP_ETHMAC`.
  2. Merge `adp.config` over the defconfig. The start of `adp.config` gives the commands. The
     kernel version is then `6.18.64-adp`.
  3. Build the `Image` and the modules.
- The drivers of the board are built into the kernel, so that they work before the root file
  system is mounted. The kernel has the drivers of both Ethernet MACs, so it works with both SoCs. So are `configfs`, `nls_iso8859_1`, the line display core and
  `snd-ac97-codec`.
- **DTB**: `python3 mkdts.py csr.json > linux.dts && dtc -O dtb -o linux.dtb linux.dts`, with the
  Python of the LiteX installation. `mkdts.py` runs `litex_json2dts_linux`, replaces the bootargs,
  and adds the nodes of the seven-segment digits, the AC97 controller and the MAC with DMA. It also
  adds the PMU events and the high speed mode of the SD card.
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

Two MACs can carry eth0. The target option selects one ([../README.md](../README.md)):

- The LiteEth MAC (`--with-ethernet`): the CPU copies each frame to or from a slot memory in the
  FPGA. The driver is `liteeth`, with `patches/0001`, `0004` and `0005`.
- The MAC with DMA (`--with-ethernet-dma`, `adp_ethmac.py`): the frames go between the MAC and the
  main RAM through the coherent DMA bus. The driver is `adp-ethmac.c`.

The CPU is the limit with LiteEth. On 2026-11-22 a program read and wrote the slot memory through
`/dev/mem`. Each access took 200 to 240 ns, also for 8-byte accesses, and the CPU makes one access
at a time. Thus the copy of a frame of 1514 bytes takes about 50 µs, and a frame takes 123 µs on
the wire at 100 Mbit/s.

The patches of `liteeth`:

- `0001` fixes a stop of the TX queue. `liteeth_start_xmit()` stopped the queue when the MAC was
  busy, and the TX interrupt woke the queue only if it was stopped. If the interrupt came between
  the check and the stop, nothing woke the queue. On 2026-11-21 this occurred after about 30
  minutes of `apt`. The patch reads the state again after the stop, and adds a TX timeout handler.
- `0004` receives in a NAPI poll and gives the frames to GRO. It sums each frame during the copy:
  received frames get `CHECKSUM_COMPLETE`, and the driver offers `HW_CSUM` and scatter-gather. It
  copies from the pieces of an skb with aligned loads, because misaligned loads trap on NaxRiscv.
  `memcpy()` of RISC-V copies one byte at a time if the two addresses are not aligned alike.
- `0005` adds TSO in software: the driver writes the headers of each segment, and the sum of the
  copy gives the TCP checksum.

`adp-ethmac.c`:

- Receive: the driver gives 256 buffers to the engine. The engine writes each frame 2 bytes after
  the start of a buffer, so that the IP header is aligned, and sums the frame. The driver builds
  the skb around the buffer: the CPU does not copy the frame.
- Transmit: the driver gives the engine one descriptor for each piece of a frame. The engine reads
  the pieces and inserts the checksum. For TSO the driver writes the headers of each segment into
  a coherent buffer; the payload pieces point into the skb.
- The engine keeps its buffers when eth0 goes down. The next open uses them again.
- `rx_missed_errors` is the count of frames that the engine dropped for lack of room, and
  `rx_crc_errors` the count of frames with a bad CRC.

The `dropped` count of `ip -s link` is not a count of lost frames. Neither MAC has an address
filter. The network stack discards the frames of other stations and the frames of unknown
protocols (for example PTP), and counts the latter in `dropped`. With LiteEth, the MAC counts the
frames that it drops for lack of a free slot in the CSR `ethmac_sram_writer_errors`. Linux does not
read it.

Results **[measured, 2026-11-22]**. The host ran `iperf3 -s` on the LAN. The board ran
`taskset -c 1 iperf3 -c HOST -t 10`, with `-R` for the direction into the board. Out is from the
board. The TCP values are the receiver side of iperf3, in Mbit/s. The maximum of TCP with an MTU
of 1500 is about 94.

| MAC and driver | TCP out | TCP in | UDP out (`-u -b 100M`) |
|---|---|---|---|
| LiteEth, 8 RX and 4 TX slots, `liteeth` with `0001` | 58.4 | 71.1 | not measured |
| The same, with `0004` | 68.5 | 93.9 | 77.0 |
| The same, with `0004` and `0005` | 76.2 | 93.6 | not measured |
| LiteEth, 16 RX and 8 TX slots, `0001`, `0004`, `0005` | 77.7 | 93.7 | 76.2 |
| The MAC with DMA, `adp-ethmac.c` | 89.5 | 93.8 | 93.4 |

- With LiteEth, CPU 0 was busy all the time in both directions. A program at the lowest priority
  got 1% of CPU 0. The copy took about one third of CPU 0, and the TCP stack the rest.
- With iperf3 on CPU 0, all the work was on one CPU: the MAC with DMA gave 69.5 out and 81.7 in.
- In both directions at the same time for 60 s, the MAC with DMA gave 77.9 out and 75.8 in. The
  engine dropped 4715 frames, and the host sent again 12640 segments. The engine then had no free
  buffer in 2 of 1000 samples: the poll was late while CPU 0 was busy.
- 32 MiB of random data went through SSH in each direction with the same MD5. Ping with 0 to 1472
  bytes of data worked. The kernel showed no checksum fault.
- An `ip link set eth0 down` and `up` with the MAC with DMA kept the receive buffers, and TCP worked
  after it.

### SD card

The `litex_mmc` driver of Linux 6.18.64 keeps the defaults of the MMC core: one segment of one page
for each request. Thus each request reads or writes 8 blocks of 512 bytes. On 2026-11-22 a trace of
the events `mmc_request_start` and `mmc_request_done` showed the cost of 512 such reads at 25 MHz:

- Each request took 693 µs in the driver on average. The data of 8 blocks needs about 333 µs of the
  bus.
- The time between two requests was 519 µs on average.

`patches/0003-mmc-litex-mmc-requests-of-128-kib.patch` allows requests of 128 KiB in 32 segments.
A request of several segments goes through the bounce buffer of the driver. The wait for the data
gets a timeout of 1 s, because 128 KiB needs 11 ms at 25 MHz.

`mkdts.py` gives `cap-sd-highspeed` to the SD card node. Linux then switches the card to high speed
mode, and the clock goes from 25 MHz to 50 MHz. The divider of LiteSDCard makes 50 MHz from the
system clock of 100 MHz.

The first test at 50 MHz had protection, because a refused write makes the later writes of this
card one write late ([../../docs/sd-card.md](../../docs/sd-card.md)):

- A temporary parameter of the driver kept the clock at 25 MHz during the boot. The card was in
  high speed mode.
- The root file system was frozen with `fsfreeze`, and the swap was off. Then the debugfs file
  `clock` of `mmc0` set 50 MHz.
- At 50 MHz, a read of 128 MiB of partition 2 gave the same MD5 as at 25 MHz. Two writes of 32 MiB
  to partition 3 read back the same. The kernel showed no error.

Other limits of the SD card in Linux:

- LiteSDCard gives its interrupt at the end of the command response, and not at the end of the
  data. The driver polls for the end of the data, with sleeps of 2 to 5 µs.
- The DMA of LiteSDCard is a 32-bit Wishbone master. LiteX connects it to the coherent DMA bus of
  NaxRiscv, with one beat for each transfer ([../README.md](../README.md)). If the DMA is slow,
  LiteSDCard stops the SD clock.

Results **[measured, 2026-11-22]**, with the card in the socket (CID name `SH32G`). `dd` read and
wrote 32 MiB of partition 3, with the swap off. The reads used `iflag=direct`, and the writes used
`oflag=direct conv=fsync`. The data read back was the same as the data written. The columns give
the `dd` block size, and the values are in MB/s.

| Clock, request size | Read 4 KiB | Read 64 KiB | Read 1 MiB | Write 4 KiB | Write 64 KiB | Write 1 MiB |
|---|---|---|---|---|---|---|
| 25 MHz, 4 KiB | 2.9 | 3.0 | 3.3 | 1.5 | 1.5 | 1.6 |
| 25 MHz, 128 KiB | 2.8 | 8.8 | 8.6 | 1.6 | 7.7 | 8.5 |
| 50 MHz (high speed), 128 KiB | 3.6 | 13.8 | 15.3 | 1.8 | 12.8 | 13.0 |

- With blocks of 1 MiB, the CPU time for the read of 32 MiB was 5.9 s, 2.5 s and 1.0 s. For the
  write it was 9.5 s, 2.2 s and 1.3 s.
- The bus limit with 4 data lines is 12.5 MB/s at 25 MHz and 25 MB/s at 50 MHz.
- A trace of 128 reads of 128 KiB gave the time in the driver and the time between two requests.
  At 50 MHz they were 7.35 ms and 0.87 ms; the data needs about 5.3 ms of the bus. In high speed
  mode at 25 MHz they were 12.26 ms and 0.91 ms; the data needs about 10.7 ms.
- Thus each request of 128 KiB had about 2 ms more than its data at both clocks. The DMA did not
  stop the clock for a long time at 50 MHz.

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
