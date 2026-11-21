# FPGA and speed grade

## The chip

| Item | Value | Source |
|---|---|---|
| Part | XC7K160T, package FBG676 | **[measured]** marking on the chip |
| Marking | `XC7K160T FBG676ABX1525 DF5089150A 1C`: speed grade **-1**, commercial | **[measured]** photograph |
| Vivado part | `xc7k160tfbg676-1` ("Speed grade" below says when `-2` is used) | |
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

## Speed grade

The chip is marked -1. The DDR3-1600 designs and the LiteX designs here are built as **-2**. The
reasons and the evidence:

- For -1 in the FB package, MIG permits DDR3-800 at most. DDR3-1600 needs the -2 settings
  ([ddr3.md](ddr3.md)).
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
