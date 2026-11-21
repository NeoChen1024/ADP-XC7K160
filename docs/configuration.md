# Configuration and the configuration flash U4

## Configuration

- Configuration mode: **Master BPI x16**. The mode pins read `010` **[measured]**. At power-on the
  FPGA loads the bitstream in U4. A design that JTAG loads stays until power-off.
- The XDC must have these two lines **[measured, all the designs have them]**:

  ```tcl
  set_property CFGBVS VCCO [current_design]
  set_property CONFIG_VOLTAGE 3.3 [current_design]
  ```

  For a slow JTAG cable, add `set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]`. A
  design that uses one seventh of the device then has a 2.5 MB bitstream. The full size is 6.7 MB.
- Related pins **[package]**: `DONE` J7 (LED7 shows DONE), `INIT_B` G7, `PROGRAM_B` P6 (pull-up on
  the board).

## U4

**U4** is a 128 MB parallel NOR flash, x16. The Vivado configuration memory part is
`28f00am29ew-bpi-x16` **[measured]**: Vivado 2025.2.1 lists it, and it wrote U4 with it. An earlier
version of this text gave `28f00am29ew-bbp-x16`, which was wrong. Pins: `FCS_B` C23, `FOE_B` M17,
`FWE_B` L18, `FA[0..24]`, `FDQ[0..15]` (see `pins.csv`).

- **A design that does not use the flash drives `FCS_B`, `FOE_B` and `FWE_B` high and leaves the
  other flash pins unconnected** **[measured]**. After configuration these three pins are general
  I/O. Vivado puts a weak pull-down on a pin that the design does not use. Without a driver, the
  three active-low control pins all go low together.
- **FA25 is not the A25 pin of the FPGA.** The highest address line of the flash is on T18, a
  general I/O that the manual calls `FA25_PU`. The board pulls it low through 330 Ω. The A25
  configuration pin of the FPGA (E20) connects to the busy output of the flash (`F_BUSY_B`). Thus:
  - During configuration FA25 is low. The FPGA sees only the lower half of the flash (the first
    64 MB). The bitstream is there.
  - The upper half (the last 64 MB) is the "system storage" of the manual. A design that reads or
    writes it must drive T18 high.
  - **A read that drives only FA[24:0] reads the lower half twice.** An earlier backup of this
    board made that mistake: its two halves were identical.
- `RS0` (K18) and `RS1` (L17) connect through SW12 to the same flash pins as FA23 and FA24 (U4.1,
  U4.56) **[manual]**. All four positions of SW12 are off (seen). The workbench agrees: `RS0` and
  `RS1` follow the pull, and they do not move when the workbench drives `FA23` and `FA24`.
- Byte order of a flash file: `99 55 66 AA` in the flash image is the sync word `AA 99 55 66` of a
  `.bit` file. The bits in each byte are reversed, and then each pair of bytes is swapped.

The contents of U4 **[measured]**. The factory contents were read completely on 2026-11-20, and two
reads agreed. The writes of 2026-11-21 changed only the start of the lower half:

| Location | Contents |
|---|---|
| `0x0000000–0x046D9C7` | The LiteX SoC of [litex/linux/README.md](../litex/linux/README.md): two NaxRiscv cores, Debian from the SD card |
| `0x0480000–0x04BFFFF` | The end of an earlier LiteX bitstream. Alone it is not a bitstream |
| `0x04C0000–0x0680000` | The end of the factory bitstream. Alone it is not a bitstream |
| `0x4000000–0x40A0000` | Andes boot monitor (factory) |
| `0x4100000–0x4120000` | `U-Boot 2010.12-rc1 (Jul 22 2011 - 16:10:26)` (factory) |
| `0x4400000–0x4D80000` | A compressed image with its decompressor (factory) |
| `0x5600000`, `0x5F00000` | One 128 KiB block each, with no text (factory) |
| The remainder | Blank (`FF`) |

The upper half was not read again after the writes. The factory bitstream was at
`0x0000000–0x0680000`, the factory design of Andes with a core older than their RISC-V cores. The
DVD that came with the board (AndeSight and the BSP) does not contain these images. The backup of
the owner is the only copy.

## Read U4

To read U4 through JTAG, use `flash-probes/`:

```sh
sh flash-probes/run.sh u4 probe            # 16 words at each of five addresses
sh flash-probes/run.sh u4 out.bin          # all 128 MiB, about 11 minutes
```

The probe design (`flash-probes/u4/bpijtag.v`) cannot write the flash. It holds `FWE_B` high, and
its data pins are inputs only. It drives all 26 address bits, with FA25, so it reads both halves.
One read has no check. Read two times with two different scan lengths, and compare the files.

## Write U4

**Rule: write U4 only when the owner explicitly asks for that write.** A write changes what the board
does at power-on, and only the backup can restore the factory design. `jtag/flash_u4.sh` is the only
tool here that writes U4:

```sh
VIVADO=... XVCD=... sh jtag/flash_u4.sh design.bit
```

- It makes an MCS file of the bitstream at address 0. It refuses a file that ends above `0x4000000`,
  because FA25 keeps the upper half out of reach.
- Vivado loads its BPI programming design through JTAG. Then it erases the blocks of the file,
  writes them and verifies them. At the end, PROGRAM_B makes the FPGA load the new contents of U4.
- If the write fails, the FPGA does not configure at power-on. JTAG still works, so `load.sh` and a
  new write still work.

Result **[measured, 2026-11-21]**: three writes passed the erase, the write and the verify. Through
xvc-pico, each complete run took 109 to 130 seconds. After PROGRAM_B, DONE was 1, and the SoC
booted. After a power cycle, the FPGA loaded the SoC from U4.

To restore the factory design, convert the first `0x680000` bytes of the backup to the byte order of
a `.bit` file (see above). Then write them at address 0, for example with `write_cfgmem -loaddata`.
This was not tried.
