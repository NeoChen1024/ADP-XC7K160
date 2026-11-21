#!/usr/bin/env python3
#
# LiteX SoC for the Andes AndeShape ADP-XC7K160: a CPU, the BIOS, the UART and the 1 GiB DDR3.
#
#   python3 adp_xc7k160_target.py --build [--load] [--sys-clk-freq 175e6] [--output-dir DIR] ...
#
# The DDR3 is main RAM (LiteDRAM, K7DDRPHY at 1:4), so its data rate is 8 times the system clock:
# DDR3-1400 at the default 175 MHz, DDR3-800 at 100 MHz. The 4x clock goes through a BUFG, so
# 710 MHz (-2) limits the rate to DDR3-1420 (litex/README.md).
#
# With --integrated-main-ram-size the SoC has no DDR3 controller. With --with-bist the SoC also has
# the LiteDRAM BIST on the native port (BIOS: sdram_bist, sdram_hw_test).
#
# --with-etherbone --eth-ip A.B.C.D adds Etherbone on the 10/100 Ethernet: a UDP/IP stack in the
# FPGA that answers ARP, ping, and Wishbone accesses on UDP port 1234 (litex_server --udp). It
# only answers; it sends nothing by itself. Use an address that no station answers on that LAN.
# The MAC address is locally administered, the same as the workbench's.
#
# --with-ethernet --eth-ip A.B.C.D --remote-ip E.F.G.H adds the LiteEth MAC, for the CPU (BIOS
# netboot, the liteeth driver of Linux). The BIOS tries a TFTP boot from the remote address only
# if no other boot medium works; --tftp-port moves its server from port 69, so that a user can run
# it. --with-etherbone and --with-ethernet exclude each other.
#
# --with-ac97 adds the AC97 controller of adp_ac97.py: playback and capture at 48 kHz through two DMA
# engines, and the codec registers. The Linux driver is litex/linux/adp-ac97.c.
#
# --with-sdcard adds LiteSDCard on the 4-bit bus, for reading only: the SoC has no path that
# writes the card. --with-sdcard-write also adds the write path. Use it only with a card that the
# owner gave for writing (AGENTS.md).
#
# Run it with the Python of a LiteX installation (litex/README.md). The build writes to
# DIR, ./build/adp_xc7k160 in the current directory by default. It refuses a directory inside this
# repository. --load uses ../jtag/load.sh, so set VIVADO and XVCD (or XVC) as for that script. If
# timing is not met, the script stops before the load with exit status 3. No option writes a flash.
#
# The SoC has these CSRs besides the standard ones:
#   buttons    bits 0-7: SW1 to SW8 pushed; bit 8: PWR_SW1 pushed
#   status     bit 0: PORn high (the peripherals are out of reset); bit 1: a card is in the SD socket
#   xadc       temperature, vccint, vccaux, vccbram: the raw 12-bit values of the XADC, sampled
#              all the time. litex/linux/litex-xadc-hwmon.c gives them to Linux as hwmon.
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
from litex.soc.cores.xadc import S7SystemMonitor
from litex.soc.integration.builder import Builder
from litex.soc.integration.soc_core import SoCCore
from litex.soc.interconnect import wishbone

from litedram.modules import MT41J256M16
from litedram.phy import s7ddrphy

from liteeth.phy.mii import LiteEthPHYMII

import adp_xc7k160_platform


class _CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, with_dram):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # HW_RST_SW1 resets the SoC. It does not reconfigure the FPGA
        # (docs/buttons-displays-resets.md).
        # SYSCLK is the clock that the board gives to the DDR3 (docs/clocks.md).
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
    def __init__(self, sys_clk_freq=175e6, speedgrade=-2, with_bist=False,
        with_etherbone=False, with_ethernet=False, eth_ip=None, remote_ip=None, tftp_port=69,
        with_sdcard=False, with_sdcard_write=False, with_ac97=False, **kwargs):
        platform = adp_xc7k160_platform.Platform(speedgrade=speedgrade)

        with_dram = not kwargs.get("integrated_main_ram_size")
        self.crg = _CRG(platform, sys_clk_freq, with_dram)

        SoCCore.__init__(self, platform, sys_clk_freq, ident="LiteX SoC on ADP-XC7K160", **kwargs)

        if with_dram:
            # Two PMF512816BBR (4 Gbit, x16). MT41J256M16 has the same geometry; MIG uses the same
            # substitute (docs/ddr3.md).
            self.ddrphy = s7ddrphy.K7DDRPHY(platform.request("ddram"),
                memtype      = "DDR3",
                nphases      = 4,
                sys_clk_freq = sys_clk_freq)
            self.add_sdram("sdram",
                phy           = self.ddrphy,
                module        = MT41J256M16(sys_clk_freq, "1:4"),
                l2_cache_size = kwargs.get("l2_size", 8192),
                with_bist     = with_bist,
            )

        # With all three control lines high, U4 does nothing (docs/configuration.md).
        bpi = platform.request("bpi_ctrl")
        self.comb += [bpi.cs_n.eq(1), bpi.oe_n.eq(1), bpi.we_n.eq(1)]

        buttons = Signal(9)
        self.comb += buttons.eq(Cat(
            ~Cat(*[platform.request("user_btn", i) for i in range(8)]),
            ~platform.request("pwr_btn_n"),
        ))
        self.buttons = GPIOIn(buttons)

        status = Signal(2)
        self.comb += status.eq(Cat(platform.request("por_n"), ~platform.request("sd_cd_n")))
        self.status = GPIOIn(status)

        # The two known states: the PHY runs, and the socket has power (docs/ethernet.md,
        # docs/sd-card.md).
        self.comb += [platform.request("eth_pwrdwn").eq(0), platform.request("sd_pwr_on").eq(1)]

        if with_etherbone or with_ethernet:
            # PORn holds the PHY in reset for about 385 ms after the load
            # (docs/buttons-displays-resets.md). The core
            # needs nothing from the PHY before that.
            self.ethphy = LiteEthPHYMII(
                clock_pads = platform.request("eth_clocks"),
                pads       = platform.request("eth"))
        if with_etherbone:
            # A record of more words than buffer_depth is dropped without an error, and the host
            # tools of LiteX do not split a write. 255 is the largest count that a record can hold.
            self.add_etherbone(phy=self.ethphy, ip_address=eth_ip, mac_address=0x02adb0000001,
                buffer_depth=255)
        if with_ethernet:
            # More slots than the default of 2 give Linux at 100 MHz more time to empty them
            # (litex/linux/README.md, "Ethernet").
            self.add_ethernet(phy=self.ethphy, local_ip=eth_ip, remote_ip=remote_ip,
                mac_address=0x02adb0000001, nrxslots=8, ntxslots=4)
            self.add_constant("TFTP_SERVER_PORT", tftp_port)

        if with_sdcard or with_sdcard_write:
            self.add_sdcard(mode="read+write" if with_sdcard_write else "read")

        if with_ac97:
            from adp_ac97 import AC97
            def dma_master(mode):
                return wishbone.Interface(
                    data_width = self.bus.data_width,
                    adr_width  = self.bus.get_address_width(standard="wishbone", addressing="word"),
                    addressing = "word",
                    mode       = mode,
                )
            bus_play, bus_rec = dma_master("r"), dma_master("w")
            pads = platform.request("ac97")
            self.ac97 = AC97(pads, bus_play, bus_rec)
            dma_bus = getattr(self, "dma_bus", self.bus)
            dma_bus.add_master(name="ac97_play", master=bus_play)
            dma_bus.add_master(name="ac97_rec",  master=bus_rec)
            self.irq.add("ac97", use_loc_if_exists=True)
            platform.add_period_constraint(pads.bit_clk, 1e9/12.288e6)
            platform.add_false_path_constraints(self.crg.cd_sys.clk, self.ac97.cd_ac97.clk)
            # K26 is not clock capable: the clock goes from the IBUF to the BUFG on general routing.
            platform.add_platform_command("set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets ac97_bit_clk_IBUF]")

        # LiteDRAM, unlike MIG (docs/ddr3.md), does not use the only XADC of the device.
        self.xadc = S7SystemMonitor()

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
    parser.add_target_argument("--sys-clk-freq", default=175e6, type=float, help="System clock frequency.")
    parser.add_target_argument("--speedgrade",   default=-2, type=int, choices=[-1, -2], help="Speed grade for Vivado (docs/fpga.md).")
    parser.add_target_argument("--with-bist",    action="store_true", help="Add the LiteDRAM BIST.")
    parser.add_target_argument("--with-etherbone", action="store_true", help="Add Etherbone on the 10/100 Ethernet.")
    parser.add_target_argument("--eth-ip",       default=None, help="IP address of Etherbone; required with --with-etherbone.")
    parser.add_target_argument("--with-ethernet", action="store_true", help="Add the LiteEth MAC for the CPU.")
    parser.add_target_argument("--remote-ip",    default=None, help="TFTP server of the BIOS; required with --with-ethernet.")
    parser.add_target_argument("--tftp-port",    default=69, type=int, help="UDP port of the TFTP server of the BIOS.")
    parser.add_target_argument("--with-sdcard",  action="store_true", help="Add LiteSDCard, read only.")
    parser.add_target_argument("--with-sdcard-write", action="store_true", help="Add LiteSDCard with its write path.")
    parser.add_target_argument("--with-ac97",    action="store_true", help="Add the AC97 controller with DMA.")
    args = parser.parse_args()
    if (args.with_etherbone or args.with_ethernet) and not args.eth_ip:
        parser.error("Ethernet needs --eth-ip: an address that no station answers on the LAN")
    if args.with_etherbone and args.with_ethernet:
        parser.error("--with-etherbone and --with-ethernet exclude each other")
    if args.with_ethernet and not args.remote_ip:
        parser.error("--with-ethernet needs --remote-ip, or the BIOS asks the LAN for 192.168.1.100")

    soc = BaseSoC(sys_clk_freq=args.sys_clk_freq, speedgrade=args.speedgrade, with_bist=args.with_bist,
        with_etherbone=args.with_etherbone, with_ethernet=args.with_ethernet, eth_ip=args.eth_ip,
        remote_ip=args.remote_ip, tftp_port=args.tftp_port, with_sdcard=args.with_sdcard, with_sdcard_write=args.with_sdcard_write,
        with_ac97=args.with_ac97,
        **parser.soc_argdict)
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
