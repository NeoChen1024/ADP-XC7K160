# Andes ADP-XC7K160 board notes

This repository records what is known about one Andes AndeShape ADP-XC7K160 board (green PCB, Xilinx
XC7K160T) as of 2026-11-20. It has the pins, the clocks, the configuration, JTAG, the UART, and the
DDR3 memory with its MIG parameters. It also has the measurements and the problems that were found.
A project that uses this board can take these files as they are.

Each statement has one of three sources. The text marks them:

| Mark | Meaning |
|---|---|
| **[measured]** | A design ran on this board and showed it, or an instrument measured it (2026-11-18 to 2026-11-20) |
| **[package]** | Checked against the `xc7k160tfbg676` package data of Vivado 2025.2.1 |
| **[manual]** | From the Andes manual UM098 v1.9 only. Not verified on the board |

There is no public schematic. If the manual does not say something and no measurement shows it, the
text says that it is not known.

## Files

| File | Contents |
|---|---|
| `README.md` | This document |
| `AGENTS.md` | The rules for work on this repository |
| `LICENSE` | The MIT License, which applies to all files here |
| `pins.csv` | The pin table of section 2.11 of the manual (445 rows, 440 pins), with the package pin function, the I/O standard, whether the pin was measured, and notes |
| `package-pins.csv` | All 676 package pins: bank, function name, package trace delay, and what the board connects |
| `adp_xc7k160_master.xdc` | Master XDC. Every port is a comment; remove the comment mark from the ports that you use |
| `mig/mig_prj.py` | Makes the MIG project file `mig.prj` from `pins.csv` (DDR3-800, 1066, 1300, 1600, 1800) |
| `mig/prj/mig-ddr3-*.prj` | The output of that script, one file for each rate. MIG can read them directly |
| `mig/mig.tcl`, `mig/generate.sh` | Generate MIG with Vivado, for a non-project flow |
| `jtag/load.sh`, `jtag/load.tcl` | Load a bitstream into the FPGA through XVC. They do not touch a flash |
| `jtag/xvc_scan.py` | Reads the IDCODE, the configuration status, and the XADC temperature and voltages through XVC. It only reads |
| `flash-probes/` | Two designs that read the flash devices U4 and U17 through JTAG, and their host scripts. They only read. See sections 5 and 10 |
| `example-ddr3-test/` | An example that builds alone: MIG, a 1 GiB memory test and a UART report. It shows the clocks, the UART, MIG and the configuration flash pins |
| `tools/` | The script that makes `pins.csv` and `package-pins.csv`, and its two inputs (the pin table of the manual, the package data of Vivado). A new board finding goes into `tools/tables.py`; then make the tables again |
| `workbench/` | A design and a host script that measure the board: the level and frequency of each pin, I2C, MDIO, Ethernet, SD, audio, buttons, displays. See section 14 |

Not in this repository:

- The manual (`AndeShape_ADP-XC7K160_410_UM098_V1.9.pdf`). It is a document of Andes.
- The backups of the two flash devices (U4 and U17). The contents of U4 are software of Andes.
  `flash-probes/` has the designs that made the backups.
- An earlier pin CSV and master XDC of this board. Their header says FFG676, and they give the DDR3
  as "2x K4B4G1646B". Both are wrong. `tools/manual-pins.csv` came from that CSV; the files here
  are corrected.

The scripts do not write into the repository. They write their products to the current directory,
or to the directory that you name. The start of each script gives its usage.

### Quick start

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

---

## 1. FPGA

| Item | Value | Source |
|---|---|---|
| Part | XC7K160T, package FBG676 | **[measured]** marking on the chip |
| Marking | `XC7K160T FBG676ABX1525 DF5089150A 1C`: speed grade **-1**, commercial | **[measured]** photograph |
| Vivado part | `xc7k160tfbg676-1` (section 2 says when `-2` is used) | |
| JTAG | One device in the chain, IR length 6, IDCODE `0x0364C093` (XC7K160T, revision 0) | **[measured]** |
| Encryption | FUSE_CNTL `0x00C8`. Vivado reports `IS_AES_PROGRAMMED=1`, but a bitstream that is not encrypted loads | **[measured]** |
| Supplies | VCCINT 1.047 V, VCCBRAM 1.049 V, VCCAUX 1.859 V (XADC) | **[measured]** |
| Temperature | 46 to 54 °C with the heat sink and a design in operation (XADC). 45 °C without the heat sink, with the factory design | **[measured]** |

JTAG does not give the speed grade: the IDCODE has only the part and the revision. Only the marking
shows it.

Resources (the "Available" column of the Vivado utilization report):

| Resource | Quantity |
|---|---|
| LUT | 101,400 (35,000 of them can be distributed RAM) |
| Flip-flop | 202,800 |
| Block RAM | 325 RAMB36 (650 RAMB18) |
| DSP48E1 | 600 |
| MMCM, PLL | 8 each |
| BUFG | 32 |
| GTX | 8 channels (2 quads), 1 PCIE_2_1 hard block |
| User I/O | 400 (banks 12 to 16 are HR, 250 pins; banks 32 to 34 are HP, 150 pins) |
| XADC, DNA_PORT, STARTUPE2 | 1 each; 4 BSCANE2; 2 ICAPE2 |

Tool: Vivado 2025.2.1. Its free license includes this part.

## 2. Speed grade and the real speed

The chip is marked -1. The DDR3-1600 designs here are built as **-2**. The reasons and the evidence:

- For -1 in the FB package, MIG permits DDR3-800 at most. DDR3-1600 needs the -2 settings (section 8).
- A small CPU core was synthesized alone (out of context). As -1 it closed timing at 330 MHz and not
  at 360 MHz. As -2 it closed timing at 400 MHz and not at 450 MHz.
- **[measured]** A design built as -2, with a 400 MHz core clock and DDR3-1600, passed all its
  tests on the board. The tests compared its output with a software model.
- **[measured]** The same core built as -3 passed a longer check at 400, 450, 500 and 525 MHz on
  this chip. At 550 MHz the output had errors. At 600 MHz almost all the output was wrong. The die
  was at about 50 °C and VCCINT was 1.046 V. The -3 timing model did not close above 450 MHz, so
  this result shows that the chip is faster than the model.
- The minimum period of a block RAM is 2.495 ns for -1 (400 MHz) and 2.095 ns for -2 (477 MHz).

This is evidence for one chip at room temperature. It is not a guarantee, and no margin was
measured. Above the marked grade, a failure shows as wrong data, or as a DDR3 calibration that does
not complete. It does not show as a clean stop.

Power (Vivado `report_power`, a vectorless estimate and not a measurement): a design with MIG and a
400 MHz core uses about 2.1 to 2.5 W. The DDR3 side (I/O, PHASER, MMCM, PLL) is about 1.6 W of that.

## 3. Banks and power

| Bank | Type | VCCO | What is on it |
|---|---|---|---|
| 0 | Configuration | 3.3 V | JTAG, DONE, INIT_B, PROGRAM_B |
| 12 | HR | 3.3 V | Two seven-segment displays, 10/100 Ethernet MII, AICE, touch SPI and trace, `PORn`, `SYS_RSTn`, `SD_PWR_ON` |
| 13 | HR | 3.3 V | LCD, SD card, AC97, I2C EEPROM, UART S1, `FA25_PU` |
| 14 | HR | 3.3 V | NOR flash FDQ[15:0], FA[15:0], FCS_B; `OSCCLK1`, `OSCCLK2`; SW1 to SW4; UART S2; USB 1.1 transceiver |
| 15 | HR | 3.3 V | NOR flash FA[24:16], FOE_B, FWE_B, F_BUSY_B, RS0 and RS1; USB OTG PHY (UTMI+); `RTC_32K` |
| 16 | HR | SW11 selects 1.8, 2.5 or 3.3 V. Now 3.3 V | IDE/CF connector, SW5 to SW8, `PCIE_PERST_N`, test points |
| 32 | HP | 1.5 V | DDR3 data (DQ, DQS, DM), `DDR3_RST_B` |
| 33 | HP | 1.5 V | DDR3 address and control, CK; 200 MHz `SYSCLK` |
| 34 | HP | 1.8 V | Gigabit Ethernet PHY (GMII, optional), SPI flash U17, test points |
| 115, 116 | GTX | | PCIe x8 edge connector, two reference clocks |

The VCCO values are from the block diagram of the manual (Figure 3). Operation on the board
confirmed three of them: 3.3 V, 1.5 V (DDR3) and 1.8 V (U17).

SW11 sets the voltage of bank 16 **[manual, section 2.7.1]**. SW11 is an SMD DIP switch with four
positions (`SW_4P_SMD`). The manual puts it "to the right of the seven-segment displays" (Table 2,
Figure 9). Only two positions have a function:

| 3–6 | 4–5 | VCCO_16 |
|---|---|---|
| on | off | 1.8 V |
| off | on | 2.5 V |
| off | off | 3.3 V |
| on | on | Not permitted |

**On this board all four positions of SW11 are off** (seen on 2026-11-20). From the table,
**VCCO_16 is 3.3 V**, and the pins of bank 16 use `LVCMOS33`. This comes from the switch position
and the table of the manual. The voltage itself was not measured. To measure it:

1. Load the pull-up bitstream of the workbench.
2. Put a voltmeter across one of SW5 to SW8 while the button is not pushed.

Only the weak pull-up of the FPGA is on that pin, so the meter shows VCCO_16.

If the switch position is not known, declare the inputs of bank 16 as `LVCMOS18`. The input
threshold then comes from the 1.8 V VCCAUX, and the input reads correctly at all three voltages. The
workbench reads SW5 to SW8 in this way.

Power: a 12 V, 5 A adapter goes into `POWER_JACK1` (or the ATX 4-pin `J19`). `POWER_SW1` is the
switch. The board makes 5 V, 3.3 V, 1.8 V, 1.5 V, 1.2 V, 1.0 V and one adjustable supply. LED12 to
LED17 show 1.0 V, 3.3 V, 1.8 V, 1.5 V, 1.2 V and VTT 0.75 V. LED7 shows DONE. **The board has no
general user LED.**

Pins that the manual does not list **[package]**: its table does not have 20 user I/O pins of bank
33 and one of bank 32 (AB20). VRN and VRP of bank 32 (V13, W13) are not in it either.
To list them, find the rows of `package-pins.csv` with an empty `board` column and a `pin_func` that
starts with `IO_`. Section 13 says what the workbench found on them.

## 4. Clocks

| Signal | Pin | Source | Frequency | Status |
|---|---|---|---|---|
| `OSCCLK1` | G24 (bank 14, SRCC) | X1 | 20 MHz, LVCMOS33 | **[measured]** The main clock of all the designs here |
| `OSCCLK2` | D23 (bank 14, SRCC) | X3 | 20 MHz | **[measured]** **No signal.** The pin follows the weak pull of the FPGA: X3 is not fitted |
| `RTC_32K` | H17 (bank 15, SRCC) | X2 | 32.768 kHz | **[measured]** Present: 32.767 kHz |
| `SYSCLK_P/N` | AA10, AB10 (bank 33, SRCC) | U22 | 200 MHz, differential | **[measured]** For MIG. It also works alone as LVDS: 199.978 MHz |
| `CKOUT1_P/N` | D6, D5 (MGTREFCLK0, bank 116) | U14 | 100 MHz, LVDS | **[measured]** Present: 99.997 MHz |
| `PCIE_CLK_P/N` | H6, H5 (MGTREFCLK0, bank 115) | PCIe slot | 100 MHz | **[measured]** No signal when the board is not in a slot |

The workbench measured the frequencies on 2026-11-20. It counts the rising edges in 20,000,000
periods of `OSCCLK1`, which it takes as one second. Thus each figure is relative to X1. The
resolution is 1 Hz. The three differential clocks are divided by 16 first, so their resolution is
16 Hz.

Important points:

- **An MMCM makes a high frequency directly from 20 MHz** **[measured]**. Use `MMCME2_BASE` with
  `CLKIN1_PERIOD=50.0` and `DIVCLK_DIVIDE=1`, and keep the VCO at or below 1200 MHz:
  `CLKOUT0_DIVIDE_F = 1200 / f` and `CLKFBOUT_MULT_F = f × CLKOUT0_DIVIDE_F / 20`. For example,
  400 MHz is ×60 and ÷3; 300 MHz is ×60 and ÷4. The multiplier has steps of 1/8, so
  `f × divider` must be a multiple of 2.5. Designs ran with 300 to 525 MHz made in this way.
- **In MIG, `SYSCLK` is `DIFF_SSTL15`.** The XDC of MIG sets it, as on the KC705. The manual says
  LVDS. Without MIG, `IOSTANDARD LVDS` with an `IBUFDS` and no `DIFF_TERM` works
  **[measured, workbench]**. The same 1.5 V bank 33 also had `LVCMOS15` inputs, and the DRC of
  Vivado accepted it. If a design has MIG, the XDC of MIG owns these two pins. Do not set them
  again in your XDC.
- **`CKOUT1` and `PCIE_CLK` are on GT reference clock pins.** Only an `IBUFDS_GTE2` can receive
  them. Its output can go directly to a `BUFG` as a general clock. The design does not need a GTX
  channel **[measured, workbench]**.
- Use the 20 MHz clock directly for slow logic (UART, flash, buttons, displays). Then that logic
  does not have to close timing with a fast core. A design with MIG then has three clock domains:
  20 MHz, the core clock from the MMCM, and the `ui_clk` of MIG.

## 5. Configuration and the configuration flash (U4)

- Configuration mode: **Master BPI x16**. The mode pins read `010` **[measured]**. At power-on the
  FPGA loads the factory bitstream of Andes from U4. A design that JTAG loads stays until power-off.
- The XDC must have these two lines **[measured, all the designs have them]**:

  ```tcl
  set_property CFGBVS VCCO [current_design]
  set_property CONFIG_VOLTAGE 3.3 [current_design]
  ```

  For a slow JTAG cable, add `set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]`. A
  design that uses one seventh of the device then has a 2.5 MB bitstream. The full size is 6.7 MB.
- Related pins **[package]**: `DONE` J7 (LED7 shows DONE), `INIT_B` G7, `PROGRAM_B` P6 (pull-up on
  the board).

**U4** is a 128 MB parallel NOR flash, x16. The Vivado configuration memory part is
`28f00am29ew-bbp-x16` **[manual]**. Pins: `FCS_B` C23, `FOE_B` M17, `FWE_B` L18, `FA[0..24]`,
`FDQ[0..15]` (see `pins.csv`).

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
- `RS0` (K18) and `RS1` (L17) connect through the optional SW12 to the same flash pins as FA23 and
  FA24 (U4.1, U4.56) **[manual]**. They were not used.
- Byte order of a flash file: `99 55 66 AA` in the flash image is the sync word `AA 99 55 66` of a
  `.bit` file. The bits in each byte are reversed, and then each pair of bytes is swapped.

The contents of U4 (factory state; read completely on 2026-11-20, two reads agreed):

| Location | Contents |
|---|---|
| `0x0000000–0x0680000` | FPGA bitstream (the factory design of Andes, with a core older than their RISC-V cores) |
| `0x4000000–0x40A0000` | Andes boot monitor |
| `0x4100000–0x4120000` | `U-Boot 2010.12-rc1 (Jul 22 2011 - 16:10:26)` |
| `0x4400000–0x4D80000` | A compressed image with its decompressor |
| `0x5600000`, `0x5F00000` | One 128 KiB block each, with no text |
| The remainder | Blank (`FF`) |

The DVD that came with the board (AndeSight and the BSP) does not contain these images. The backup
of the owner is the only copy.

To read U4 through JTAG, use `flash-probes/`:

```sh
sh flash-probes/run.sh u4 probe            # 16 words at each of five addresses
sh flash-probes/run.sh u4 out.bin          # all 128 MiB, about 11 minutes
```

The probe design (`flash-probes/u4/bpijtag.v`) cannot write the flash. It holds `FWE_B` high, and
its data pins are inputs only. It drives all 26 address bits, with FA25, so it reads both halves.
One read has no check. Read two times with two different scan lengths, and compare the files.

**Rule: nothing has written U4, and nothing in this repository writes it.** A write changes what the
board does at power-on, and only that backup can restore it.

## 6. JTAG

Connectors **[manual; J3 is measured]**:

| Signal | FPGA pin | J3 (6-pin) | J4 (14-pin, Xilinx type) |
|---|---|---|---|
| `TMS` | N8 | 1 | 4 |
| `TDI` | R6 | 2 | 10 |
| `TDO` | R7 | 3 | 8 |
| `TCK` | L8 | 4 | 6 |
| GND | | 5 | All odd pins |
| 3.3 V | | 6 | 2 |

Pins 12 and 14 of J4 are not connected. Bank 0 is 3.3 V. The manual gives this warning: **switch
off the board before you connect or disconnect the JTAG cable.**

The cable here is a Raspberry Pi Pico that runs [xvc-pico](https://github.com/kholia/xvc-pico)
(Xilinx Virtual Cable) on J3:

- The Pico firmware is xvc-pico at commit `eeac7d0` (slow slew, 2 mA). A firmware update needs the
  BOOTSEL button.
- The host daemon is `xvcd-pico` from the same project. It listens on `0.0.0.0:2542`, so run it
  only while you need it. `load.sh` starts it and stops it.
- To load: `VIVADO=... XVCD=/path/to/xvcd-pico sh jtag/load.sh design.bit`. A compressed bitstream
  of 2.5 MB takes about 12 seconds to program. The full sequence takes about 20 seconds
  **[measured]**.
- Bulk data is slow: about 1.4 Mbit/s (11 minutes to read 128 MiB). A Xilinx cable is much faster.
  But `xvc_scan.py` and the `dump.py` scripts of `flash-probes/` speak XVC directly. A different
  cable needs a different path for them.

Problems that were found **[measured]**:

- A new `hw_server` has not yet scanned the JTAG chain when the first `open_hw_target -xvc_url`
  comes. That call fails (`[Labtools 27-2269] No devices detected`). The cure is to wait 1 second,
  call `close_hw_target`, and call `open_hw_target [get_hw_targets]`. `load.tcl` does this.
  Sometimes it fails some times in sequence. Run the load again; the board is not the cause.
- Vivado leaves `hw_server` and `cs_server` in operation when it stops. Stop them by name
  (`pkill -x`). `load.sh` does this.
- If you write raw scan patterns, end them with a 1. One time, when the cable was touched, a long
  scan went into the IR. With a 1 at the end, the instruction that loads by mistake is BYPASS.
- To move data through JTAG in a design, use `BSCANE2` (`JTAG_CHAIN=1`, USER1, IR `0x02`). The
  two designs of `flash-probes/` are examples that work.

## 7. UART (RS-232, COM1)

The DB9 connector (COM1) goes through the RS-232 transceiver U15 to the FPGA. U15 has two ports.
**The DB9 has S1** **[measured]**:

| Signal | Pin (bank 13) | Direction (at the FPGA) | Description |
|---|---|---|---|
| `S1_TXD` | T17 | Output | From the board to the host |
| `S1_RXD` | R18 | Input | From the host to the board |
| `S1_CTS` | P18 | Input | **The RTS of the host.** It reads low while the host asserts RTS |
| `S1_RTS` | U16 | Output | A low asserts the CTS of the host |
| `S2_TXD`, `S2_RXD` | D25, G25 (bank 14) | | Probed: no signal of S2 goes to the DB9 |

All are LVCMOS33. Measurements **[measured, 2026-11-18]**:

- Through U15, both directions had no errors from 115200 to 625000 baud. Errors started at 666667
  baud. From 750000 baud the host could not read the board. The board could read the host up to
  1.5 Mbaud. Thus the limit to use is **500000 baud**.
- The DTR of the host has no effect. The host reads nothing on DSR, DCD and RI.
- The host asserts RTS when it opens the port, and P18 goes low. A design can use this as the
  sign that a host is connected. For RTS to go off when the port closes, the host must set `HUPCL`.
  (minicom clears it; `stty -F <port> hupcl` sets it again.) Do not use `CRTSCTS` together with
  this: the kernel then moves RTS for flow control.
- When the host opens its port, the board receives about ten bytes of noise.
- The console of the factory design of Andes is 38400 8N1.

The host port used here is an RS-232 port of a PC motherboard (a 16550A, 115200 baud at most). The
FIFO of a 16550A has only 16 bytes. When the host is busy, continuous output loses bytes: one time 7
bytes in about 680 kB. Reliable transfer needs flow control or framing. To make 115200 baud from
20 MHz, the divider is 174, which gives 114,943 baud (−0.2%).

A CH347 USB adapter (`1a86:55da`) was used before, and is not used now. It had these problems:

- Data that it receives while the port is closed comes out after the port opens.
- It cannot send a Break.
- It gives received data to the host only after 512 bytes, or after 0.5 ms without data.

## 8. DDR3 and MIG

### Hardware

- Two **PieceMakers PMF512816BBR-MBDN** devices **[measured, the marking]**. Each is 4 Gbit
  (256M × 16). Together they are one 32-bit memory of **1 GiB**. No datasheet for the B die was
  found. The datasheet for the F die of the same family gives 1.5 V, 96 balls, A0 to A14 and BA0 to
  BA2. It says that `-MB` is DDR3-1866 13-13-13. The manual says DDR3-800; the devices are much
  faster than that.
- Bank 32 (HP, 1.5 V) has the four data bytes. Byte *n* is in the T*n* group, and its DQS is on
  the DQS pins of that group. `DDR3_RST_B` is on AC16. Bank 33 has the address and control in T0 to
  T2, CK on W10 and W9, and the 200 MHz clock on AA10 and AB10. **MIG accepts this pin assignment
  as it is** **[measured]**.
- The VREF pins of bank 32 (AE16, Y18) connect to VTT 0.75 V: the board uses external VREF.
- Only bank 33 has DCI reference resistors (U9 VRN, V12 VRP). Bank 32 has none. Thus the design
  needs this line, and **MIG does not write it**:

  ```tcl
  set_property DCI_CASCADE {32} [get_iobanks 33]
  ```

  With this line, DCI matches on the board **[measured]**.

### MIG parameters

MIG 7 series 4.2, native interface. `mig/mig_prj.py` writes the full `mig.prj`. The main values:

| Parameter | Value |
|---|---|
| Memory part | `MT41J256m16XX-107` (1300 and above), `-125` (800, 1066). A Micron part with the same specification replaces the PieceMakers part. The `m` in the name is lower case: the part table of MIG spells it so |
| Data width | 32 bits, 4 DQS, with data mask, no ECC |
| Address | Row 15, column 10, bank 3; `BANK_ROW_COLUMN` |
| PHY ratio | 4:1 |
| System clock | 200 MHz, differential, pins AA10 and AB10 (`DIFF_SSTL15`) |
| Reference clock | Use System Clock |
| Reset polarity | `sys_rst` is **active low** |
| I/O | DQ `SSTL15_T_DCI`, DQS `DIFF_SSTL15_T_DCI`, the others `SSTL15`, CK `DIFF_SSTL15`, reset `LVCMOS15`; DCI cascade on, external VREF |
| Memory voltage | 1.5 V |
| Timing | tRFC 260 ns, tREFI 7.8 µs, tRAS 37.5, tRCD 15, tRP 15, tFAW 50, tRRD 10, tWTR 7.5, tRTP 7.5, tCKE 7.5 ns |
| Mode register | BL8, sequential; output drive RZQ/7; RTT_NOM RZQ/4 (60 Ω); RTT_WR off; AL 0 |
| XADC | Enabled. MIG instantiates the XADC for temperature compensation and gives the 12-bit reading on `device_temp` |
| Other | 4 bank machines, ordering Normal, debug off |

The settings for each rate:

| Rate | tCK | CL / CWL | Part in the MIG project | VccAuxIO | `ui_clk` |
|---|---|---|---|---|---|
| 800 | 2500 ps | 6 / 5 | `xc7k160t-fbg676/-2` | 1.8 V | 100 MHz |
| 1066 | 1875 ps | 7 / 6 | `xc7k160t-fbg676/-2` | 1.8 V | 133 MHz |
| 1300 | 1538 ps | 9 / 7 | `xc7k160t-ffg676/-2` | 1.8 V | 163 MHz |
| **1600** | 1250 ps | 11 / 8 | `xc7k160t-ffg676/-2` | 2.0 V | 200 MHz |
| 1800 | 1111 ps | 13 / 9 | `xc7k160t-ffg676/-2` | 2.0 V | 225 MHz |

The reasons for these settings:

- **The limit of MIG comes from the part, package and speed grade that you give it** (the
  `time_periods.csv` of MIG). For the FB package, the fastest period is 2500 ps for -1 and 1875 ps
  for -2. A faster rate needs a project for FFG676, which has the same pins. From 1600, it also
  needs the 2.0 V VccAuxIO that FBG does not have. After MIG generates its files, the design
  builds for the real `xc7k160tfbg676`. Thus the timing report is for the real part. What MIG does
  not guarantee is the eye at the pins.
- **With a 200 MHz oscillator, MIG accepts only the periods that it can make**: 2500, 1875, 1538,
  1250 and 1111 ps. It refuses 1500 and 1072 (`Invalid Input Clock Period`).
- **If tCK is less than 1500 ps (1600, 1800), MIG makes one more clock, the IDELAY reference.** It
  is 300 MHz for speed grade 1 and 400 MHz for grades 2 and 3 (`FPGA_SPEED_GRADE` in `mig_mig.v`).
  A 400 MHz IDELAY reference does not pass DRC on a -1 part (`AVAL-31`: -1 permits only 200 or
  300 MHz). Thus, with the -2 project that `mig_prj.py` writes, **the design must also build as
  `xc7k160tfbg676-2`**. To build 1600 as -1, change `GRADE` in `mig_prj.py` to `"-1"`. MIG permits
  FFG676 -1 with 2.0 V. The +0.013 ns of the table below came from such a build on 2026-11-18.
- The other fields, such as `<LowPower_En>ON` and `<XADC_En>Enabled`, are the same as in the prj
  of the KC705 board file.

### Results on the board

The memory test (`example-ddr3-test/`) fills 1 GiB and reads it back. Each word is a hash of its
address and a seed. Each pass has a new seed. Each odd pass inverts every second word. The die was
at 49 to 54 °C. At each rate, 20 new calibrations all passed:

| Rate | Worst slack, build grade | Calibration time | Passes without error |
|---|---|---|---|
| 800 | +0.94 ns, -1 | 108 to 110 ms | More than 250 |
| 1066 | +0.58 ns, -1 | 606 to 639 ms | 745 |
| 1300 | +0.30 ns, -1 | 325 to 341 ms | 310 |
| **1600** | +0.013 ns, -1 | 382 to 391 ms | 291 |
| 1800 | −0.24 ns as -1; +0.046 ns as -2 | 334 to 347 ms | 1786 |

One pass of 1 GiB (the write and then the read) takes about 1.4 seconds at DDR3-800 and about 0.7
seconds at 1600.

**Use 1600.** The memory test passed at 1800. But in a larger design at 1800, calibration did not
complete after 11 of 101 JTAG loads. MIG stayed not ready until the next load. The cause was not
found. At 1600, 36 loads had no failure, and none came in long use after that.

### Notes for the use of MIG

- **`app_addr` has 29 bits, but only the low 28 bits address memory.** The unit is a 32-bit word
  (2^28 × 4 = 1 GiB). Bit 28 is the rank. There is one rank, so an address with bit 28 set is an
  alias.
- At 4:1 and 32 bits, `app_wdf_data` and `app_rd_data` have **256 bits**. One beat is one BL8 burst
  (8 words), and the address increases by 8 each time. One transfer is one command and one data
  beat. Set `app_wdf_end` together with `app_wdf_wren`.
- `sys_rst` is active low. The designs here hold it low for 52 ms after configuration (2^20 cycles
  of 20 MHz) and then release it.
- MIG makes `ui_clk`. It has no relation to a clock made from the 20 MHz source, so a crossing
  needs synchronizers. One method is a toggle handshake, with the data held constant beside it. The
  constraint is `set_max_delay -datapath_only` or `set_clock_groups -asynchronous`. See the XDC of
  the example.
- MIG uses the only XADC. For the temperature, use its `device_temp`. A design that needs the
  XADC must set `XADC_En` of MIG to off and give MIG the temperature.
- Latency: at DDR3-1600, the first word comes back about **178 ns** after the read request
  **[measured]**. This includes two synchronizers, the command path of MIG, CL 11 and the return
  path. A design that needs fast memory must put a cache before MIG.
- Resources: MIG with the tester uses 8,009 LUT and 6,860 FF (DDR3-1600). MIG uses 1 PLL, 2 MMCM,
  2 PHASER_REF, 1 IDELAYCTRL and the XADC. One of the two MMCM is the IDELAY reference clock, which
  exists only from 1600.

### Generate MIG (non-project)

```sh
VIVADO=/path/to/vivado sh mig/generate.sh 1600 /some/writable/directory/mig-1600
```

After some minutes the script prints the path of `user_design`. The design reads these files:

```tcl
foreach dir {clocking controller ecc ip_top phy ui} { read_verilog [glob $mig/rtl/$dir/*.v] }
read_verilog $mig/rtl/mig.v $mig/rtl/mig_mig.v      ;# do not read mig_mig_sim.v
read_xdc $mig/constraints/mig.xdc                   ;# read it first, then your own XDC
```

The DDR3 ports of the top level must keep the names of MIG (`ddr3_dq`, `ddr3_dqs_p`, `sys_clk_p`
and so on), because `mig.xdc` constrains those names. `mig_prj.py` sets `sys_rst` and
`init_calib_complete` to have no pin.

## 9. Buttons, seven-segment displays, resets

**Buttons** SW1 to SW8 (GPIO1 to GPIO8 in the manual):

| Buttons | Pins | Bank | Status |
|---|---|---|---|
| SW1 to SW4 | E21, F24, J21, L23 | 14 (3.3 V) | **[measured]** |
| SW5 to SW8 | A13, A12, J14, D9 | 16 (SW11) | **[measured]** The same as SW1 to SW4: with a pull-up, a pushed button reads low |

**None of the eight has a pull-up on the board.** The XDC must set `PULLUP TRUE`. A pushed button
then reads low **[measured]**. For SW5 to SW8 this was found on 2026-11-20: the workbench declared
the inputs as `LVCMOS18`, and each button was pushed in turn. The design must debounce the buttons.
One method that works is to sample each millisecond and to accept a level after four equal samples.

**Seven-segment displays** 7SEG1 and 7SEG2 (bank 12, LVCMOS33) are common anode: **a low output
lights the segment** **[measured]**. **7SEG2 is the left digit (the high digit)** **[measured]**. On
2026-11-20 the workbench showed `L` on 7SEG2 and `r` on 7SEG1, and the board showed `Lr`.

| Segment | 7SEG1 | 7SEG2 |
|---|---|---|
| A | V26 | W24 |
| B | W25 | AB26 |
| C | W26 | AC26 |
| D | V21 | Y25 |
| E | W21 | Y26 |
| F | AA25 | AD21 |
| G | AB25 | AD23 |
| P (decimal point) | W23 | AB24 |

Each segment has its own pin, so the design does not scan. All sixteen segments are confirmed: the
`digits` command of the workbench lit each one in turn (2026-11-20), and each agreed with the table.

**Reset and power buttons** **[measured, 2026-11-20, workbench]**:

| Button | Signal | Pin | Behaviour |
|---|---|---|---|
| `HW_RST_SW1` | `SYS_RSTn` (through U19) | U22 | High at rest, low while pushed. It goes only to the FPGA. It does not reset the Ethernet PHY |
| `PWR_SW1` | `GPIO0_ENn` | K21 | High at rest, low while pushed |
| (no button) | `PORn` (from U18) | U21 | Low while the FPGA is not configured. It goes high about **385 ms** after the design starts. Neither button moves it |

The board drives all three. The weak pull-down of the FPGA cannot move them, so they need no pull.

**`PORn` is not only a power-on reset: it comes again after each configuration of the FPGA.** The
workbench records the first level of each pin after configuration and the time of its first change
(`workbench.py boot`, in periods of the 20 MHz clock). After a JTAG load, `PORn` starts low and goes
high after about 385 ms. Five loads gave 387.14 and 387.05 ms (with the short circuit of section
11), then 384.30, 384.26 and 384.37 ms (after the repair). The clocks of the Ethernet PHY,
`MAC_RX_CLK` and `MAC_TX_CLK`, first appear 0.5 µs after `PORn` goes high.

Thus `PORn` is the reset of the peripherals on the board. A design can use it as the sign that the
peripherals are out of reset. Do not use the PHY before that.

This is not an effect of the short circuit in the SD socket (section 11). After the repair, a new
load gave the same `PORn` and the same PHY clocks. The PHY took address 31 with the pull-up
bitstream and address 0 with the pull-down bitstream.

**`HW_RST_SW1` does not reset the PHY.** The test (`probes/probe_reset.py`) wrote `0081` to register
4 of the PHY. Then the button held `SYS_RSTn` low for 0.68 seconds. After that the register was
still `0081`, and the address of the PHY was the same.

The pin table of the manual gives the cause. `DONE` of the FPGA (J7) connects to U18.3, and `PORn`
is U18.2. U18 and U19 are two reset supervisors. The marking on both is `692B2`. The Selector Guide
of the GMT G692/G693 datasheet gives `692Bx` as the **G692L293T**:

- Package SOT-143. Pin 1 is GND, pin 2 is RESET, pin 3 is MR and pin 4 is VCC.
- The threshold is 2.93 V. The reset output is push-pull and active low.
- MR is the manual reset input. It is active low and has an internal 20 kΩ pull-up.
- The output is low while MR is low or VCC is below the threshold. After that it stays low for a
  timeout: 140, 310 and 520 ms (minimum, typical, maximum).

These pins agree with the table of the manual: the MR of U18 is `DONE`, and the MR of U19 is
`HW_RST_SW1`. The measured 385 ms is in the range of the timeout. Thus the time is different for
each device and each temperature. A design must look at `PORn` itself and must not assume a time.

Both devices also monitor the 3.3 V supply. **If 3.3 V goes below 2.93 V, both outputs go low.**
That occurred when the SD socket had its short circuit and its power was switched on (section 11).

**`HW_RST_SW1` is only an input for the design.** It does not start a new configuration of the FPGA:
the workbench continued to answer after the button was pushed. The design decides if it is a reset.
The factory bitstream probably uses it to reset the soft core of Andes. `PWR_SW1` is only an input
too. A design needs neither: it can make its power-on reset from the `LOCKED` output of its MMCM.

## 10. User SPI flash (U17) and EEPROM (U16)

**U17** is a Macronix MX25U1635E: 16 Mbit (2 MiB), **1.8 V**, on bank 34 **[measured]**.

| Signal | Pin | Role in plain SPI |
|---|---|---|
| `SPI_CSN` | Y5 | CS# |
| `SPI_SCLK` | AA5 | CLK |
| `SPI_SI/SIO0` | AC6 | MOSI |
| `SPI_SO_SIO1` | AC4 | MISO |
| `SPI_WP#_SIO2` | AB5 | WP# (drive it high) |
| `SPI_SIO3` | AB6 | HOLD# (drive it high) |

All are `LVCMOS18`. The JEDEC ID is `C2 25 35`, the status register is `00`, and the device
answers SFDP. Read, erase and program all worked at 2.5 MHz **[measured]**. U17 is not the
configuration flash of the FPGA, so a design can use it freely. It was blank (`FF`) when it was
first read on 2026-11-20.

To read U17 through JTAG, use `flash-probes/`:

```sh
sh flash-probes/run.sh u17                 # the ID, the status register and the SFDP header
sh flash-probes/run.sh u17 out.bin         # all 2 MiB, read two times with two read commands
```

The probe design (`flash-probes/u17/flashjtag.v`) puts U17 on the USER1 register of JTAG. It passes
each command to the flash. The host script sends only read commands: `9F`, `05`, `5A`, `03` and
`0B`.

**U16** is an AT24C128 I2C EEPROM (16 KB) on `I2C_SCL` M25 and `I2C_SDA` L25 (bank 13). The board
has pull-ups on both lines. Only address `0x50` answers on the bus, and the first 32 bytes are all
`FF` **[measured, workbench; read only, never written]**. The memory address has two bytes.

## 11. Other peripherals

The pins are in `pins.csv` and `adp_xc7k160_master.xdc`. Find them by the start of the signal name:

| Peripheral | Part or connector | Signal prefix | Bank |
|---|---|---|---|
| 10/100 Ethernet (MII) | U9 DM9161C, `RJ45_CON2`, 25 MHz crystal | `MAC_`, `PHY_PWRDWN` | 12 |
| Gigabit Ethernet (GMII), **optional** | U12 88E1119R, `RJ45_CON1` | `PHY_` | 34 (1.8 V) |
| SD card | `SD_CON1`; `SD_PWR_ON` controls the power switch U31 | `SD_` | 13, 12 |
| Audio | U8 ALC203 (AC97), 24.576 MHz crystal | `AC97_` | 13 |
| LCD module and touch panel | `LCD_CON1`, `LCD_CON2` (24-bit RGB and touch SPI) | `LCD_`, `TP_` | 13, 12 |
| USB OTG (UTMI+), **optional** | U5 USB3500, U7 power switch | `USB_`, UTMI signal names | 15 |
| USB 1.1 device, **optional** | U6 | `U_` | 14 |
| IDE/CF | `IDE_CON1` (44-pin) | `CFC_`, `IDE_` | 16 |
| AICE debug | `AICE_CON1` (20-pin), `AICE_CON3` (10-pin) | `ICE_` | 12 |
| Trace | `ACON2` (20-pin) | `TRC_` | 12 |
| PCIe x8 | Edge connector | `PCIE_`, `CKOUT1_` | 115, 116, 16 |

The subsections below give what the workbench measured on this board **[measured, 2026-11-20]**.
Section 14 says what "follows the pull" means.

### 10/100 Ethernet

**The 10/100 Ethernet works.**

- The PHY ID on MDIO is `0181 B8B1` (Davicom DM9161 family).
- On a LAN, autonegotiation gives 100BASE-TX full duplex. `MAC_RX_CLK` and `MAC_TX_CLK` are both
  25.0016 MHz.
- A frame from the LAN has a correct preamble, SFD, MAC address and FCS.
- With the loopback of the PHY (BMCR bit 14), 200 bytes that the workbench sent came back the same.
  After the loopback bit is set, the PHY needs about one second before it receives its own frames.

**The board sent and received on a real LAN** (`workbench.py net`; the result is
`workbench/results/net-2026-11-20.txt`). The only other station was the host that ran the test. The
board was a host with a locally administered MAC address. Its IP address was one for which no
station answered an ARP probe.

- ARP in both directions: the board asked for the host and got an answer, and the host asked for
  the board and got an answer.
- Ping from the host to the board: 10 packets of 56 bytes and 3 packets of 1472 bytes all came
  back. A packet of 1472 bytes makes a frame of 1514 bytes, the maximum.
- UDP to the board and back: 20 datagrams of 1 to 1472 bytes came back with the same contents.
- The board sent 20000 frames of 1518 bytes with only the standard gap between frames
  (99 Mbit/s), and then 20000 frames of 64 bytes. The host received all of them with correct
  contents. The network adapter and the kernel of the host checked the FCS and the IP and UDP
  checksums.
- The host sent 10000 datagrams of 1472 bytes and 20000 datagrams of 18 bytes. The sender kept the
  rate below 60 Mbit/s, so that the switch did not drop frames. The board counted every one as a
  frame with a correct FCS, and counted 0 frames with a bad FCS.

The workbench handles the packets through the UART. That is the cause of the ping time (from 33 ms)
and of the limit of one received frame at a time. The board is not the cause. The workbench samples
the two clocks of the PHY at 400 MHz and does not use them as clocks. A normal design can use them
as clocks: `MAC_RX_CLK` (AA23) is on an SRCC pin and `MAC_TX_CLK` (Y23) is on an MRCC pin. Only
100BASE-TX full duplex was tested.

**The address and the mode of this PHY depend on the pulls that the FPGA design puts on the MII
pins.** When the DM9161 comes out of reset, it reads its own output pins as straps:

| Pins | Strap |
|---|---|
| `RXD0` to `RXD3`, `CRS` | PHY address |
| `COL` | RMII |
| `TX_CLK` | Isolate |
| `RX_ER` | Repeater |
| `RX_DV` | Test mode |

Its reset is `PORn` (section 9). After each configuration the design is in operation, and the PHY
stays in reset for about 385 ms more. Thus the PHY reads the pulls of the design. It does not read
the pull-ups that the pins have before configuration.

- With a pull-down or no pull on these pins: address 0, MII, BMCR `3100`. All is correct.
- With `PULLUP` on these pins: address 31, BMCR `1400` (isolate), register 16 is `0D14` (RMII,
  repeater). There is no `RX_CLK` or `TX_CLK`, and no link. A write of the correct bits through
  MDIO does not repair it, and a software reset does not repair it.

Thus **do not put `PULLUP` on the pins of this PHY**. Vivado puts a pull-down on a pin that the
design does not use, which is correct here.

`MAC_PHY_LINKSTS` (AF22) follows the pull: the PHY does not drive it. The PHY operates normally
while `PHY_PWRDWN` (Y20) is low.

### Parts that are not fitted

- **The Gigabit Ethernet PHY (U12) is not fitted.** `PHY_MDIO` has a pull-up, but none of the 32
  addresses answers. All other `PHY_` pins follow the pull.
- **The USB parts (U5, U6) are not fitted.** That area of the board is empty (seen). The
  measurement agrees: `USB_CLKOUT` has no clock, and the UTMI pins and the 16 `U_` pins all follow
  the pull.

### Audio

**The audio codec (U8) works.** It is a Realtek ALC203 (vendor ID `414C 4770`).

- With `AC97_RSTn` high, `AC97_BCLK` is 12.288 MHz. With `AC97_RSTn` low, it stops.
- The workbench runs the AC-link itself (frames at 48 kHz). The status is `000F`: the ADC, the DAC,
  the mixer and the reference are ready. The registers can be read and written. The default sample
  rate is 48000. VRA is available (extended ID `0A07`).
- Loop test: a square wave of ±4096 goes to the DAC. The ADC records the sum of the mixer (record
  select `0505`, all outputs muted). The ADC gives a swing of 4532 on the left and 4623 on
  the right. With no sound it gives 4. With sound on one side only, the other side stays at 4 to 5.
  Thus left and right are not exchanged, and there is no crosstalk.
- `AC97_SYNC` and `AC97_SDO` must be low at the moment `AC97_RSTn` goes from low to high. If not,
  the codec goes into a test mode.
- `workbench.py tone` sends 1 kHz to the left output and 500 Hz to the right output. The sound was
  heard (2026-11-20).

### SD card

**The SD card socket works. Blocks were read and written on both interfaces.** The clock was
238 kHz, and the card was one 32 GB SanDisk SDHC card (OCR `C0FF8000`). The command is
`workbench.py sdrw`; the result is `workbench/results/sdrw-2026-11-20.txt`.

- SPI mode (`SD_DAT3` is CS, `SD_CMD` is MOSI, `SD_DAT0` is MISO). CMD59 made the card check each
  CRC. The CID and the CSD had a correct CRC (31.9 GB). The test used 8 blocks, from block 1 to the
  last block of the card. For each block it read the block, wrote a pattern, read the pattern back,
  wrote the original contents and read them back. All were correct. The card refused a block with a
  wrong CRC (data response `0B`) and kept the contents.
- The 4-bit bus of the card (CMD0, CMD8, ACMD41, CMD2, CMD3, CMD7, ACMD6). The CID was the same as
  in SPI mode. A block read on the four data lines was the same as in SPI mode, and the CRC16 of
  each line was correct. Write, read back and restore were all correct. With a wrong CRC on
  `SD_DAT2`, the card answered CRC status `101` and kept the contents. Thus **`SD_CMD` and
  `SD_DAT0` to `SD_DAT3` work in both directions**.
- The speed was only 238 kHz, because the workbench makes each clock from a UART command. 25 MHz
  and 50 MHz were not tested.

Firmware must know two behaviours. Both are behaviours of this card; a different card can be
different.

1. After CMD17, **the card starts the data before the end of its response**. The start bit comes 18
   clocks after the start of the response. Receive the response and the data lines together.
2. After the card refuses one write on the 4-bit bus, **each subsequent write is one write late**.
   The card stores the data of the write before, but answers CRC status `010`. CMD12 does not
   help. Only a power cycle of the card (`SD_PWR_ON`) cures it.

The other pins of the socket:

- `SD_CDn` (P16) is low with a card and high without one. `SD_WPn` (N17) changes together with it.
  It is low for a card that is not write-protected.
- The board has pull-ups on `SD_CMD` and `SD_DAT0` to `SD_DAT3`. `SD_CLK` has none.
- `SD_PWR_ON` (V22) is the enable of the socket power switch U31, and 1 is on. A pull-up on the
  board makes on the default. Thus the power is also on when the FPGA is not configured, or when
  the design does not use this pin. LED11 (below the socket) shows `SD_3V3`. In the normal
  condition, a change of this pin or a hot plug of the card has no effect on other parts.

**The socket of this board had a fault (2026-11-20).** The VDD contact in the socket was bent and
touched the metal cover of the socket. This made a short circuit from `SD_3V3` to ground. The
symptoms:

- The card did not answer (CMD0 read `FF`).
- LED11 was dark, and U31 was hot.
- When `SD_PWR_ON` went off for 0.2 seconds or more and then on, the 3.3 V supply went down. This
  made `PORn` and `SYS_RSTn` go low, which also reset the Ethernet PHY.

After the contact was pushed back, all was normal. If the card does not answer again, look at LED11
and touch U31 first. `workbench.py set SD_PWR_ON 0` switches the power off.

### Other findings

- The transceiver of the second UART (S2) is present: `S2_RXD`, an output of the transceiver, stays
  high. While `S1_RTS` is 1, the CTS of the host is not asserted. The transceiver inverts it, as it
  inverts TXD.
- Nothing drives `PCIE_PERST_N` (the board is not in a slot).

Notes:

- **The trace connector shares pins with other signals.** `TRC_CLK` and `ICE_DBGRQ` are both on
  AC24. `TRC_DATA0..3` share U24, U25, V23 and V24 with `TP_SPI_DO`, `TP_SPI_DI`, `TP_SPI_CLK` and
  `TP_IRQ`. Thus `pins.csv` has 445 rows for 440 pins.
- The `ICE2_*` signals of bank 16 are for `AICE_CON2`, which was removed. The manual marks them
  "Not be used".
- PCIe: lanes 0 to 3 are in bank 116 and lanes 4 to 7 in bank 115. Lane 0 is `MGTX*3_116` and lane
  7 is `MGTX*0_115`. This reversed order is usual for an add-in card. `PCIE_PERST_N` is on F9 (bank
  16, so its voltage depends on SW11). The board itself has the shape of a PCIe card.
- Test points: 18 I/O pins of bank 34 and 6 of bank 16 go to test points (the `pcb_symbol` column
  of `pins.csv`).

## 12. Vivado flow and problems that were found

The designs here use a non-project batch flow; `example-ddr3-test/build.tcl` is a short example.
Each item below cost time to find **[measured, Vivado 2025.2.1]**:

- **Vivado writes a bitstream when timing fails.** Always check `report_timing_summary`. `build.sh`
  does.
- **Vivado reads some SystemVerilog differently from a simulator, and gives only a warning.** For
  example, a member of a function result (`decode(word).is_move`) becomes a constant 0. To stop
  this class of error, make `Synth 8-3848` (a net without a driver) an error:
  `set_msg_config -id {Synth 8-3848} -new_severity ERROR`.
- Two write statements to one LUT RAM are inferred as "read, then write", and the RAM doubles.
- **If separate `always_ff` blocks of a generate loop assign the bits of one vector, the initial
  value of the declaration is lost.** Vivado then takes a bit that is only set, and never cleared,
  as a constant 1 (`Synth 8-3333 propagating constant 1`, only an INFO). A simulator starts it at
  the initial value 0. The cure is one `always_ff` with a `for` loop and an explicit clear.
- The slack says only that timing passed. It does not say how much margin the design has: the tool
  stops when timing passes. A change of one line of RTL can move the slack by 0.1 ns. After each
  RTL change, build again before you trust the clock. The same netlist gives the same result each
  time.
- At 400 MHz, the clock skew between clock regions can be 0.29 ns. That is more than the margin of
  a path. A pblock that keeps the most critical logic in one clock region (for example
  `CLOCKREGION_X0Y0`) makes the timing close reliably.
- `place_design -directive ExtraTimingOpt` rescued a design that failed by 0.034 ns.
- `opt_design -directive NoBramPowerOpt`: by default, Vivado adds power gates to the enables of the
  block RAMs. That is more logic on a critical path.
- Block RAM at 400 MHz: without the output register (`DO_REG`), the data comes about 1.8 ns after
  the clock. The address and enable routes are also long. One design gave two cycles to each side of
  the block RAM (multicycle constraints). Each port is enabled only in the cycle in which the RAM
  must sample. An enable that is high while the address comes late can corrupt the RAM (UG473).
- For small storage with low latency (tens to hundreds of entries), distributed RAM (LUT RAM) is
  faster than flip-flops with a large mux.
- This device has many LUTs. If a shared adder or mux makes a path long, make a copy of the logic
  first.

## 13. What is not known

Items that need a person at the board:

- None at this time.

Other items:

- The polarity of `PHY_PWRDWN`: it is known only that low is normal operation.
- Ethernet was tested only at 100BASE-TX full duplex. 10 Mbit/s and half duplex (`MAC_COL`,
  `MAC_CRS`) were not tested.
- The SD card was tested only at 238 kHz, and only with one card.
- The margin above the -1 marking (400 MHz, DDR3-1600): there is a result for one chip at room
  temperature only.
- The data lines of the LCD, IDE/CF, AICE, trace and PCIe were not used.

Items that the workbench confirmed on 2026-11-20:

- The polarity and behaviour of SW5 to SW8, `HW_RST_SW1` and `PWR_SW1`, and that 7SEG2 is the left
  digit (section 9).
- All four positions of SW11 are off, so bank 16 is 3.3 V (section 3).
- The short circuit at the VDD contact of the SD socket is repaired. Blocks were read and written
  in SPI mode and on the 4-bit bus (section 11).
- The 10/100 Ethernet sent and received on a real LAN: ARP, ping, UDP, and tens of thousands of
  frames in each direction without error (section 11).
- The reset of the Ethernet PHY is `PORn`. It releases about 385 ms after each configuration of the
  FPGA (sections 9 and 11).
- All four positions of SW12 are off (seen). The measurement agrees: `RS0` and `RS1` follow the
  pull, and they do not move when the workbench drives `FA23` and `FA24`.
- `OSCCLK2` is not fitted. `RTC_32K` has a signal. `SYSCLK` works alone as LVDS (section 4).
- The Gigabit Ethernet PHY and the USB parts are not fitted. The 10/100 Ethernet, the audio codec
  and the I2C EEPROM are present (sections 10 and 11).
- The 20 pins of bank 33 that the manual does not list, and AB20 of bank 32, all follow the pull.
  Nothing drives them, so take them as not connected.
- `VRN` and `VRP` of bank 32 (V13, W13) also follow the pull: they have no reference resistors.
  Thus `DCI_CASCADE` (section 8) is necessary and not optional. `VRN` of bank 33 is always high and
  `VRP` always low: the resistors are there.

## 14. Workbench: measure the board

`workbench/` is a small design without MIG, and a host script. It answers the question "what is on
this pin". The build is for -1 and takes about four minutes: 5,909 LUT, 19,960 FF and two RAMB18.

Each of 109 pins is one channel. The FPGA samples each channel at 400 MHz. It records the level,
whether the pin was low or high since the last read, and the number of rising edges in each second. The host reads
these through the UART (115200 baud). The host can also drive the small number of pins that the
board gives to the FPGA to drive.

```sh
sh workbench/build.sh                                   # the products go into ./adp-workbench
cp workbench/prebuilt/*.bit /some/writable/directory/   # or use these (-1, Vivado 2025.2.1); load.sh writes its logs beside the bitstream
sh jtag/load.sh /some/writable/directory/workbench-pullup.bit
export BOARD_PORT=/dev/ttyS0
python3 workbench/workbench.py snapshot up.json         # load the pull-down bitstream and do it again into down.json
python3 workbench/workbench.py pulls up.json down.json  # each pin: driven, or follows the pull
python3 workbench/workbench.py auto                     # I2C, MDIO, Ethernet, SD, audio, SW12, RTS
python3 workbench/workbench.py boot                     # run it directly after a load: first level and first change of each pin
python3 workbench/workbench.py tone 10                  # 1 kHz left and 500 Hz right to the codec outputs, to hear
python3 workbench/workbench.py buttons                  # interactive: push each button in turn
python3 workbench/workbench.py digits                   # interactive: each segment lights in turn
python3 workbench/workbench.py watch 60                 # each change of a pin in 60 seconds
python3 workbench/workbench.py set SD_PWR_ON 0          # drive one drivable pin and hold it (0, 1, or z to release)
python3 workbench/workbench.py net                      # SENDS FRAMES TO THE LAN: the board is a host and talks to this host
python3 workbench/workbench.py sdrw                     # WRITES TO THE SD CARD: read, write, read back and restore blocks
```

- **The two bitstreams have the same routing.** They differ only in the weak pull on the channels
  (`PULLUP` or `PULLDOWN`). If the value of a pin follows the pull, nothing drives the pin. If the
  value is the same in both, something drives the pin, or the board has a stronger pull than the
  FPGA. The workbench also measures pins with a known state, as a check of the
  method: SW1 to SW4, `FA25`, `VRN` and `VRP` of bank 33, and two empty test points.
- The Ethernet tests need the pull-down bitstream (the straps of section 11). The other tests work
  with both.
- Safety limits:
  - A pin with an unknown connection is only read and never driven.
  - The I2C EEPROM is only read.
  - The three control pins of U4 stay high. U17 is not touched.
  - The tests of `auto` do not write to the SD card: they initialize it and read the CID and the
    CSD. Their Ethernet transmission goes only through the loopback of the PHY, and not to the
    line.
- Only two commands change something outside the board. Each must be given explicitly:
  - `net [board IP [host IP [UDP port]]]` sends frames to the LAN. First it sends ARP broadcasts.
    An ARP probe (RFC 5227) finds an address that no station uses. An ARP request then asks for
    the address of the host. All other frames go only to the host that runs the test. The board
    also sends frames that are not answers. For these, the host needs a UDP port that the firewall
    permits and that no program uses (5201 by default). Ping and the UDP echo do not need it.
  - `sdrw [blocks [keep file]]` writes to the SD card, and then writes the original contents back.
    Before the first write, it stores the original contents of the blocks in a file
    (`./sdrw-kept.json` by default). It deletes the file only after all blocks are restored. If
    the test stops in the middle, the original contents are in that file. The test writes block 1.
    On a card with one partition, that is usually the boot sector of the file system.
- `workbench/sim/run.sh` simulates the command protocol with Verilator.
- `workbench/results/` has the measurements of 2026-11-20.
- `workbench/prebuilt/` has the two bitstreams of that date and their build reports (-1, 400 MHz
  sampling, setup slack +0.092 ns).
- The FPGA sends and receives the Ethernet frames:
  - `m` and `n` put a frame into the buffer and send it. The FPGA adds the preamble and the FCS,
    and can send the frame up to 65535 times in sequence.
  - `q` sets the MAC address of the board, and whether it receives only frames to that address.
  - `a`, `e` and `o` release the receiver, read the status and the counters, and read the one frame
    that the receiver holds.
  - The host script does the layers above (ARP, ICMP, UDP) through the UART. Thus it is slow, and
    the receiver holds only one frame: while it holds one, it only counts the frames that arrive.
- `workbench/probes/` has the one-time scripts that found the conclusions of section 11 (the straps
  of the PHY, the loopback, the SD card). The usage is
  `python3 workbench/probes/probe_strap.py [port]`. They are kept so that the work can be done
  again; they are not tools. `probe_loop.py` and `probe_loop2.py` use the commands of an earlier
  design (`adb00002`) and do not run on the present bitstream.
- The start of `rtl/adp_xc7k160_workbench.sv` gives the command protocol. `board.py` is the channel
  table, and also makes the XDC.

## License

The files of this repository are under the MIT License: see [LICENSE](LICENSE).

The repository does not contain the manual of Andes, the flash contents of the board or the output
of Xilinx tools other than the reports and bitstreams of its own designs. `tools/manual-pins.csv`
gives the pin assignments of section 2.11 of the manual as facts.
