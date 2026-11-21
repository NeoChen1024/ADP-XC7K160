# UART (RS-232, COM1)

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
- The transceiver of the second port (S2) is present: `S2_RXD`, an output of the transceiver, stays
  high. While `S1_RTS` is 1, the CTS of the host is not asserted. The transceiver inverts it, as it
  inverts TXD.

The host port used here is an RS-232 port of a PC motherboard (a 16550A, 115200 baud at most). The
FIFO of a 16550A has only 16 bytes. When the host is busy, continuous output loses bytes: one time 7
bytes in about 680 kB. Reliable transfer needs flow control or framing. To make 115200 baud from
20 MHz, the divider is 174, which gives 114,943 baud (−0.2%).

A CH347 USB adapter (`1a86:55da`) is not suitable. It had these problems:

- Data that it receives while the port is closed comes out after the port opens.
- It cannot send a Break.
- It gives received data to the host only after 512 bytes, or after 0.5 ms without data.
