# Buttons, seven-segment displays, resets

## Buttons

SW1 to SW8 (GPIO1 to GPIO8 in the manual):

| Buttons | Pins | Bank | Status |
|---|---|---|---|
| SW1 to SW4 | E21, F24, J21, L23 | 14 (3.3 V) | **[measured]** |
| SW5 to SW8 | A13, A12, J14, D9 | 16 (SW11, [power.md](power.md)) | **[measured]** The same as SW1 to SW4 |

**None of the eight has a pull-up on the board.** The XDC must set `PULLUP TRUE`. A pushed button
then reads low **[measured, 2026-11-20, workbench]**. The workbench read SW5 to SW8 as `LVCMOS18`
inputs. The design must debounce the buttons. One method that works is to sample each millisecond
and to accept a level after four equal samples.

## Seven-segment displays

7SEG1 and 7SEG2 (bank 12, LVCMOS33) are common anode: **a low output lights the segment**
**[measured]**. **7SEG2 is the left digit (the high digit)** **[measured]**.

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

Each segment has its own pin, so the design does not scan. The `digits` command of the workbench lit
each of the sixteen segments in turn (2026-11-20), and each agreed with the table.

## Reset and power buttons, `PORn`

**[measured, 2026-11-20, workbench]**

| Button | Signal | Pin | Behaviour |
|---|---|---|---|
| `HW_RST_SW1` | `SYS_RSTn` (through U19) | U22 | High at rest, low while pushed. It goes only to the FPGA. It does not reset the Ethernet PHY |
| `PWR_SW1` | `GPIO0_ENn` | K21 | High at rest, low while pushed |
| (no button) | `PORn` (from U18) | U21 | Low while the FPGA is not configured. It goes high about **385 ms** after the design starts. Neither button moves it |

The board drives all three. The weak pull-down of the FPGA cannot move them, so they need no pull.

**`PORn` is not only a power-on reset: it comes again after each configuration of the FPGA.** After
a JTAG load, `PORn` starts low. In five loads it went high after 384.26 to 387.14 ms
(`workbench.py boot`). The clocks of the Ethernet PHY, `MAC_RX_CLK` and `MAC_TX_CLK`, first appear
0.5 µs after `PORn` goes high. Thus `PORn` is the reset of the peripherals on the board. A design can
use it as the sign that the peripherals are out of reset. Do not use the PHY before that.

**`HW_RST_SW1` does not reset the PHY.** A test wrote `0081` to register 4 of the PHY. Then the
button held `SYS_RSTn` low for 0.68 seconds. After that the register was still `0081`, and the
address of the PHY was the same.

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
A short circuit in the SD socket once caused this ([sd-card.md](sd-card.md)).

**`HW_RST_SW1` is only an input for the design.** It does not start a new configuration of the FPGA:
the workbench continued to answer after the button was pushed. The design decides if it is a reset.
`PWR_SW1` is only an input too. A design needs neither: it can make its power-on reset from the
`LOCKED` output of its MMCM.
