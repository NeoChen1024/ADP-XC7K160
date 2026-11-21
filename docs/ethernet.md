# 10/100 Ethernet

The board has a DM9161C PHY (U9) with MII on bank 12, `RJ45_CON2` and a 25 MHz crystal. The signal
names start with `MAC_`; the power-down pin is `PHY_PWRDWN`. The Gigabit PHY (U12) is not fitted
([peripherals.md](peripherals.md)).

**The 10/100 Ethernet works** **[measured, 2026-11-20, workbench]**:

- The PHY ID on MDIO is `0181 B8B1` (Davicom DM9161 family).
- On a LAN, autonegotiation gives 100BASE-TX full duplex. `MAC_RX_CLK` and `MAC_TX_CLK` are both
  25.0016 MHz.
- With the loopback of the PHY (BMCR bit 14), 200 bytes that the workbench sent came back the same.
  After the loopback bit is set, the PHY needs about one second before it receives its own frames.
- On a real LAN (`workbench.py net`, `workbench/results/net-2026-11-20.txt`), ARP, ping and UDP
  worked in both directions. The board sent 20000 frames of 1518 bytes at 99 Mbit/s and 20000
  frames of 64 bytes, and the host received all of them correctly. The board received 30000
  datagrams from the host, each with a correct FCS.
- `MAC_RX_CLK` (AA23) is on an SRCC pin and `MAC_TX_CLK` (Y23) is on an MRCC pin, so a design can
  use them as clocks.
- The PHY operates normally while `PHY_PWRDWN` (Y20) is low.
- `MAC_PHY_LINKSTS` (AF22) follows the pull: the PHY does not drive it.

The LiteX MAC and Etherbone use this PHY ([litex/README.md](../litex/README.md)), and so does the
`liteeth` driver of Linux ([litex/linux/README.md](../litex/linux/README.md)).

## Straps: do not put `PULLUP` on these pins

**The address and the mode of this PHY depend on the pulls that the FPGA design puts on the MII
pins.** When the DM9161 comes out of reset, it reads its own output pins as straps:

| Pins | Strap |
|---|---|
| `RXD0` to `RXD3`, `CRS` | PHY address |
| `COL` | RMII |
| `TX_CLK` | Isolate |
| `RX_ER` | Repeater |
| `RX_DV` | Test mode |

Its reset is `PORn` ([buttons-displays-resets.md](buttons-displays-resets.md)). After each
configuration the design is in operation, and the PHY stays in reset for about 385 ms more. Thus the
PHY reads the pulls of the design. It does not read the pull-ups that the pins have before
configuration.

- With a pull-down or no pull on these pins: address 0, MII, BMCR `3100`. All is correct.
- With `PULLUP` on these pins: address 31, BMCR `1400` (isolate), register 16 is `0D14` (RMII,
  repeater). There is no `RX_CLK` or `TX_CLK`, and no link. A write of the correct bits through
  MDIO does not repair it, and a software reset does not repair it.

Thus **do not put `PULLUP` on the pins of this PHY**. Vivado puts a pull-down on a pin that the
design does not use, which is correct here.
