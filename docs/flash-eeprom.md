# User SPI flash U17 and EEPROM U16

## U17

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

## U16

**U16** is an AT24C128 I2C EEPROM (16 KB) on `I2C_SCL` M25 and `I2C_SDA` L25 (bank 13). The board
has pull-ups on both lines. Only address `0x50` answers on the bus, and the first 32 bytes are all
`FF` **[measured, workbench; read only, never written]**. The memory address has two bytes.
