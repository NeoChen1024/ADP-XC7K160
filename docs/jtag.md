# JTAG

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

## The cable

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

## Problems that were found

**[measured]**

- A new `hw_server` has not yet scanned the JTAG chain when the first `open_hw_target -xvc_url`
  comes. That call fails (`[Labtools 27-2269] No devices detected`). The cure is to wait 1 second,
  call `close_hw_target`, and call `open_hw_target [get_hw_targets]`. `load.tcl` and
  `flash_u4.tcl` do this. Sometimes it fails some times in sequence. Run the load again; the board
  is not the cause.
- Vivado leaves `hw_server` and `cs_server` in operation when it stops. Stop them by name
  (`pkill -x`). `load.sh` does this.
- If you write raw scan patterns, end them with a 1. One time, when the cable was touched, a long
  scan went into the IR. With a 1 at the end, the instruction that loads by mistake is BYPASS.
- To move data through JTAG in a design, use `BSCANE2` (`JTAG_CHAIN=1`, USER1, IR `0x02`). The
  two designs of `flash-probes/` are examples that work.
