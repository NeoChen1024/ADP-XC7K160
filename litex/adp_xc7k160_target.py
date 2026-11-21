#!/usr/bin/env python3
#
# LiteX SoC for the Andes AndeShape ADP-XC7K160: a CPU, the BIOS, the UART and the 1 GiB DDR3.
#
#   python3 adp_xc7k160_target.py --build [--load] [--sys-clk-freq 100e6] [--output-dir DIR] ...
#
# The DDR3 is main RAM (LiteDRAM, K7DDRPHY at 1:4), so its data rate is 8 times the system clock:
# DDR3-800 at 100 MHz. With --integrated-main-ram-size the SoC has no DDR3 controller.
#
# Run it with the Python of a LiteX installation (README section 15). The build writes to
# DIR, ./build/adp_xc7k160 in the current directory by default. It refuses a directory inside this
# repository. --load uses ../jtag/load.sh, so set VIVADO and XVCD (or XVC) as for that script. If
# timing is not met, the script stops before the load with exit status 3. No option writes a flash.
#
# The SoC has these CSRs besides the standard ones:
#   buttons    bits 0-7: SW1 to SW8 pushed; bit 8: PWR_SW1 pushed; bit 9: PORn high
#   seven_seg  bits 0-6: segments A to G of 7SEG1 (right); bit 7: its point; bits 8-15: 7SEG2.
#              A 1 lights the segment. All are off after reset.
# The BIOS console is 115200 8N1 on the DB9 (COM1).

import os
import re
import sys

sys.dont_write_bytecode = True

from migen import Cat, ClockDomain, Signal

from litex.gen import LiteXModule
from litex.soc.cores.clock import S7IDELAYCTRL, S7MMCM
from litex.soc.cores.gpio import GPIOIn, GPIOOut
from litex.soc.integration.builder import Builder
from litex.soc.integration.soc_core import SoCCore

from litedram.modules import MT41J256M16
from litedram.phy import s7ddrphy

import adp_xc7k160_platform


class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, with_dram):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # HW_RST_SW1 resets the SoC. It does not reconfigure the FPGA (section 9).
        # SYSCLK is the clock that the board gives to the DDR3 (section 4).
        self.mmcm = mmcm = S7MMCM(speedgrade=platform.speedgrade)
        self.comb += mmcm.reset.eq(~platform.request("sys_rst_n") | self.rst)
        mmcm.register_clkin(platform.request("clk200"), 200e6)
        mmcm.create_clkout(self.cd_sys, sys_clk_freq)
        platform.add_false_path_constraints(self.cd_sys.clk, mmcm.clkin)

        if with_dram:
            self.cd_sys4x  = ClockDomain()
            self.cd_idelay = ClockDomain()
            mmcm.create_clkout(self.cd_sys4x,  4*sys_clk_freq)
            mmcm.create_clkout(self.cd_idelay, 200e6)
            self.idelayctrl = S7IDELAYCTRL(self.cd_idelay)


class BaseSoC(SoCCore):
    def __init__(self, sys_clk_freq=100e6, speedgrade=-1, **kwargs):
        platform = adp_xc7k160_platform.Platform(speedgrade=speedgrade)

        with_dram = not kwargs.get("integrated_main_ram_size")
        self.crg = _CRG(platform, sys_clk_freq, with_dram)

        SoCCore.__init__(self, platform, sys_clk_freq, ident="LiteX SoC on ADP-XC7K160", **kwargs)

        if with_dram:
            # Two PMF512816BBR (4 Gbit, x16). MT41J256M16 has the same geometry; MIG uses the same
            # substitute (section 8).
            self.ddrphy = s7ddrphy.K7DDRPHY(platform.request("ddram"),
                memtype      = "DDR3",
                nphases      = 4,
                sys_clk_freq = sys_clk_freq)
            self.add_sdram("sdram",
                phy           = self.ddrphy,
                module        = MT41J256M16(sys_clk_freq, "1:4"),
                l2_cache_size = kwargs.get("l2_size", 8192),
            )

        # U4 holds the factory design. With all three control lines high it does nothing (section 5).
        bpi = platform.request("bpi_ctrl")
        self.comb += [bpi.cs_n.eq(1), bpi.oe_n.eq(1), bpi.we_n.eq(1)]

        buttons = Signal(10)
        self.comb += buttons.eq(Cat(
            ~Cat(*[platform.request("user_btn", i) for i in range(8)]),
            ~platform.request("pwr_btn_n"),
            platform.request("por_n"),
        ))
        self.buttons = GPIOIn(buttons)

        segments = Signal(16)
        self.seven_seg = GPIOOut(segments)
        for n in range(2):
            pads = platform.request("seven_seg", n)
            self.comb += Cat(pads.segments, pads.dp).eq(~segments[8*n:8*n + 8])


def _inside(path, directory):
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    return os.path.commonpath([path, directory]) == directory


def _timing(report):
    """The slack line of a Vivado timing summary, and whether all constraints are met."""
    with open(report) as f:
        text = f.read()
    m = re.search(r"WNS\(ns\)\s+TNS\(ns\).*\n.*\n\s*(\S+)\s+\S+\s+\S+\s+\S+\s+(\S+)\s+\S+\s+\S+\s+\S+\s+(\S+)", text)
    slack = f"setup slack {m[1]} ns, hold slack {m[2]} ns, pulse width slack {m[3]} ns" if m else "no slack line"
    return slack, "All user specified timing constraints are met." in text


def main():
    from litex.build.parser import LiteXArgumentParser
    parser = LiteXArgumentParser(platform=adp_xc7k160_platform.Platform, description="LiteX SoC on ADP-XC7K160.")
    parser.add_target_argument("--sys-clk-freq", default=100e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--speedgrade",   default=-1, type=int, choices=[-1, -2], help="Speed grade for Vivado (README section 2).")
    args = parser.parse_args()

    soc = BaseSoC(sys_clk_freq=args.sys_clk_freq, speedgrade=args.speedgrade, **parser.soc_argdict)
    builder = Builder(soc, **parser.builder_argdict)
    if _inside(builder.output_dir, adp_xc7k160_platform._REPOSITORY):
        parser.error(f"the output directory {builder.output_dir} is inside the repository; use --output-dir")

    if args.build:
        builder.build(**parser.toolchain_argdict)
    report = os.path.join(builder.gateware_dir, f"{soc.platform.name}_timing.rpt")
    if (args.build and builder.compile_gateware) or args.load:
        slack, met = _timing(report)
        print(slack)
        if not met:
            print(f"timing is not met; see {report}", file=sys.stderr)
            sys.exit(3)

    if args.load:
        soc.platform.create_programmer().load_bitstream(builder.get_bitstream_filename(mode="sram"))


if __name__ == "__main__":
    main()
