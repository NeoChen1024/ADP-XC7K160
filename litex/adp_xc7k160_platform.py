#
# LiteX platform for the Andes AndeShape ADP-XC7K160.
#
# The pins and the I/O standards come from ../pins.csv at import time, by the signal names of the
# manual (the manual_signal column). Thus this file has no copy of a pin number, and a finding that
# goes into pins.csv reaches the platform. The README of the repository gives the evidence for
# each resource; the section numbers below refer to it.
#
# Resources that are here:
#   clk20         OSCCLK1, 20 MHz (section 4)
#   clk200        SYSCLK, 200 MHz, differential (section 4)
#   sys_rst_n     SYS_RSTn, low while HW_RST_SW1 is pushed (section 9)
#   por_n         PORn, the reset of the peripherals on the board (section 9)
#   pwr_btn_n     GPIO0_ENn, low while PWR_SW1 is pushed (section 9)
#   serial        S1 to the DB9 connector (section 7)
#   serial_ctrl   the RTS and CTS of S1 (section 7)
#   user_btn 0-7  SW1 to SW8, with PULLUP, low while pushed (section 9)
#   seven_seg 0-1 7SEG1 (right) and 7SEG2 (left), low lights a segment (section 9)
#   bpi_ctrl      FCS_B, FOE_B, FWE_B of U4 (section 5). A design must drive all three high
#   ddram         the 32-bit DDR3, two x16 devices, 1 GiB (section 8)
#   eth_clocks    MAC_TX_CLK, MAC_RX_CLK of the 10/100 PHY, 25 MHz at 100 Mbit/s (section 11)
#   eth           the MII of the PHY, without its straps COL, CRS and RX_ER (see _eth below)
#   eth_pwrdwn    PHY_PWRDWN; low is normal operation (section 11)
#   sdcard        SD_CLK, SD_CMD, SD_DAT0 to SD_DAT3 (section 11)
#   sd_cd_n       SD_CDn, low with a card in the socket
#   sd_pwr_on     SD_PWR_ON, the enable of the socket power; 1 is on

import csv
import os

from litex.build.generic_platform import IOStandard, Misc, Pins, Subsignal
from litex.build.generic_programmer import GenericProgrammer
from litex.build.xilinx import Xilinx7SeriesPlatform

_REPOSITORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(_REPOSITORY, "pins.csv"), newline="") as f:
    _TABLE = {row["manual_signal"]: row for row in csv.DictReader(f)}


def _io(*signals, iostandard=None):
    """Pins and IOStandard of the manual signals, in the order given.

    The I/O standard comes from pins.csv. It must be the same for all the signals. Bank 16 has a
    voltage that SW11 selects, so its pins have no single standard in pins.csv: the caller gives one.
    The DDR3 pins have the standards that MIG gave them, which ran on the board (section 8).
    """
    rows = [_TABLE[s] for s in signals]
    standards = {iostandard or r["iostandard"] for r in rows}
    if len(standards) != 1 or not all(s and "/" not in s for s in standards):
        raise ValueError(f"no single I/O standard for {signals}: {standards}")
    return [Pins(" ".join(r["pin"] for r in rows)), IOStandard(standards.pop())]


def _seven_seg(n):
    return ("seven_seg", n - 1,
        Subsignal("segments", *_io(*[f"7SEG{n}_{s}" for s in "ABCDEFG"])),
        Subsignal("dp", *_io(f"7SEG{n}_P")),
    )


def _ddram():
    return ("ddram", 0,
        Subsignal("a",       *_io(*[f"DDR3_A{i}" for i in range(15)])),
        Subsignal("ba",      *_io("DDR3_BA0", "DDR3_BA1", "DDR3_BA2")),
        Subsignal("ras_n",   *_io("DDR3_RASn")),
        Subsignal("cas_n",   *_io("DDR3_CASn")),
        Subsignal("we_n",    *_io("DDR3_Wen")),
        Subsignal("cs_n",    *_io("DDR3_CSn")),
        Subsignal("dm",      *_io(*[f"DDR3_DM{i}" for i in range(4)])),
        Subsignal("dq",      *_io(*[f"DDR3_DQ{i}" for i in range(32)])),
        Subsignal("dqs_p",   *_io(*[f"DDR3_DQS{i}_P" for i in range(4)])),
        Subsignal("dqs_n",   *_io(*[f"DDR3_DQS{i}_N" for i in range(4)])),
        Subsignal("clk_p",   *_io("DDR3_CK_P")),
        Subsignal("clk_n",   *_io("DDR3_CK_N")),
        Subsignal("cke",     *_io("DDR3_CKE0")),
        Subsignal("odt",     *_io("DDR3_ODT0")),
        Subsignal("reset_n", *_io("DDR3_RST_B")),
        # No VCCAUX_IO=HIGH: the FBG676 package has no 2.0 V VccAuxIO (section 8).
        Misc("SLEW=FAST"),
    )


def _eth():
    # The PHY reads RXD0-3, CRS, COL, RX_DV, RX_ER and TX_CLK as straps when PORn releases it, after
    # the design is in operation. A pull-up on them isolates the PHY (section 11): never add PULLUP
    # here. The MII PHY of LiteEth does not use COL, CRS and RX_ER, so they are left out, and
    # Vivado puts its weak pull-down on them, which gives address 0 and MII.
    return [
        ("eth_clocks", 0,
            Subsignal("tx", *_io("MAC_TX_CLK")),
            Subsignal("rx", *_io("MAC_RX_CLK")),
        ),
        ("eth", 0,
            Subsignal("rx_dv",   *_io("MAC_RX_DV")),
            Subsignal("rx_data", *_io(*[f"MAC_RXD{i}" for i in range(4)])),
            Subsignal("tx_en",   *_io("MAC_TX_EN")),
            Subsignal("tx_data", *_io(*[f"MAC_TXD{i}" for i in range(4)])),
            Subsignal("mdc",     *_io("MAC_MDC")),
            Subsignal("mdio",    *_io("MAC_MDIO")),
        ),
        ("eth_pwrdwn", 0, *_io("PHY_PWRDWN")),
    ]


_io_list = [
    ("clk20", 0, *_io("OSCCLK1")),
    ("clk200", 0,
        Subsignal("p", *_io("SYSCLK_P")),
        Subsignal("n", *_io("SYSCLK_N")),
    ),

    ("sys_rst_n", 0, *_io("SYS_RSTn")),
    ("por_n",     0, *_io("PORn")),
    ("pwr_btn_n", 0, *_io("GPIO0_ENn")),

    ("serial", 0,
        Subsignal("tx", *_io("S1_TXD")),
        Subsignal("rx", *_io("S1_RXD")),
    ),
    ("serial_ctrl", 0,
        Subsignal("rts", *_io("S1_RTS")),
        Subsignal("cts", *_io("S1_CTS")),
    ),

    # The board has no pull-up on the buttons. Bank 16 (SW5 to SW8) is read as LVCMOS18: the input
    # then reads correctly at each voltage that SW11 can select (section 3).
    *[("user_btn", i - 1, *_io(f"GPIO{i}"), Misc("PULLUP TRUE")) for i in range(1, 5)],
    *[("user_btn", i - 1, *_io(f"GPIO{i}", iostandard="LVCMOS18"), Misc("PULLUP TRUE")) for i in range(5, 9)],

    _seven_seg(1),
    _seven_seg(2),

    ("bpi_ctrl", 0,
        Subsignal("cs_n", *_io("FCS_B")),
        Subsignal("oe_n", *_io("FOE_B")),
        Subsignal("we_n", *_io("FWE_B")),
    ),

    _ddram(),
    *_eth(),

    # The board has pull-ups on SD_CMD and SD_DAT0 to SD_DAT3, and none on SD_CLK.
    ("sdcard", 0,
        Subsignal("clk",  *_io("SD_CLK")),
        Subsignal("cmd",  *_io("SD_CMD")),
        Subsignal("data", *_io(*[f"SD_DAT{i}" for i in range(4)])),
    ),
    ("sd_cd_n",   0, *_io("SD_CDn")),
    ("sd_pwr_on", 0, *_io("SD_PWR_ON")),
]


class JTAGLoader(GenericProgrammer):
    """Loads a bitstream through ../jtag/load.sh (XVC). It cannot write a flash."""

    def load_bitstream(self, bitstream_file):
        self.call(["sh", os.path.join(_REPOSITORY, "jtag", "load.sh"), bitstream_file])

    def flash(self, address, data_file):
        raise RuntimeError("this platform does not write a flash: see section 5 of the README")


class Platform(Xilinx7SeriesPlatform):
    default_clk_name   = "clk200"
    default_clk_period = 1e9/200e6

    def __init__(self, speedgrade=-2, toolchain="vivado"):
        # The chip is marked -1. The LiteX designs are built as -2: section 2 gives the evidence.
        Xilinx7SeriesPlatform.__init__(self, f"xc7k160tfbg676{speedgrade}", _io_list,
            name="adp_xc7k160", toolchain=toolchain)
        self.speedgrade = speedgrade
        self.add_platform_command("set_property CFGBVS VCCO [current_design]")
        self.add_platform_command("set_property CONFIG_VOLTAGE 3.3 [current_design]")
        # The XVC cable is slow: a compressed bitstream loads in less time (section 5).
        self.toolchain.bitstream_commands = ["set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]"]

    def create_programmer(self):
        return JTAGLoader()

    def do_finalize(self, fragment):
        Xilinx7SeriesPlatform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk20", loose=True), 1e9/20e6)
        self.add_period_constraint(self.lookup_request("clk200", loose=True), 1e9/200e6)
        if self.lookup_request("ddram", loose=True) is not None:
            # Only bank 33 has the DCI reference resistors (section 8).
            self.add_platform_command("set_property DCI_CASCADE {{32}} [get_iobanks 33]")
