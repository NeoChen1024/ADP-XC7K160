# Clocks

| Signal | Pin | Source | Frequency | Status |
|---|---|---|---|---|
| `OSCCLK1` | G24 (bank 14, SRCC) | X1 | 20 MHz, LVCMOS33 | **[measured]** The main clock of the MIG designs here |
| `OSCCLK2` | D23 (bank 14, SRCC) | X3 | 20 MHz | **[measured]** **No signal.** The pin follows the weak pull of the FPGA: X3 is not fitted |
| `RTC_32K` | H17 (bank 15, SRCC) | X2 | 32.768 kHz | **[measured]** Present: 32.767 kHz |
| `SYSCLK_P/N` | AA10, AB10 (bank 33, SRCC) | U22 | 200 MHz, differential | **[measured]** For MIG and LiteDRAM. It also works alone as LVDS: 199.978 MHz |
| `CKOUT1_P/N` | D6, D5 (MGTREFCLK0, bank 116) | U14 | 100 MHz, LVDS | **[measured]** Present: 99.997 MHz |
| `PCIE_CLK_P/N` | H6, H5 (MGTREFCLK0, bank 115) | PCIe slot | 100 MHz | **[measured]** No signal when the board is not in a slot |

The workbench measured the frequencies on 2026-11-20. It counts the rising edges in 20,000,000
periods of `OSCCLK1`, which it takes as one second. Thus each figure is relative to X1. The
resolution is 1 Hz. The three differential clocks are divided by 16 first, so their resolution is
16 Hz.

Important points:

- **An MMCM makes a high frequency directly from 20 MHz** **[measured]**. Use `MMCME2_BASE` with
  `CLKIN1_PERIOD=50.0` and `DIVCLK_DIVIDE=1`, and keep the VCO at or below 1200 MHz:
  `CLKOUT0_DIVIDE_F = 1200 / f` and `CLKFBOUT_MULT_F = f × CLKOUT0_DIVIDE_F / 20`. For example,
  400 MHz is ×60 and ÷3; 300 MHz is ×60 and ÷4. The multiplier has steps of 1/8, so
  `f × divider` must be a multiple of 2.5. Designs ran with 300 to 525 MHz made in this way.
- **In MIG, `SYSCLK` is `DIFF_SSTL15`.** The XDC of MIG sets it, as on the KC705. The manual says
  LVDS. Without MIG, `IOSTANDARD LVDS` with an `IBUFDS` and no `DIFF_TERM` works
  **[measured, workbench]**. The same 1.5 V bank 33 also had `LVCMOS15` inputs, and the DRC of
  Vivado accepted it. If a design has MIG, the XDC of MIG owns these two pins. Do not set them
  again in your XDC.
- **`CKOUT1` and `PCIE_CLK` are on GT reference clock pins.** Only an `IBUFDS_GTE2` can receive
  them. Its output can go directly to a `BUFG` as a general clock. The design does not need a GTX
  channel **[measured, workbench]**.
- Use the 20 MHz clock directly for slow logic (UART, flash, buttons, displays). Then that logic
  does not have to close timing with a fast core. A design with MIG then has three clock domains:
  20 MHz, the core clock from the MMCM, and the `ui_clk` of MIG.
