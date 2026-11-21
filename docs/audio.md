# Audio

The board has a Realtek ALC203 AC97 codec (U8) with a 24.576 MHz crystal, on bank 13. The signal
names start with `AC97_`.

**The codec works** **[measured, 2026-11-20, workbench]**. Its vendor ID is `414C 4770`.

- With `AC97_RSTn` high, `AC97_BCLK` is 12.288 MHz. With `AC97_RSTn` low, it stops.
- `AC97_BCLK` is on K26, which is not a clock-capable pin. A design that uses it as a clock needs
  `CLOCK_DEDICATED_ROUTE FALSE` on its net to the BUFG.
- The workbench runs the AC-link itself (frames at 48 kHz). The status is `000F`: the ADC, the DAC,
  the mixer and the reference are ready. The registers can be read and written. The default sample
  rate is 48000. VRA is available (extended ID `0A07`).
- Loop test: a square wave of ±4096 goes to the DAC. The ADC records the sum of the mixer (record
  select `0505`, all outputs muted). The ADC gives a swing of 4532 on the left and 4623 on
  the right. With no sound it gives 4. With sound on one side only, the other side stays at 4 to 5.
  Thus left and right are not exchanged, and there is no crosstalk.
- `AC97_SYNC` and `AC97_SDO` must be low at the moment `AC97_RSTn` goes from low to high. If not,
  the codec goes into a test mode.
- `workbench.py tone` sends 1 kHz to the left output and 500 Hz to the right output. The sound was
  heard.

The LiteX SoC has an AC97 controller with DMA ([litex/README.md](../litex/README.md)), and Linux has
an ALSA driver for it ([litex/linux/README.md](../litex/linux/README.md)).
