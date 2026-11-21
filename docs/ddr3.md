# DDR3 and MIG

## Hardware

- Two **PieceMakers PMF512816BBR-MBDN** devices **[measured, the marking]**. Each is 4 Gbit
  (256M × 16). Together they are one 32-bit memory of **1 GiB**. No datasheet for the B die was
  found. The datasheet for the F die of the same family gives 1.5 V, 96 balls, A0 to A14 and BA0 to
  BA2. It says that `-MB` is DDR3-1866 13-13-13. The manual says DDR3-800; the devices are much
  faster than that.
- Bank 32 (HP, 1.5 V) has the four data bytes. Byte *n* is in the T*n* group, and its DQS is on
  the DQS pins of that group. `DDR3_RST_B` is on AC16. Bank 33 has the address and control in T0 to
  T2, CK on W10 and W9, and the 200 MHz clock on AA10 and AB10. **MIG accepts this pin assignment
  as it is** **[measured]**.
- The VREF pins of bank 32 (AE16, Y18) connect to VTT 0.75 V: the board uses external VREF.
- Only bank 33 has DCI reference resistors (U9 VRN, V12 VRP). Bank 32 has none. Thus the design
  needs this line, and **MIG does not write it**:

  ```tcl
  set_property DCI_CASCADE {32} [get_iobanks 33]
  ```

  With this line, DCI matches on the board **[measured]**.

## MIG parameters

MIG 7 series 4.2, native interface. `mig/mig_prj.py` writes the full `mig.prj`. The main values:

| Parameter | Value |
|---|---|
| Memory part | `MT41J256m16XX-107` (1300 and above), `-125` (800, 1066). A Micron part with the same specification replaces the PieceMakers part. The `m` in the name is lower case: the part table of MIG spells it so |
| Data width | 32 bits, 4 DQS, with data mask, no ECC |
| Address | Row 15, column 10, bank 3; `BANK_ROW_COLUMN` |
| PHY ratio | 4:1 |
| System clock | 200 MHz, differential, pins AA10 and AB10 (`DIFF_SSTL15`) |
| Reference clock | Use System Clock |
| Reset polarity | `sys_rst` is **active low** |
| I/O | DQ `SSTL15_T_DCI`, DQS `DIFF_SSTL15_T_DCI`, the others `SSTL15`, CK `DIFF_SSTL15`, reset `LVCMOS15`; DCI cascade on, external VREF |
| Memory voltage | 1.5 V |
| Timing | tRFC 260 ns, tREFI 7.8 µs, tRAS 37.5, tRCD 15, tRP 15, tFAW 50, tRRD 10, tWTR 7.5, tRTP 7.5, tCKE 7.5 ns |
| Mode register | BL8, sequential; output drive RZQ/7; RTT_NOM RZQ/4 (60 Ω); RTT_WR off; AL 0 |
| XADC | Enabled. MIG instantiates the XADC for temperature compensation and gives the 12-bit reading on `device_temp` |
| Other | 4 bank machines, ordering Normal, debug off |

The settings for each rate:

| Rate | tCK | CL / CWL | Part in the MIG project | VccAuxIO | `ui_clk` |
|---|---|---|---|---|---|
| 800 | 2500 ps | 6 / 5 | `xc7k160t-fbg676/-2` | 1.8 V | 100 MHz |
| 1066 | 1875 ps | 7 / 6 | `xc7k160t-fbg676/-2` | 1.8 V | 133 MHz |
| 1300 | 1538 ps | 9 / 7 | `xc7k160t-ffg676/-2` | 1.8 V | 163 MHz |
| **1600** | 1250 ps | 11 / 8 | `xc7k160t-ffg676/-2` | 2.0 V | 200 MHz |
| 1800 | 1111 ps | 13 / 9 | `xc7k160t-ffg676/-2` | 2.0 V | 225 MHz |

The reasons for these settings:

- **The limit of MIG comes from the part, package and speed grade that you give it** (the
  `time_periods.csv` of MIG). For the FB package, the fastest period is 2500 ps for -1 and 1875 ps
  for -2. A faster rate needs a project for FFG676, which has the same pins. From 1600, it also
  needs the 2.0 V VccAuxIO that FBG does not have. After MIG generates its files, the design
  builds for the real `xc7k160tfbg676`. Thus the timing report is for the real part. What MIG does
  not guarantee is the eye at the pins.
- **With a 200 MHz oscillator, MIG accepts only the periods that it can make**: 2500, 1875, 1538,
  1250 and 1111 ps. It refuses 1500 and 1072 (`Invalid Input Clock Period`).
- **If tCK is less than 1500 ps (1600, 1800), MIG makes one more clock, the IDELAY reference.** It
  is 300 MHz for speed grade 1 and 400 MHz for grades 2 and 3 (`FPGA_SPEED_GRADE` in `mig_mig.v`).
  A 400 MHz IDELAY reference does not pass DRC on a -1 part (`AVAL-31`: -1 permits only 200 or
  300 MHz). Thus, with the -2 project that `mig_prj.py` writes, **the design must also build as
  `xc7k160tfbg676-2`**. To build 1600 as -1, change `GRADE` in `mig_prj.py` to `"-1"`. MIG permits
  FFG676 -1 with 2.0 V. The +0.013 ns of the table below came from such a build on 2026-11-18.
- The other fields, such as `<LowPower_En>ON` and `<XADC_En>Enabled`, are the same as in the prj
  of the KC705 board file.

## Results on the board

The memory test (`example-ddr3-test/`) fills 1 GiB and reads it back. Each word is a hash of its
address and a seed. Each pass has a new seed. Each odd pass inverts every second word. The die was
at 49 to 54 °C. At each rate, 20 new calibrations all passed:

| Rate | Worst slack, build grade | Calibration time | Passes without error |
|---|---|---|---|
| 800 | +0.94 ns, -1 | 108 to 110 ms | More than 250 |
| 1066 | +0.58 ns, -1 | 606 to 639 ms | 745 |
| 1300 | +0.30 ns, -1 | 325 to 341 ms | 310 |
| **1600** | +0.013 ns, -1 | 382 to 391 ms | 291 |
| 1800 | −0.24 ns as -1; +0.046 ns as -2 | 334 to 347 ms | 1786 |

One pass of 1 GiB (the write and then the read) takes about 1.4 seconds at DDR3-800 and about 0.7
seconds at 1600.

**Use 1600.** The memory test passed at 1800. But in a larger design at 1800, calibration did not
complete after 11 of 101 JTAG loads. MIG stayed not ready until the next load. The cause was not
found. At 1600, 36 loads had no failure, and none came in long use after that.

LiteDRAM of LiteX is a different controller for the same memory. Its limits and results are in
[litex/README.md](../litex/README.md).

## Notes for the use of MIG

- **`app_addr` has 29 bits, but only the low 28 bits address memory.** The unit is a 32-bit word
  (2^28 × 4 = 1 GiB). Bit 28 is the rank. There is one rank, so an address with bit 28 set is an
  alias.
- At 4:1 and 32 bits, `app_wdf_data` and `app_rd_data` have **256 bits**. One beat is one BL8 burst
  (8 words), and the address increases by 8 each time. One transfer is one command and one data
  beat. Set `app_wdf_end` together with `app_wdf_wren`.
- `sys_rst` is active low. The designs here hold it low for 52 ms after configuration (2^20 cycles
  of 20 MHz) and then release it.
- MIG makes `ui_clk`. It has no relation to a clock made from the 20 MHz source, so a crossing
  needs synchronizers. One method is a toggle handshake, with the data held constant beside it. The
  constraint is `set_max_delay -datapath_only` or `set_clock_groups -asynchronous`. See the XDC of
  the example.
- MIG uses the only XADC. For the temperature, use its `device_temp`. A design that needs the
  XADC must set `XADC_En` of MIG to off and give MIG the temperature.
- Latency: at DDR3-1600, the first word comes back about **178 ns** after the read request
  **[measured]**. This includes two synchronizers, the command path of MIG, CL 11 and the return
  path. A design that needs fast memory must put a cache before MIG.
- Resources: MIG with the tester uses 8,009 LUT and 6,860 FF (DDR3-1600). MIG uses 1 PLL, 2 MMCM,
  2 PHASER_REF, 1 IDELAYCTRL and the XADC. One of the two MMCM is the IDELAY reference clock, which
  exists only from 1600.

## Generate MIG (non-project)

```sh
VIVADO=/path/to/vivado sh mig/generate.sh 1600 /some/writable/directory/mig-1600
```

After some minutes the script prints the path of `user_design`. The design reads these files:

```tcl
foreach dir {clocking controller ecc ip_top phy ui} { read_verilog [glob $mig/rtl/$dir/*.v] }
read_verilog $mig/rtl/mig.v $mig/rtl/mig_mig.v      ;# do not read mig_mig_sim.v
read_xdc $mig/constraints/mig.xdc                   ;# read it first, then your own XDC
```

The DDR3 ports of the top level must keep the names of MIG (`ddr3_dq`, `ddr3_dqs_p`, `sys_clk_p`
and so on), because `mig.xdc` constrains those names. `mig_prj.py` sets `sys_rst` and
`init_calib_complete` to have no pin.
