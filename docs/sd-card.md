# SD card

The socket is `SD_CON1`. Its signals start with `SD_`, on banks 13 and 12. `SD_PWR_ON` controls the
power switch U31.

**The socket works. Blocks were read and written on both interfaces** **[measured, 2026-11-20,
workbench]**. The clock was 238 kHz, and the card was one 32 GB SanDisk SDHC card (OCR
`C0FF8000`). The command is `workbench.py sdrw`; the result is
`workbench/results/sdrw-2026-11-20.txt`.

- SPI mode (`SD_DAT3` is CS, `SD_CMD` is MOSI, `SD_DAT0` is MISO). CMD59 made the card check each
  CRC. The CID and the CSD had a correct CRC (31.9 GB). For each of 8 blocks the test read the
  block, wrote a pattern, read the pattern back, and restored the block. All were correct. The card
  refused a block with a wrong CRC (data response `0B`) and kept the contents.
- The 4-bit bus. The CID was the same as in SPI mode. A block read on the four data lines was the
  same as in SPI mode, and the CRC16 of each line was correct. Write, read back and restore were all
  correct. With a wrong CRC on `SD_DAT2`, the card answered CRC status `101` and kept the contents.
  Thus **`SD_CMD` and `SD_DAT0` to `SD_DAT3` work in both directions**.
- LiteSDCard of LiteX read blocks at 21.875 MHz, and Linux uses the card as its root file system
  ([litex/linux/README.md](../litex/linux/README.md)). Linux runs the card in high speed mode at
  50 MHz, and that document gives the speed.

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

## If the card does not answer

On 2026-11-20 the VDD contact in the socket was bent and touched the metal cover. This made a short
circuit from `SD_3V3` to ground. The card did not answer (CMD0 read `FF`), LED11 was dark, and U31
was hot. When `SD_PWR_ON` went off and on, the 3.3 V supply went down, and `PORn` and `SYS_RSTn`
went low. After the contact was pushed back, all was normal.

If the card does not answer again, look at LED11 and touch U31 first. `workbench.py set SD_PWR_ON 0`
switches the power off.
