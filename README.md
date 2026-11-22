# Andes ADP-XC7K160 board notes

This repository records what is known about one Andes AndeShape ADP-XC7K160 board (green PCB, Xilinx
XC7K160T). It has the pins, the clocks, the configuration, the memory and the peripherals, with the
measurements and the problems that were found. A project that uses this board can take these files
as they are. It also has a LiteX SoC that runs Debian on the board.

Each statement has one of three sources. The text marks them:

| Mark | Meaning |
|---|---|
| **[measured]** | A design ran on this board and showed it, or an instrument measured it (2026-11-18 to 2026-11-21) |
| **[package]** | Checked against the `xc7k160tfbg676` package data of Vivado 2025.2.1 |
| **[manual]** | From the Andes manual UM098 v1.9 only. Not verified on the board |

There is no public schematic. If the manual does not say something and no measurement shows it, the
text says that it is not known.

## The board

| Part | Summary | Details |
|---|---|---|
| FPGA | XC7K160T-FBG676, marked -1. The designs here are built as -2 | [docs/fpga.md](docs/fpga.md) |
| Banks and power | 3.3 V HR banks, 1.5 V DDR3 banks; SW11 sets bank 16 (now 3.3 V). No user LED | [docs/power.md](docs/power.md) |
| Clocks | 20 MHz `OSCCLK1`, 200 MHz `SYSCLK`, 100 MHz `CKOUT1`, 32.768 kHz. `OSCCLK2` is not fitted | [docs/clocks.md](docs/clocks.md) |
| Configuration | Master BPI x16 from U4, a 128 MB NOR flash | [docs/configuration.md](docs/configuration.md) |
| JTAG | J3 (6-pin) and J4 (14-pin); used here through xvc-pico | [docs/jtag.md](docs/jtag.md) |
| UART | COM1 (S1) through an RS-232 transceiver, up to 500000 baud | [docs/uart.md](docs/uart.md) |
| DDR3 | 1 GiB, 32 bits. MIG works up to DDR3-1600, LiteDRAM up to DDR3-1400 | [docs/ddr3.md](docs/ddr3.md) |
| Buttons, displays, resets | 8 buttons without pull-ups, two seven-segment digits, `PORn` after each configuration | [docs/buttons-displays-resets.md](docs/buttons-displays-resets.md) |
| U17, U16 | 2 MiB SPI flash at 1.8 V; 16 KB I2C EEPROM | [docs/flash-eeprom.md](docs/flash-eeprom.md) |
| Ethernet | 10/100 with a DM9161CEP PHY (MII). The Gigabit PHY is not fitted | [docs/ethernet.md](docs/ethernet.md) |
| Audio | ALC203 AC97 codec | [docs/audio.md](docs/audio.md) |
| SD card | 4-bit bus and SPI mode | [docs/sd-card.md](docs/sd-card.md) |
| Other parts | LCD, IDE/CF, AICE, trace, PCIe: not used. USB: not fitted | [docs/peripherals.md](docs/peripherals.md) |
| Vivado | The flow and the problems that were found | [docs/vivado.md](docs/vivado.md) |
| Not known | The open questions | [docs/unknowns.md](docs/unknowns.md) |

Read these points before you use the board:

- **Switch off the board before you connect or disconnect the JTAG cable.**
- **A design that does not use U4 drives `FCS_B`, `FOE_B` and `FWE_B` high.** Write U4 only when its
  contents may change: only the backup of the owner can restore the factory design.
- **Do not put `PULLUP` on the pins of the Ethernet PHY.** The PHY reads them as straps.
- **The buttons need `PULLUP TRUE`** in the XDC.
- **Bank 16 depends on SW11.** If its position is not known, declare the inputs as `LVCMOS18`.

## Files

| File | Contents |
|---|---|
| `docs/` | The description of the board, one file for each part (the table above) |
| `AGENTS.md` | The rules for work on this repository |
| `LICENSE` | The MIT License, which applies to all files here |
| `pins.csv` | The pin table of section 2.11 of the manual (445 rows, 440 pins), with the package pin function, the I/O standard, whether the pin was measured, and notes |
| `package-pins.csv` | All 676 package pins: bank, function name, package trace delay, and what the board connects |
| `adp_xc7k160_master.xdc` | Master XDC. Every port is a comment; remove the comment mark from the ports that you use |
| `mig/mig_prj.py` | Makes the MIG project file `mig.prj` from `pins.csv` (DDR3-800, 1066, 1300, 1600, 1800) |
| `mig/prj/mig-ddr3-*.prj` | The output of that script, one file for each rate. MIG can read them directly |
| `mig/mig.tcl`, `mig/generate.sh` | Generate MIG with Vivado, for a non-project flow |
| `jtag/load.sh`, `jtag/load.tcl` | Load a bitstream into the FPGA through XVC. They do not touch a flash |
| `jtag/flash_u4.sh`, `jtag/flash_u4.tcl` | Write a bitstream into the configuration flash U4 through XVC ([docs/configuration.md](docs/configuration.md)) |
| `jtag/xvc_scan.py` | Reads the IDCODE, the configuration status, and the XADC temperature and voltages through XVC. It only reads |
| `flash-probes/` | Two designs that read the flash devices U4 and U17 through JTAG, and their host scripts. They only read |
| `example-ddr3-test/` | An example that builds alone: MIG, a 1 GiB memory test and a UART report. It shows the clocks, the UART, MIG and the configuration flash pins |
| `tools/` | The script that makes `pins.csv` and `package-pins.csv`, and its two inputs (the pin table of the manual, the package data of Vivado) |
| `workbench/` | A design and a host script that measure the board: see [workbench/README.md](workbench/README.md) |
| `litex/` | A LiteX SoC for this board: see [litex/README.md](litex/README.md). Linux and Debian on it: see [litex/linux/README.md](litex/linux/README.md) |

Not in this repository:

- The manual (`AndeShape_ADP-XC7K160_410_UM098_V1.9.pdf`). It is a document of Andes.
- The backups of the two flash devices (U4 and U17). The contents of U4 are software of Andes.
  `flash-probes/` has the designs that made the backups.
- An earlier pin CSV and master XDC of this board. Their header says FFG676, and they give the DDR3
  as "2x K4B4G1646B". Both are wrong. `tools/manual-pins.csv` came from that CSV; the files here
  are corrected.

The scripts do not write into the repository. They write their products to the current directory,
or to the directory that you name. The start of each script gives its usage.

## Quick start

```sh
export VIVADO=/path/to/Xilinx/2025.2.1/Vivado/bin/vivado
export XVCD=/path/to/xvc-pico/daemon/xvcd-pico
A=/path/to/this/repository

cd /some/writable/directory
sh $A/example-ddr3-test/build.sh                       # MIG, synthesis, routing: about 5 minutes, into ./adp-ddr3-test
sh $A/jtag/load.sh adp-ddr3-test/build/ddr3-test.bit   # loads the FPGA in about 20 seconds; no flash is touched
python3 $A/example-ddr3-test/ddr3_test.py -p /dev/ttyS0 check 5
python3 $A/jtag/xvc_scan.py                            # xvcd-pico must be running
```

These steps ran from the contents of this repository on 2026-11-20 **[measured]**. The build was
DDR3-1600 as speed grade -2: setup slack +0.183 ns, 8,009 LUT, 6,860 FF. After the load, 5
calibrations passed (389 to 391 ms). After each one, two passes of 1 GiB had no errors. The tester
counted the errors that the test injected.

## License

The files of this repository are under the MIT License: see [LICENSE](LICENSE).

The repository does not contain the manual of Andes, the flash contents of the board or the output
of Xilinx tools other than the reports and bitstreams of its own designs. `tools/manual-pins.csv`
gives the pin assignments of section 2.11 of the manual as facts.
