# Peripherals and connectors

The pins are in `pins.csv` and `adp_xc7k160_master.xdc`. Find them by the start of the signal name:

| Peripheral | Part or connector | Signal prefix | Bank | Status |
|---|---|---|---|---|
| 10/100 Ethernet (MII) | U9 DM9161CEP, `RJ45_CON2`, 25 MHz crystal | `MAC_`, `PHY_PWRDWN` | 12 | Works: [ethernet.md](ethernet.md) |
| Gigabit Ethernet (GMII), **optional** | U12 88E1119R, `RJ45_CON1` | `PHY_` | 34 (1.8 V) | Not fitted |
| SD card | `SD_CON1`; `SD_PWR_ON` controls the power switch U31 | `SD_` | 13, 12 | Works: [sd-card.md](sd-card.md) |
| Audio | U8 ALC203 (AC97), 24.576 MHz crystal | `AC97_` | 13 | Works: [audio.md](audio.md) |
| LCD module and touch panel | `LCD_CON1`, `LCD_CON2` (24-bit RGB and touch SPI) | `LCD_`, `TP_` | 13, 12 | Not used |
| USB OTG (UTMI+), **optional** | U5 USB3500, U7 power switch | `USB_`, UTMI signal names | 15 | Not fitted |
| USB 1.1 device, **optional** | U6 | `U_` | 14 | Not fitted |
| IDE/CF | `IDE_CON1` (44-pin) | `CFC_`, `IDE_` | 16 | Not used |
| AICE debug | `AICE_CON1` (20-pin), `AICE_CON3` (10-pin) | `ICE_` | 12 | Not used |
| Trace | `ACON2` (20-pin) | `TRC_` | 12 | Not used |
| PCIe x8 | Edge connector | `PCIE_`, `CKOUT1_` | 115, 116, 16 | Not used |

The I2C EEPROM and the SPI flash are in [flash-eeprom.md](flash-eeprom.md). The buttons and the
displays are in [buttons-displays-resets.md](buttons-displays-resets.md).

## Parts that are not fitted

**[measured, 2026-11-20, workbench]** ("follows the pull" is defined in
[workbench/README.md](../workbench/README.md))

- **The Gigabit Ethernet PHY (U12) is not fitted.** `PHY_MDIO` has a pull-up, but none of the 32
  addresses answers. All other `PHY_` pins follow the pull.
- **The USB parts (U5, U6) are not fitted.** That area of the board is empty (seen). The
  measurement agrees: `USB_CLKOUT` has no clock, and the UTMI pins and the 16 `U_` pins all follow
  the pull.

## Notes

- **The trace connector shares pins with other signals.** `TRC_CLK` and `ICE_DBGRQ` are both on
  AC24. `TRC_DATA0..3` share U24, U25, V23 and V24 with `TP_SPI_DO`, `TP_SPI_DI`, `TP_SPI_CLK` and
  `TP_IRQ`. Thus `pins.csv` has 445 rows for 440 pins.
- The `ICE2_*` signals of bank 16 are for `AICE_CON2`, which was removed. The manual marks them
  "Not be used".
- PCIe: lanes 0 to 3 are in bank 116 and lanes 4 to 7 in bank 115. Lane 0 is `MGTX*3_116` and lane
  7 is `MGTX*0_115`. This reversed order is usual for an add-in card. `PCIE_PERST_N` is on F9 (bank
  16, so its voltage depends on SW11). The board itself has the shape of a PCIe card. Nothing drives
  `PCIE_PERST_N` when the board is not in a slot.
- Test points: 18 I/O pins of bank 34 and 6 of bank 16 go to test points (the `pcb_symbol` column
  of `pins.csv`).
