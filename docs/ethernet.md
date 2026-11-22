# 10/100 Ethernet

The board has a Davicom DM9161CEP PHY (U9, the marking) with MII on bank 12, `RJ45_CON2` and a
25 MHz crystal. The signal names start with `MAC_`; the power-down pin is `PHY_PWRDWN`. The
Gigabit PHY (U12) is not fitted ([peripherals.md](peripherals.md)).

**The 10/100 Ethernet works** **[measured, 2026-11-20, workbench]**:

- The PHY ID on MDIO is `0181 B8B1`: model 11, revision 1. This is the ID that the DM9161C
  datasheet gives (see [The datasheet](#the-datasheet)).
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
- `MAC_PHY_LINKSTS` (AF22) follows the pull: the PHY does not drive it. The pin is U9.32, which is
  `MDINTR` in the datasheet, the interrupt output. It is not the link status (U9.14).

**The PHY gives a preamble of variable length at 10 Mbit/s** **[measured, 2026-11-23, direct link
to an Intel I211]**. A design must align the received bytes on the start frame delimiter (SFD).

- The receive path of `adp_ethmac.py` counts the nibbles 5 before the SFD. At 100 Mbit/s each frame
  had 15. At 10 Mbit/s the frames had 13 or 14.
- That path counted no burst of `RX_DV` without an SFD, at 100 Mbit/s and at 10 Mbit/s.
- With it, at 10 Mbit/s full duplex, 100 of 100 pings came back with 0 to 1472 bytes of data, and
  the CRC error count stayed at 0. TCP gave 9.33 Mbit/s in each direction.

The datasheet agrees with this. Its MII section requires only that `RX_DV` starts at the SFD or
earlier. At 100 Mbit/s the PHY replaces the J/K symbols with two nibbles of preamble, so the
preamble is complete. The datasheet does not give the length of the preamble at 10 Mbit/s.

The receive path of LiteEth (`LiteEthPHYMIIRX`) makes bytes from the nibbles in pairs, from the
first nibble with `RX_DV`. Before the change, the reference SoC used it, with these faults:

- At 10 Mbit/s, 62 to 87 of 100 pings came back, and the CRC error count increased.
- At 100 Mbit/s, the preamble error count of LiteEth increased by one for each received frame,
  although the frames were correct. The PHY sends no burst without an SFD (see above), so the cause
  is in `LiteEthPHYMIIRX`. The exact mechanism was not examined.

The PTP stamps of `adp_ethmac.py` are at the MII. The Linux driver adds the typical latency of the
PHY from the datasheet ([litex/linux/README.md](../litex/linux/README.md)). The datasheet does not
guarantee these values, and the board did not measure them ([unknowns.md](unknowns.md)).

The LiteX MAC, the MAC with DMA and Etherbone use this PHY ([litex/README.md](../litex/README.md)).
Linux reached about 94 Mbit/s of TCP into the board, and 90 Mbit/s out with the MAC with DMA
([litex/linux/README.md](../litex/linux/README.md)).

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
| `RX_CLK` | 10 Mbit/s mode: GPSI if pulled down, MII if pulled up (internal pull-up) |
| `MAC_PHY_LINKSTS` (`MDINTR`) | Polarity of the interrupt: active high if pulled down |

Register 24 of the PHY shows the latched values. With the LiteX SoC it was `84E0`
**[measured, 2026-11-23]**: address 0, MII, node mode, MII at 10 Mbit/s, autonegotiation of all
modes, and `MDINTR` active high. The LED pins of the PHY are also straps; they do not go to the
FPGA.

Its reset is `PORn` ([buttons-displays-resets.md](buttons-displays-resets.md)). After each
configuration the design is in operation, and the PHY stays in reset for about 385 ms more. Thus the
PHY reads the pulls of the design. It does not read the pull-ups that the pins have before
configuration.

- With a pull-down or no pull on these pins: address 0, MII, BMCR `3100`. All is correct.
- With `PULLUP` on these pins: address 31, BMCR `1400` (isolate), register 16 is `0D14` (RMII,
  repeater). There is no `RX_CLK` or `TX_CLK`, and no link. A write of the correct bits through
  MDIO does not repair it, and a software reset does not repair it.

Thus **do not put `PULLUP` on the pins of this PHY**. Vivado puts a pull-down on a pin that the
design does not use. That is correct for all straps except `RX_CLK`: a pull-down on it selects
GPSI at 10 Mbit/s. A design with Ethernet uses `RX_CLK` as an input with no pull, so the internal
pull-up of the PHY sets MII.

## The datasheet

The text above uses the Davicom datasheet "DM9161C 10/100 Mbps Fast Ethernet Physical Layer Single
Chip Transceiver", version DM9161C-DS-F01 (2011-10-26). An earlier datasheet for the DM9161,
version DM9161-DS-F05 (2008-09-10), was also read. It does not apply to this PHY: its PHY ID is
`0181 B880` (model 8, revision 0), and these items are different:

- Pin 31 is `RXEN` in DM9161-DS-F05 and `LEDMODE` in DM9161C-DS-F01. Pin 39 is `DVDD` and
  `DISMDIX` (automatic MDI/MDI-X). Pin 45 is the fiber signal detect and `NC`.
- `MDINTR` is an open-drain output in DM9161-DS-F05. In DM9161C-DS-F01 it is also a strap, and the
  strap sets its polarity.
- `RX_CLK` also selects the scrambler bypass in DM9161-DS-F05. The DM9161C has no 100BASE-FX mode.
- The DM9161C has automatic MDI/MDI-X (register 20), and the defaults of register 16 and the BMSR
  are different.
- DM9161-DS-F05 gives 2.5 MHz as the limit of MDC; DM9161C-DS-F01 gives 12.5 MHz.

The two datasheets give the same typical latencies, in bit times (10 ns at 100 Mbit/s, 100 ns at
10 Mbit/s). They are typical values at 25 °C, not guaranteed:

| Speed | Transmit (`TX_EN` to the cable) | Receive (the cable to `RXD`) |
|---|---|---|
| 100 Mbit/s | 8 | 15 |
| 10 Mbit/s | 2 (maximum 4) | 7 |

The registers of the PHY with the LiteX SoC and a link to an Intel I211 **[measured, 2026-11-23,
`SIOCGMIIREG` in Linux]**:

- Register 16 was `0414`. DM9161C-DS-F01 gives `1614` as the default: bits 12 and 9 are different.
  Register 17 was `8208`: 100BASE-TX full duplex, autonegotiation complete. Bit 9 is reserved in
  the datasheet, but it was 1.
- Register 20 was `0080`: the PHY used MDI-X on the direct cable.
- Register 21 was `1F00`: all interrupts are masked.
- Registers 22 (the count of `RX_ER` events) and 23 (the count of link drops) were `002F` and
  `0004`, and `0000` at the next read. A read clears them, as the datasheet says.
- `mii-tool -vv` of net-tools 2.10 showed `0000` for registers 16 and 17. A direct `SIOCGMIIREG`
  read gave the values above.

