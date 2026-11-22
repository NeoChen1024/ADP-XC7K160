# What is not known

Items that need a person at the board:

- None at this time.

Other items:

- The polarity of `PHY_PWRDWN`: it is known only that low is normal operation
  ([ethernet.md](ethernet.md)).
- Ethernet was tested only at 100BASE-TX full duplex. 10 Mbit/s and half duplex (`MAC_COL`,
  `MAC_CRS`) were not tested.
- The SD card was tested only with one card. The workbench used 238 kHz. Linux on NaxRiscv used
  25 MHz and 50 MHz (high speed mode), from 2026-11-22 for less than one day at 50 MHz. The timing
  margin at 50 MHz is not known.
- The margin above the -1 marking (400 MHz, DDR3-1600): there is a result for one chip at room
  temperature only ([fpga.md](fpga.md)).
- Debian on NaxRiscv ([litex/linux/README.md](../litex/linux/README.md)): it ran for less than one
  day in total, at room temperature.
- LiteX at DDR3-1400 ([litex/README.md](../litex/README.md)): the read window is about 5 IDELAY
  taps. The memory had one test of all 1 GiB and at least two runs of 54 GiB of BIST. Long use and
  other temperatures were not tested.
- The data lines of the LCD, IDE/CF, AICE, trace and PCIe were not used.
- AC97 in Linux: only playback and the capture of `Mix` were tested, at 48 kHz. The line input and
  the microphone input were not tested.
- The branch misses of NaxRiscv: its event 4 does not count them
  ([litex/linux/README.md](../litex/linux/README.md)), so their share of the time is not known.
- Ethernet in Linux: the patch of `liteeth` ran for less than one hour. With traffic in both
  directions, the MAC dropped some frames with 8 RX slots.
