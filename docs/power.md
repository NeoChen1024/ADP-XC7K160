# Banks and power

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

## SW11 and bank 16

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

## Power input and LEDs

A 12 V, 5 A adapter goes into `POWER_JACK1` (or the ATX 4-pin `J19`). `POWER_SW1` is the switch.
The board makes 5 V, 3.3 V, 1.8 V, 1.5 V, 1.2 V, 1.0 V and one adjustable supply. LED12 to LED17 show
1.0 V, 3.3 V, 1.8 V, 1.5 V, 1.2 V and VTT 0.75 V. LED7 shows DONE. **The board has no general user
LED.**

## Pins that the manual does not list

The pin table of the manual does not have 20 user I/O pins of bank 33 and one of bank 32 (AB20)
**[package]**. VRN and VRP of bank 32 (V13, W13) are not in it either. To list them, find the rows
of `package-pins.csv` with an empty `board` column and a `pin_func` that starts with `IO_`.

The workbench found these results **[measured, 2026-11-20]**:

- The 21 user I/O pins follow the pull. Nothing drives them, so take them as not connected.
- `VRN` and `VRP` of bank 32 also follow the pull: they have no reference resistors. Thus
  `DCI_CASCADE` ([ddr3.md](ddr3.md)) is necessary and not optional.
- `VRN` of bank 33 is always high and `VRP` always low: the resistors are there.
