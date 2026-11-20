#!/usr/bin/env python3
"""The workbench's channels: every pin it watches, and which of them it may drive.

    board.py <pins.csv> <directory>      writes workbench_pkg.sv and pins.xdc there, for build.sh

workbench.py reads the same table, so a channel has one number everywhere.

A channel is an input that is sampled at 400 MHz: its level, whether it was seen low and seen high
since the last look, and how many times it rose in each second. Most are only ever read, with the
FPGA's own weak pull on them: a pull-up in one bitstream and a pull-down in the other, so that a pin
that follows the pull is one nothing drives. A few are lines the FPGA owns on the board (a bus clock,
a reset, a chip select) and can be driven on command; nothing is driven until the host says so.
"""
import csv
import sys

# (name, manual signal or package pin, group, what it may do)
#   ""      read only, pull as the bitstream's variant
#   "drive" may be driven
#   "od"    may be driven low only (open drain)
#   "+down" a pull-down in both bitstreams: a line whose high would switch something off
CHANNELS = [
    # What is known already, to show that the method reads what it should.
    ("SW1", "GPIO1", "control", ""), ("SW2", "GPIO2", "control", ""), ("SW3", "GPIO3", "control", ""),
    ("SW4", "GPIO4", "control", ""),
    ("FA25", "FA25_PU", "control", ""),              # 330 ohm to ground on the board
    ("VRN_33", "U9", "control", ""),                 # the manual: PULL_UP
    ("VRP_33", "V12", "control", ""),                # the manual: PULL_DOWN
    ("TP59", "T7", "control", ""), ("TP60", "U4", "control", ""),     # pads with nothing on them
    ("CFC_NCD1", "CFC_NCD1 (PULL UP)", "control", ""), ("CFC_NCD2", "CFC_NCD2 (PULL UP)", "control", ""),
    ("HOST_RTS", "S1_CTS", "control", ""),

    # Buttons, resets, switches.
    ("SW5", "GPIO5", "buttons", ""), ("SW6", "GPIO6", "buttons", ""), ("SW7", "GPIO7", "buttons", ""),
    ("SW8", "GPIO8", "buttons", ""),
    ("PORn", "PORn", "buttons", ""), ("SYS_RSTn", "SYS_RSTn", "buttons", ""), ("GPIO0_ENn", "GPIO0_ENn", "buttons", ""),
    ("PCIE_PERST_N", "PCIE_PERST_N", "buttons", ""),

    # Clocks on ordinary pins.
    ("OSCCLK2", "OSCCLK2", "clocks", ""), ("RTC_32K", "RTC_32K", "clocks", ""),

    # The configuration flash's side pins. FA23 and FA24 are driven to see whether RS0 and RS1 follow (SW12).
    ("F_BUSY_B", "F_BUSY_B", "flash", ""), ("RS0", "RS0", "flash", ""), ("RS1", "RS1", "flash", ""),
    ("FA23", "FA23", "flash", "drive"), ("FA24", "FA24", "flash", "drive"),

    # The serial port's other lines.
    ("S2_RXD", "S2_RXD", "uart", ""), ("S2_TXD", "S2_TXD", "uart", "drive"), ("S1_RTS", "S1_RTS", "uart", "drive"),

    # The I2C EEPROM.
    ("I2C_SCL", "I2C_SCL", "i2c", "od"), ("I2C_SDA", "I2C_SDA", "i2c", "od"),

    # The SD card. As SPI, DAT3 is the chip select, CMD the data in, DAT0 the data out; on its own bus
    # all four data lines go both ways, so all of them may be driven.
    ("SD_CDn", "SD_CDn", "sd", ""), ("SD_WPn", "SD_WPn", "sd", ""),
    ("SD_PWR_ON", "SD_PWR_ON", "sd", "drive"), ("SD_CLK", "SD_CLK", "sd", "drive"), ("SD_CMD", "SD_CMD", "sd", "drive"),
    ("SD_DAT0", "SD_DAT0", "sd", "drive"), ("SD_DAT1", "SD_DAT1", "sd", "drive"), ("SD_DAT2", "SD_DAT2", "sd", "drive"),
    ("SD_DAT3", "SD_DAT3", "sd", "drive"),

    # The 10/100 Ethernet PHY. Its transmit lines are the design's own outputs, not channels.
    ("MAC_RX_CLK", "MAC_RX_CLK", "eth", ""), ("MAC_RX_DV", "MAC_RX_DV", "eth", ""), ("MAC_RX_ER", "MAC_RX_ER", "eth", ""),
    ("MAC_RXD0", "MAC_RXD0", "eth", ""), ("MAC_RXD1", "MAC_RXD1", "eth", ""), ("MAC_RXD2", "MAC_RXD2", "eth", ""),
    ("MAC_RXD3", "MAC_RXD3", "eth", ""),
    ("MAC_TX_CLK", "MAC_TX_CLK", "eth", ""), ("MAC_CRS", "MAC_CRS", "eth", ""), ("MAC_COL", "MAC_COL", "eth", ""),
    ("MAC_PHY_LINKSTS", "MAC_PHY_LINKSTS", "eth", ""),
    ("MAC_MDC", "MAC_MDC", "eth", "drive"), ("MAC_MDIO", "MAC_MDIO", "eth", "drive"),
    ("PHY_PWRDWN", "PHY_PWRDWN", "eth", "drive+down"),

    # The gigabit PHY, which the manual calls optional.
    ("PHY_RX_CLK", "PHY_RX_CLK", "gbe", ""), ("PHY_TX_CLK", "PHY_TX_CLK", "gbe", ""), ("PHY_RXDV", "PHY_RXDV", "gbe", ""),
    ("PHY_RXER", "PHY_RXER", "gbe", ""), ("PHY_CRS", "PHY_CRS", "gbe", ""), ("PHY_COL", "PHY_COL", "gbe", ""),
    ("PHY_RXD0", "PHY_RXD0", "gbe", ""), ("PHY_RXD1", "PHY_RXD1", "gbe", ""), ("PHY_RXD2", "PHY_RXD2", "gbe", ""),
    ("PHY_RXD3", "PHY_RXD3", "gbe", ""),
    ("PHY_MDC", "PHY_MDC", "gbe", "drive"), ("PHY_MDIO", "PHY_MDIO", "gbe", "drive"),

    # The USB parts, optional too: what they would drive.
    ("USB_CLKOUT", "CLKOUT", "usb", ""), ("USB_LINESTATE0", "LINESTATE0", "usb", ""), ("USB_LINESTATE1", "LINESTATE1", "usb", ""),
    ("USB_RXVALID", "RXVALID", "usb", ""), ("USB_RXACTIVE", "RXACTIVE", "usb", ""), ("USB_RXERROR", "RXERROR", "usb", ""),
    ("USB_TXREADY", "TXREADY", "usb", ""), ("USB_VBUSVLD", "VBUSVLD", "usb", ""), ("USB_SESSVLD", "SESSVLD", "usb", ""),
    ("USB_SESSEND", "SESSEND", "usb", ""), ("USB_IDDIG", "IDDIG", "usb", ""), ("USB_HOSTDISC", "HOSTDISC", "usb", ""),
    ("USB_FAULTn", "USB_FAULTn", "usb", ""),
    ("U_RCV", "U_RCV", "usb", ""), ("U_VP", "U_VP", "usb", ""), ("U_VM", "U_VM", "usb", ""),

    # The audio codec: out of reset it gives a 12.288 MHz bit clock.
    ("AC97_RSTn", "AC97_RSTn/I2S_MCLK", "audio", "drive+down"), ("AC97_BCLK", "AC97_BCLK/I2S_SCLK", "audio", ""),
    ("AC97_SDI", "AC97_SDI/I2S_RXD", "audio", ""),

    # I/O of the two memory banks that no table of the manual has.
    ("VRN_32", "V13", "unlisted", ""), ("VRP_32", "W13", "unlisted", ""), ("AB20", "AB20", "unlisted", ""),
] + [(pin, pin, "unlisted", "") for pin in (
    "Y12", "Y13", "AA12", "AA13", "AB11", "AC11", "AC13", "AD10", "AD11", "AD13",
    "AE8", "AE10", "AE11", "AE12", "AE13", "AF8", "AF9", "AF10", "AF12", "AF13")]

# Channels with no pin: clocks on dedicated inputs, divided by 16 on their own clock.
INTERNAL = [
    ("SYSCLK/16", "the 200 MHz oscillator U22, taken as LVDS"),
    ("CKOUT1/16", "the 100 MHz oscillator U14, through the GTX reference input of bank 116"),
    ("PCIE_CLK/16", "the PCIe slot's reference clock, bank 115"),
]
DIVIDED = 16

NAMES = [c[0] for c in CHANNELS] + [c[0] for c in INTERNAL]
INDEX = {name: at for at, name in enumerate(NAMES)}
GROUP = {c[0]: c[2] for c in CHANNELS} | {c[0]: "clocks" for c in INTERNAL}
MAGIC = 0xADB00003

STANDARD = {"12": "LVCMOS33", "13": "LVCMOS33", "14": "LVCMOS33", "15": "LVCMOS33",
            # Bank 16 is 1.8, 2.5 or 3.3 V by SW11. An LVCMOS18 input's thresholds come from the 1.8 V
            # auxiliary supply, so it reads right at any of them; nothing in this bank is driven.
            "16": "LVCMOS18", "34": "LVCMOS18", "32": "LVCMOS15", "33": "LVCMOS15"}


def pins(path):
    """Channel name to (package pin, bank)."""
    by_signal, by_pin = {}, {}
    for row in csv.DictReader(open(path)):
        by_signal.setdefault(row["manual_signal"], (row["pin"], row["bank"]))
        by_pin[row["pin"]] = (row["pin"], row["bank"])
    package = {row["pin"]: (row["pin"], row["bank"])
               for row in csv.DictReader(open(path.replace("pins.csv", "package-pins.csv")))}
    out = {}
    for name, where, _, _ in CHANNELS:
        out[name] = by_signal.get(where) or by_pin.get(where) or package[where]
    assert len({pin for pin, _ in out.values()}) == len(CHANNELS), "a pin is used twice"
    return out


def generate(pins_csv, directory):
    where = pins(pins_csv)
    npin = len(CHANNELS)
    mask = lambda test: f"{npin}'b" + "".join("1" if test(c[3]) else "0" for c in reversed(CHANNELS))
    with open(directory + "/workbench_pkg.sv", "w") as f:
        f.write("// Written by board.py: the workbench's channels.\npackage wb;\n")
        f.write(f"    localparam int NPIN = {npin};\n    localparam int NINT = {len(INTERNAL)};\n")
        f.write("    localparam int NCH = NPIN + NINT;\n")
        f.write(f"    localparam logic [31:0] MAGIC = 32'h{MAGIC:08X};\n")
        f.write(f"    localparam logic [NPIN-1:0] CAN_DRIVE = {mask(lambda k: 'drive' in k or k == 'od')};\n")
        f.write(f"    localparam logic [NPIN-1:0] OPEN_DRAIN = {mask(lambda k: k == 'od')};\n")
        for name in ("MAC_RX_CLK", "MAC_RX_DV", "MAC_RX_ER", "MAC_RXD0", "MAC_RXD1", "MAC_RXD2", "MAC_RXD3", "MAC_TX_CLK", "AC97_BCLK", "AC97_SDI"):
            f.write(f"    localparam int CH_{name} = {INDEX[name]};\n")
        f.write("endpackage\n")
    with open(directory + "/pins.xdc", "w") as f:
        f.write("# Written by board.py: the workbench's channels. A pin with PULLUP here has a pull-down in the\n"
                "# second bitstream, which build.tcl writes from the same routed design.\n")
        for at, (name, _, _, kind) in enumerate(CHANNELS):
            pin, bank = where[name]
            pull = "PULLDOWN TRUE" if "+down" in kind else "PULLUP TRUE"
            f.write(f"set_property -dict {{PACKAGE_PIN {pin} IOSTANDARD {STANDARD[bank]} {pull}}} [get_ports {{pin[{at}]}}]  ;# {name}, bank {bank}\n")


if __name__ == "__main__":
    generate(sys.argv[1], sys.argv[2])
