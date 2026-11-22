# LiteX SoC for the ADP-XC7K160

This directory has a platform and an SoC for [LiteX](https://github.com/enjoy-digital/litex). The
reference SoC has two NaxRiscv cores and runs Debian from the SD card. The Linux side is in
[linux/README.md](linux/README.md).

| File | Contents |
|---|---|
| `adp_xc7k160_platform.py` | The LiteX platform. It reads the pins and the I/O standards from `pins.csv`, by the signal names of the manual |
| `adp_xc7k160_target.py` | The SoC: the CPU (VexRiscv by default), the BIOS, the UART, the 1 GiB DDR3 as main RAM, the buttons and the displays. As options: the LiteDRAM BIST, Etherbone or the MAC on the 10/100 Ethernet, the SD card, and the AC97 controller with DMA |
| `adp_ac97.py` | The AC97 controller: the AC-link, the codec commands, and two DMA engines for playback and capture |
| `adp_ethmac.py` | The Ethernet MAC with DMA: the MAC core of LiteEth with a DMA front end on an AXI4 master |
| `naxriscv_l1.scala` | Changes the L1 caches of NaxRiscv: their sizes, and the width of their refill buses (see "Size of the L1 caches" and "Refill buses and the system clock" below) |
| `versions.sh` | Lists the URL and the commit of each repository of a LiteX installation |
| `versions.txt` | The output of `versions.sh` for the installation that built the reference SoC |
| `tftpd.py` | A TFTP server that only reads, for the network boot of the BIOS |
| `linux/` | The files for Linux and Debian: see [linux/README.md](linux/README.md) |

## Install LiteX and build

LiteX is not in this repository. Install it into a separate directory with its `litex_setup.py`.
With `--config=standard`, that script clones 28 Git repositories side by side.
In the commands, `$A` is the path of this repository.

```sh
mkdir /path/to/litex-hub && cd /path/to/litex-hub
curl -O https://raw.githubusercontent.com/enjoy-digital/litex/master/litex_setup.py
uv venv --python 3.13 venv
venv/bin/python litex_setup.py --init --install --config=standard
export PATH=/path/to/litex-hub/venv/bin:/path/to/Xilinx/2025.2.1/Vivado/bin:$PATH

cd /some/writable/directory
nice -n 19 python3 $A/litex/adp_xc7k160_target.py --build    # into ./build/adp_xc7k160
VIVADO=... XVCD=... python3 $A/litex/adp_xc7k160_target.py --load
```

- The BIOS makes `python3` run LiteX modules. Thus the `bin` directory of the venv must be first
  in `PATH`.
- The BIOS needs a RISC-V compiler. LiteX accepts `riscv64-linux-gnu-gcc` from the Linux
  distribution.
- The target refuses an output directory inside this repository. The default is
  `./build/adp_xc7k160` in the current directory.
- If timing is not met, the target stops with exit status 3, before the load.
- `--load` uses `jtag/load.sh`. No option of the platform or the target writes a flash. The SoC
  holds `FCS_B`, `FOE_B` and `FWE_B` high ([docs/configuration.md](../docs/configuration.md)).
- The default speed grade is -2 ([docs/fpga.md](../docs/fpga.md)).

## The SoC

The SoC has four CSR groups besides the standard ones:

| CSR | Bits |
|---|---|
| `buttons` | 0 to 7: SW1 to SW8 pushed. 8: `PWR_SW1` pushed |
| `status` | 0: `PORn` high (the peripherals are out of reset). 1: a card is in the SD socket (`SD_CDn` low) |
| `xadc` | `temperature`, `vccint`, `vccaux`, `vccbram`: the last raw 12-bit value of each. The XADC samples them all the time (`S7SystemMonitor` of LiteX) |
| `seven_seg` | 0 to 6: segments A to G of 7SEG1 (right). 7: its decimal point. 8 to 15: the same for 7SEG2 (left). A 1 lights the segment |

`HW_RST_SW1` resets the MMCM, and thus the SoC. The BIOS console is 115200 8N1 on COM1. The SoC
always drives `PHY_PWRDWN` low and `SD_PWR_ON` high.

The options of the target:

| Option | Effect |
|---|---|
| `--with-bist` | The generator and the checker of LiteDRAM |
| `--with-etherbone --eth-ip A.B.C.D` | Etherbone: a UDP/IP stack in the FPGA that answers ARP, ping and Wishbone accesses on UDP port 1234. The CPU does not take part |
| `--with-ethernet --eth-ip A.B.C.D` | The LiteEth MAC with 16 RX slots and 8 TX slots, for the BIOS and the `liteeth` driver of Linux. `--remote-ip` and `--tftp-port` give the TFTP server of the BIOS |
| `--with-ethernet-dma` | With `--with-ethernet`: the MAC with DMA of `adp_ethmac.py` in place of LiteEth, for `adp-ethmac.c` of Linux. The BIOS then has no network boot, and the IP options are not needed. It needs NaxRiscv with `--with-coherent-dma` |
| `--with-sdcard` | LiteSDCard on the 4-bit bus, read only: the SoC has no path that writes the card |
| `--with-sdcard-write` | LiteSDCard with its write path. Use it only with a card that the owner gave for writing (AGENTS.md) |
| `--with-ac97` | The AC97 controller of `adp_ac97.py` |
| `--integrated-main-ram-size` | Block RAM as main RAM, without the DDR3 controller |

`--with-etherbone` and `--with-ethernet` exclude each other. The target has no default IP address.
Give an address that no station answers on that LAN. The MAC address is `02:ad:b0:00:00:01`, the
locally administered address of the workbench.

## The reference SoC

The reference SoC is built with these options. `jtag/flash_u4.sh` writes it into U4, so that the
board loads it at power-on ([docs/configuration.md](../docs/configuration.md)).

```sh
export JAVA_HOME=/path/to/java-17-openjdk PATH=/path/to/sbt/bin:$PATH
python3 $A/litex/adp_xc7k160_target.py --build \
    --cpu-type naxriscv --xlen 64 --with-fpu --with-rvc --with-coherent-dma --cpu-count 2 \
    --l2-bytes 524288 --l2-ways 8 --integrated-sram-size 0x4000 --sys-clk-freq 110e6 \
    --with-ethernet --with-ethernet-dma --with-sdcard-write \
    --with-ac97 --soc-json csr.json \
    --scala-file gen.scala --scala-file $A/litex/naxriscv_l1.scala \
    --scala-args icache-bytes=32768,icache-ways=8,dcache-bytes=32768,dcache-ways=8,icache-mem-bits=256,dcache-mem-bits=256,icache-reduced-banks=1
```

- NaxRiscv is an out-of-order RV64GC core of SpinalHDL. The build needs JDK 17 and sbt, in addition
  to LiteX. At the first build, LiteX clones NaxRiscv `9f452d5` with SpinalHDL `c362657` into its
  `pythondata-cpu-naxriscv` package. sbt then makes the Verilog of the core.
- Each core has 32 KiB of L1 instruction cache and 32 KiB of L1 data cache, each with 8 ways
  (`naxriscv_l1.scala`). The cores share one coherent L2 cache of 512 KiB in front of LiteDRAM
  (`NaxSoc.scala` of NaxRiscv).
- The L1 caches refill through buses of 256 bits. Thus a line of 64 bytes takes 2 beats
  ("Refill buses and the system clock").
- The CPU runs at the system clock, 110 MHz. Thus the DDR3 runs at DDR3-880. The SD card then runs
  at 27.5 MHz and not at 50 MHz ([linux/README.md](linux/README.md), "SD card").
- The 8 KiB default SRAM left less than 2 KiB for the stack of the BIOS. Thus the SRAM is 16 KiB.
- The Ethernet MAC is the MAC with DMA ("Ethernet and the SD card"). The BIOS thus boots only from
  the SD card.
- The build on 2026-11-22 was -2 at 110 MHz with Vivado 2025.2.1. The setup slack was +0.672 ns,
  with 72,357 LUT, 49,249 FF and 285.5 block RAM tiles. The slack of the clock `ac97_bit_clk` was
  +1.606 ns.

### Buses of NaxRiscv in LiteX

- The L2 cache goes to LiteDRAM through its own AXI4 bus of 256 bits (`mBus`), and not through the
  SoC bus.
- The peripherals (the CSRs, the SRAM slots of the MAC, the ROM and the SRAM) are on the AXI-Lite
  bus `pBus` of 32 bits. LiteX converts it to its Wishbone SoC bus.
- The DMA masters are on the DMA bus of LiteX, an AXI4 bus of 64 bits. The MAC with DMA is an AXI4
  master of 64 bits. LiteSDCard and AC97 are Wishbone masters, and LiteX converts each transfer
  into one AXI4 beat.
- NaxSoc makes its coherent DMA port as wide as the refill bus of the D-cache: 256 bits in the
  reference SoC. The CPU wrapper of LiteX connects only 64 bits. Thus the target puts one
  `AXIUpConverter` between the arbiter of the DMA bus and the port.
- The arbiter of the DMA bus changes master only after all responses came back. Thus the responses
  stay in the order that the converter needs.

Thus `--bus-standard axi` changes only the path from the CPU to the peripherals. The LiteEth MAC has
only a Wishbone interface, so a converter stays in front of its slots.

### Size of the cores and of the L2

The builds of 2026-11-21, as -2 at 100 MHz, without the AC97 controller and with 2 RX slots:

| Cores, L2 | Setup slack | LUT | FF | Block RAM tiles | LUT of NaxRiscv alone |
|---|---|---|---|---|---|
| 1, 128 KiB | +1.100 ns | 34,916 | 25,965 | 91.5 | 29,067 |
| 2, 128 KiB | +1.204 ns | 60,719 | 41,991 | 103.5 | 50,458 |
| 2, 512 KiB | +0.932 ns | 60,951 | 42,090 | 203.5 | 50,690 |

- A second core adds about 25,800 LUT. Three cores would use about 87% of the LUTs, and four cores
  do not fit. The FF, block RAM and DSP of the device are not the limit.
- An L2 of 1 MiB needs about 256 more RAMB36 for its data alone, and the device has 325.

The effect of the L2 size **[measured, 2026-11-21]**: two cores at 100 MHz, with the same Debian.
The script ran `sysbench memory` (reads, one thread, 512 MiB in total) and `7z b 1 -md22` (a
dictionary of 4 MiB).

| Test | L2 128 KiB | L2 512 KiB |
|---|---|---|
| sysbench, block of 32 KiB | 253.7 MiB/s | 255.4 MiB/s |
| sysbench, block of 256 KiB | 190.2 MiB/s | 276.2 MiB/s |
| sysbench, block of 1 MiB | 193.3 MiB/s | 194.8 MiB/s |
| sysbench, block of 8 MiB | 196.1 MiB/s | 196.4 MiB/s |
| 7z, 1 thread: compression, decompression, total rating | 84 KB/s, 960 KB/s, 82 MIPS | 91 KB/s, 980 KB/s, 86 MIPS |
| 7z, 2 threads: compression, decompression, total rating | 122 KB/s, 1844 KB/s, 138 MIPS | 122 KB/s, 1909 KB/s, 141 MIPS |

- A block of 256 KiB fits in the larger L2 only, and its read speed increased by 45%.
- Blocks of 1 MiB and more come from the DDR3 with both sizes, at about 195 MiB/s.
- The 7z rating increased by 5% for one thread and by 2% for two threads.

### Size of the L1 caches

`naxriscv_l1.scala` changes the L1 caches of NaxRiscv. Give it after the `gen.scala` of LiteX, with
the sizes as `--scala-args`:

```sh
--scala-file gen.scala --scala-file $A/litex/naxriscv_l1.scala \
--scala-args icache-bytes=32768,icache-ways=8,dcache-bytes=32768,dcache-ways=8
```

Each way stays at 4 KiB, the page size, because the caches are indexed with the virtual address.
The target then writes the same sizes into the DTS.

The reference SoC has 32 KiB and 8 ways for both caches. The comparison is a build with the default
caches (16 KiB, 4 ways) and the same other options, made on 2026-11-21. Its setup slack was
+0.751 ns, with 62,166 LUT, 43,058 FF and 208.5 block RAM tiles. Thus the larger caches use 2,581
more LUT and 16 more block RAM tiles.

Results **[measured, 2026-11-22]**. The same kernel and Debian ran on both SoCs after a JTAG load.
`perf stat` counted each workload, with no other workload on the board
([linux/README.md](linux/README.md)).
MPKI is the number of refills for each 1000 instructions.

| Workload | Time, 16 KiB | Time, 32 KiB | IPC | I$ MPKI | D$ MPKI |
|---|---|---|---|---|---|
| `7z a -mx5`, 4 MiB | 67.2 s | 61.3 s (−8.8%) | 0.84 → 0.91 | 1.80 → 0.99 | 13.4 → 9.8 |
| `7z t`, 4 times | 9.85 s | 9.52 s (−3.3%) | 0.94 → 0.97 | 2.26 → 1.12 | 4.18 → 3.02 |
| `xz -dc`, 4 times | 10.5 s | 9.97 s (−5.3%) | 0.96 → 1.00 | 3.02 → 1.71 | 4.69 → 3.46 |
| `sha256sum`, 16 MiB | 7.02 s | 6.87 s (−2.1%) | 1.74 → 1.77 | 1.05 → 0.71 | 1.95 → 1.43 |
| `gcc -O2`, two C files | 72.6 s | 55.1 s | 0.53 → 0.59 | 25.7 → 17.5 | 16.2 → 10.7 |
| Perl, regexes and hashes | 33.8 s | 21.1 s (−37.7%) | 0.54 → 0.84 | 38.5 → 8.5 | 10.3 → 2.8 |

- The counters include the kernel and all processes. In the gcc runs the instruction count was
  16% different, so the time of gcc is not a good comparison. Its IPC increased by 11%.
- Code with a large footprint gains most: the Perl interpreter had 78% fewer I$ refills.
- A pointer chase gave 3.1 cycles for an L1 hit and about 28.5 cycles for an L2 hit with both
  SoCs. The 32 KiB D$ moved the step from 16 KiB to 32 KiB of data.
- Both SoCs ran at 100 MHz with refill buses of 64 bits.

### Refill buses and the system clock

`naxriscv_l1.scala` also sets the width of the refill buses between the L1 caches and the L2
cache: `icache-mem-bits` and `dcache-mem-bits`. The default of NaxRiscv is 64 bits.

A simulation found the parts of an L2 hit. It used `litex_sim` of LiteX with Verilator, the cores
and caches of the reference SoC, refill buses of 64 bits, and no DMA masters. A program without
the MMU chased pointers through 256 KiB with a stride of 64 bytes. It gave 28.0 cycles for each
load; Linux on the board gave 28.5. The trace of one load gave these parts. The program is not in
this repository.

| Part | Cycles |
|---|---|
| The A request through the pipeline stages of the D-cache bus and of the L2 input | 2 |
| The L2 cache: the tags, then the data | 5 |
| The D response through the pipeline stages back to the D-cache | 2 |
| The other 7 beats of the line of 64 bytes | 7 |
| The D-cache completes the refill, and the load runs again | 5 |
| The next load: wake-up, dispatch, address, and the miss in the D-cache | 7 |

- The D-cache waits for all of the line before the load runs again. Thus the beats of the line are
  7 of the 28 cycles.
- With refill buses of 128 bits, the simulation gave 24.0 cycles. With 256 bits, it gave 22.0.

A wide refill bus makes each way of an L1 cache a bank of the same width, and that uses more block
RAM. `icache-reduced-banks=1` keeps the banks of the I-cache at 64 bits. The D-cache of NaxRiscv
`9f452d5` has no such option: its writeback path does not implement it.

The builds of 2026-11-22, -2 with the other options of the reference SoC. The two middle rows had a
converter for each DMA master, and not the one converter of the target:

| System clock, refill buses | Setup slack | LUT | FF | Block RAM tiles |
|---|---|---|---|---|
| 100 MHz, 64 bits | +0.109 ns | 64,973 | 45,340 | 219.5 |
| 110 MHz, 64 bits | +0.049 ns | 65,293 | 45,342 | 219.5 |
| 100 MHz, 256 bits | +0.143 ns | 81,156 | 49,714 | 323.5 |
| 110 MHz, 128 bits | +0.135 ns | 69,504 | 46,796 | 257.5 |
| 110 MHz, 256 bits, I-cache banks of 64 bits (the reference SoC) | +0.672 ns | 72,357 | 49,249 | 285.5 |

Results **[measured, 2026-11-22]**. The same kernel and Debian ran on each SoC after a JTAG load,
with a DTB of the same system clock. `lat` chased pointers in a random order with a stride of 64
bytes, under Linux. The time is the geometric mean of the six workloads below, compared with the
first row.

| System clock, refill buses | L1 hit | L2 hit (128 KiB) | DDR3 (32 MiB) | Time |
|---|---|---|---|---|
| 100 MHz, 64 bits | 31.3 ns | 284.9 ns (28.5 cycles) | 904.2 ns | — |
| 110 MHz, 64 bits | 28.1 ns | 258.6 ns (28.4 cycles) | 794.2 ns | −10.3% |
| 100 MHz, 256 bits | 30.9 ns | 223.5 ns (22.4 cycles) | 763.7 ns | −5.7% |
| 110 MHz, 128 bits | 28.1 ns | 221.1 ns (24.3 cycles) | 726.6 ns | −13.3% |
| 110 MHz, 256 bits (the reference SoC) | 27.9 ns | 202.8 ns (22.3 cycles) | 694.4 ns | −14.4% |

The workloads on the first row and on the reference SoC. `perf stat` counted each workload, with no
other workload on the board:

| Workload | 100 MHz, 64 bits | The reference SoC | IPC |
|---|---|---|---|
| `7z a -mx5`, 4 MiB | 62.8 s | 53.5 s (−14.9%) | 0.90 → 0.96 |
| `7z t`, 4 times | 9.60 s | 8.47 s (−11.8%) | 0.96 → 0.99 |
| `xz -dc`, 4 times | 10.2 s | 8.84 s (−13.2%) | 0.99 → 1.03 |
| `sha256sum`, 16 MiB | 7.07 s | 6.26 s (−11.4%) | 1.73 → 1.77 |
| `gcc -O2`, two C files | 60.8 s | 49.6 s (−18.4%) | 0.60 → 0.67 |
| Perl, regexes and hashes | 22.2 s | 18.6 s (−16.3%) | 0.81 → 0.88 |

- The clock alone gave 10.3%. The wide buses also decreased the cycles of each workload.
- The buses of 256 bits also decreased the time of a DDR3 access, because the L2 cache refills
  through the same width.
- The worst path of the first row was a route of 8.75 ns from an address register of the L2 cache
  to its data RAM. The build at 110 MHz met timing without a change to that path.
- At 110 MHz LiteDRAM calibrated the DDR3 at 880 MT/s, and the memtest of the BIOS passed.
- A branch that the predictor misses costs about 14.6 cycles (a loop on random data).

### Power estimate

The estimate of `report_power` is vectorless, Vivado gives it the confidence "Low", and it assumes
25 °C ambient air with a heat sink. It does not include the DDR3 devices, the PHY or the regulators.

| Supply | 1 core, 128 KiB L2 | 2 cores, 128 KiB L2 |
|---|---|---|
| Vccint (1.0 V) | 0.649 A | 0.928 A |
| Vccaux (1.8 V) | 0.311 A | 0.310 A |
| Vcco 1.5 V (DDR3 banks) | 0.242 A | 0.237 A |
| Vcco 3.3 V | 0.002 A | 0.002 A |
| Total on the chip | 1.624 W | 1.895 W |

With the 512 KiB L2, the estimate for 2 cores is 1.963 W.

## DDR3 with LiteDRAM

- LiteDRAM controls the DDR3 with `K7DDRPHY` at a ratio of 1:4. Thus the data rate is 8 times the
  system clock. The default is 175 MHz: DDR3-1400.
- The MMCM uses `SYSCLK` (200 MHz, `DIFF_SSTL15`). It makes the system clock and the 4× clock.
- `IDELAYCTRL` gets `SYSCLK` itself through a BUFG as its 200 MHz reference. Thus the MMCM needs no
  200 MHz output, and the system clock can be 110 MHz.
- The controller uses the timings of `MT41J256M16`. MIG uses the same substitute for the
  PieceMakers part ([docs/ddr3.md](../docs/ddr3.md)).
- The I/O standards are the ones of MIG in `pins.csv`. The platform does not set
  `VCCAUX_IO=HIGH`, because the FBG676 package has no 2.0 V VccAuxIO.
- The platform adds `DCI_CASCADE {32}` to bank 33 when a design uses the DDR3.

**DDR3-1600 is not possible.** `K7DDRPHY` sends the 4× clock through a BUFG. In the timing report of
Vivado, the minimum period of `BUFG/I` is 1.408 ns for -2, which is 710 MHz. Thus the data rate
cannot be more than 1420 MT/s. The rates that were tried on 2026-11-21, all as -2 with `--with-bist`:

| Rate | System clock | Result of the build |
|---|---|---|
| 1600 | 200 MHz | Timing not met. Pulse width slack −0.158 ns at the BUFG of the 4× clock |
| 1333 | 166.67 MHz | No build. LiteX found no MMCM setting that makes 166.67, 666.67 and 200 MHz from 200 MHz. The target now takes the 200 MHz reference from `SYSCLK`, but this rate was not built again |
| **1400** | 175 MHz | Setup slack +0.339 ns, pulse width slack +0.020 ns. 7,049 LUT, 5,449 FF |
| 800 | 100 MHz | Setup slack +1.583 ns. 6,970 LUT, 5,409 FF |

Results on the board **[measured, 2026-11-21]**. The BIST is the generator and the checker of
LiteDRAM on the native port (256 bits). It does not use the CPU.

| Rate | CL / CWL | BIST write | BIST read | Peak | VexRiscv write / read (`mem_speed`) |
|---|---|---|---|---|---|
| 800 | 6 / 5 | 2,568 MiB/s | 2,555 MiB/s | 3,052 MiB/s | 63.7 / 62.4 MiB/s |
| 1400 | 11 / 8 | 4,659 MiB/s | 4,615 MiB/s | 5,341 MiB/s | 112.4 / 108.5 MiB/s |

- The BIST figures are from `sdram_bist 1024 0`, which ran for 25 seconds. With random base
  addresses (`sdram_bist 1024 1`), the figures were almost the same. The BIST found no errors in
  about 30 GiB at 800 and 54 GiB at 1400.
- `sdram_hw_test 0 0x40000000 1024` covers all of the 1 GiB. It found 0 errors at both rates.
- The VexRiscv figures are low because each store of VexRiscv is one 32-bit Wishbone transfer.
  NaxRiscv reads blocks from the DDR3 at about 195 MiB/s (see the L2 table above).
- Calibration at 800: read leveling found windows of 12 to 15 IDELAY taps.
- Calibration at 1400: all 6 calibrations completed, but read leveling found windows of only 5 or 6
  taps, in bitslips 4, 5 and 6. One tap is 78 ps with a 200 MHz reference (UG471). Thus a window of
  5 taps is about 0.39 ns, and one bit at 1400 MT/s is 0.71 ns.

These are results for one chip at room temperature.

## Ethernet and the SD card

### The MAC with DMA

`adp_ethmac.py` keeps the MAC core of LiteEth (preamble, CRC, padding, gap), and replaces its slot
memory with a DMA front end. The front end has an AXI4 master of 64 bits on the coherent DMA bus of
NaxRiscv. The CPU then does not copy the frames ([linux/README.md](linux/README.md)).

- Receive: a byte FIFO of 4 KiB, then single AXI writes into the buffers that the driver gives
  through a FIFO of 256 entries. The engine starts the frame 2 bytes after the buffer address, and
  sums the frame for `CHECKSUM_COMPLETE`. A completion FIFO gives the length, an error bit and the
  sum. If the byte FIFO has no room for a frame of 1536 bytes, the engine drops the frame and
  counts it.
- Transmit: a FIFO of 64 descriptors, one for each piece of a frame, at any byte address. The engine
  reads the pieces with single AXI reads into one of two frame buffers of 2 KiB. It writes the
  checksum that `CHECKSUM_PARTIAL` asks for, and then sends the frame.
- 100 Mbit/s needs one 64-bit beat in 640 ns, which is 64 cycles at 100 MHz. Thus the engine makes
  one AXI transfer at a time, without bursts.
- The CSRs keep the base address and the interrupt of the LiteEth MAC, so the other addresses of
  the SoC do not move.

Before the first build, a testbench of migen simulated the front end with an AXI memory of random
latency. It sent frames of 14 to 1600 bytes and pieces at random byte addresses, and all were
correct. The testbench is not in this repository.

The builds of 2026-11-22, -2 at 100 MHz, with the other options of the reference SoC, but with
refill buses of 64 bits:

| Ethernet | Setup slack | LUT | FF | Block RAM tiles |
|---|---|---|---|---|
| LiteEth, 8 RX and 4 TX slots | +0.285 ns | 64,747 | 44,798 | 224.5 |
| LiteEth, 16 RX and 8 TX slots | +0.371 ns | 65,125 | 44,815 | 232.5 |
| The MAC with DMA (`--with-ethernet-dma`) | +0.109 ns | 64,973 | 45,340 | 219.5 |

The slack of the MAC with DMA is the lowest, but its worst path is in the L2 cache of NaxRiscv: a
route of 8.75 ns to a block RAM of the L2 data. Thus the placement, and not the MAC, sets it.

### Limits

Limits of the Ethernet options:

- The `eth` resource has no `COL`, `CRS` and `RX_ER`, and no pin of the PHY has `PULLUP`. Vivado
  puts its weak pull-down on the unused strap pins, which gives address 0 and MII
  ([docs/ethernet.md](../docs/ethernet.md)).
- Etherbone has `buffer_depth=255`. With the default of 16, Etherbone dropped each record of more
  than 16 words without an error. `CommUDP.write` of LiteX does not split a write into records.
  A record holds at most 255 words.
- The ICMP echo of LiteEth answers only small packets. Its FIFO has 128 bytes
  (`liteeth/core/icmp.py`).

To use Etherbone from the host:

```sh
litex_server --udp --udp-ip A.B.C.D        # listens on localhost:1234
litex_cli --regs                           # reads the CSRs; or use litex.RemoteClient in Python
```

Results **[measured, 2026-11-21]**, with the SoC at 175 MHz with `--with-etherbone --with-sdcard`
on 100BASE-TX:

- MDIO (BIOS `mdio_dump 0 7`): BMCR `3100`, BMSR `7869` (link up, autonegotiation complete), PHY
  ID `0181 B8B1`. Thus the PHY took address 0.
- Ping with 56 and 100 bytes of data: no loss, 0.077 ms average. Ping with 200 bytes or more got no
  answer, because of the ICMP FIFO.
- Etherbone wrote and read back bursts of 1 to 255 words and 1 MiB of random words in the DDR3
  without error. Through `litex_server`, the write of 1 MiB ran at 11.15 MiB/s (about 94 Mbit/s).
  The read through `litex_server` is slow because of the Python code of the host.
- `sdcard_init` passed. Blocks were read at 399 kHz and at 21.875 MHz, and the data was the same.
  The BIOS asks for 25 MHz; the divider makes 21.875 MHz from 175 MHz.

## The AC97 controller

`--with-ac97` adds `adp_ac97.py`, a controller for the ALC203 codec ([docs/audio.md](../docs/audio.md)).
The start of `adp_ac97.py` gives its CSRs.

- The codec drives `AC97_BCLK` at 12.288 MHz. K26 is not a clock-capable pin. Thus the clock goes
  from the IBUF to a BUFG on general routing (`CLOCK_DEDICATED_ROUTE FALSE`).
- The controller sends `AC97_SYNC` and `AC97_SDO` on the rising edge. It samples `AC97_SDI` on the
  falling edge, in a flip-flop with an inverted clock.
- It uses slots 0 to 4 at 48 kHz: the tag, the codec command, and the left and right samples. It
  does not use VRA.
- Two DMA engines of LiteX read and write ring buffers in the main RAM, through the coherent DMA bus
  of NaxRiscv. One 32-bit word is one stereo frame of S16_LE. Each engine gives an interrupt after
  each period.
- After configuration, `ctrl.reset` is 1. Then `AC97_RSTn`, `AC97_SYNC` and `AC97_SDO` are low,
  which agrees with the reset condition of the codec.
- The AC97 CSRs are first in the CSR map. The Linux driver uses their offsets, and
  `linux/mkdts.py` checks them.

## Problems that were found

**[measured, 2026-11-21]** These are problems of LiteX at the commits of `versions.txt`, and not of
the board.

- The BIOS command `ident` shows `-`. The banner shows the identifier correctly. The BIOS reads the
  macro `CSR_IDENTIFIER_MEM_BASE_VA`, and LiteX does not generate it.
- `litex_json2dts_linux` accepts `--initrd-start` only as a decimal number.
- After the BIOS command `reboot`, the host read the banner as wrong characters. The text after a
  pause was correct. A probable cause: the reset stopped a character in the middle, and the UART
  of the host then took a data bit as a start bit. A JTAG load gives a correct banner.
