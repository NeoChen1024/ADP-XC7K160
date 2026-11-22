#!/usr/bin/env python3
"""pins.csv and package-pins.csv of this directory: the manual's pin table (manual-pins.csv, taken from
section 2.11 of UM098 v1.9) joined with Vivado's package data (package-dump.csv, from
package-dump.tcl), with what was found on the board since.

    python3 tools/tables.py tools/manual-pins.csv tools/package-dump.csv .
"""
import csv, re, sys

manual = list(csv.DictReader(open(sys.argv[1])))
package = {r["pin"]: r for r in csv.DictReader(open(sys.argv[2]))}
out = sys.argv[3]
BANK_TYPE = {"0": "config", "12": "HR", "13": "HR", "14": "HR", "15": "HR", "16": "HR",
             "32": "HP", "33": "HP", "34": "HP", "115": "GTX", "116": "GTX"}

# Pins a design has driven or read on the board, with the standard in the table.
board = {"V22", "T19", "R16", "R17", "U17", "N18", "M19", "P16", "N17", "U22", "U21", "K21", "R26", "K25", "N16", "K26", "G24", "AA10", "AB10", "T17", "R18", "P18", "U16", "C23", "M17", "L18", "T18",
         "E21", "F24", "J21", "L23", "Y5", "AA5", "AC6", "AC4", "AB5", "AB6", "L8", "N8", "R7", "R6"}
notes = {
    "D23": "X3 is not fitted: the pin follows the FPGA's weak pull",
    "H17": "measured: 32.767 kHz",
    "AA10": "200 MHz; MIG takes it as DIFF_SSTL15; alone, LVDS into an IBUFDS works too",
    "AB10": "200 MHz; MIG takes it as DIFF_SSTL15; alone, LVDS into an IBUFDS works too",
    "D6": "measured: 100 MHz, through an IBUFDS_GTE2 into a BUFG, with no GTX channel in the design",
    "D5": "measured: 100 MHz, through an IBUFDS_GTE2 into a BUFG, with no GTX channel in the design",
    "M25": "pulled up on the board; U16 answers at 0x50",
    "L25": "pulled up on the board; U16 answers at 0x50",
    "AF22": "U9.32 is MDINTR, the interrupt output of the PHY (DM9161C datasheet); masked after reset, it follows the FPGA's weak pull. Also a strap: with a pull-down the interrupt is active high, with a pull-up active low",
    "Y20": "low: the PHY runs",
    "U22": "from U19, a G692L293T: high, low while HW_RST_SW1 is held. Only an input: the button does not reconfigure the FPGA, and does not reset the Ethernet PHY",
    "U21": "from U18, a G692L293T (2.93 V, 140 to 520 ms) with the FPGA's DONE on its manual reset (U18.3): low until about 385 ms after every configuration of the FPGA, and for 0.4 s after a dip of the 3.3 V. The Ethernet PHY leaves its reset with it; neither HW_RST_SW1 nor PWR_SW1 moves it",
    "R26": "must be low as AC97_RSTn rises, or the codec enters a test mode",
    "K25": "must be low as AC97_RSTn rises, or the codec enters a test mode",
    "K21": "driven by the board: high, low while PWR_SW1 is held",
    "P26": "low holds the codec in reset: its bit clock stops",
    "V22": "the enable of U31, the socket's supply switch: high is on, and the board's pull-up leaves it on. LED11 shows the supply",
    "T19": "the SPI clock; no pull-up on the board",
    "R16": "pulled up on the board; MOSI in SPI mode. Commands and answers both ways on the card's own bus",
    "R17": "pulled up on the board; MISO in SPI mode. Blocks read and written on it, four bits wide",
    "N18": "pulled up on the board. Blocks read and written on it, four bits wide",
    "M19": "pulled up on the board. Blocks read and written on it, four bits wide",
    "U17": "pulled up on the board; the chip select in SPI mode. Blocks read and written on it, four bits wide",
    "P16": "low with a card in the socket, high without",
    "N17": "low with a card (not write-protected) in the socket, high without",
    "T17": "to the DB9 (COM1); good to 625000 baud",
    "R18": "from the DB9 (COM1); good to 1.5 Mbaud",
    "P18": "the host's RTS: low while the host asserts it",
    "U16": "driven low asserts the host's CTS",
    "D25": "probed: nothing of S2 reaches the DB9",
    "G25": "probed: nothing of S2 reaches the DB9",
    "T18": "FA25: pulled low by 330 ohm on the board; drive it to choose the flash's half",
    "C23": "drive high to leave the configuration flash alone",
    "M17": "drive high to leave the configuration flash alone",
    "L18": "drive high to leave the configuration flash alone; never low unless writing U4 is meant",
    "K18": "the same flash pin as FA23 (U4.1), through SW12, which is open: it follows the FPGA's weak pull",
    "L17": "the same flash pin as FA24 (U4.56), through SW12, which is open: it follows the FPGA's weak pull",
    "E20": "the FPGA's own A25 configuration pin, wired to the flash's busy output and not to its A25 (that is T18)",
    "G24": "20 MHz, the clock every design here ran from",
    "AE16": "bank 32 VREF, on VTT 0.75 V",
    "Y18": "bank 32 VREF, on VTT 0.75 V",
    "U9": "bank 33 VRN: the DCI reference resistor",
    "V12": "bank 33 VRP: the DCI reference resistor",
    "AC16": "LVCMOS15, as MIG sets it",
}
for r in manual:
    s, p = r["manual_signal"], r["pin"]
    if s.startswith("DDR3_") or s.startswith("FA") and s != "FA25_PU" or s.startswith("FDQ") or s.startswith("7SEG"):
        board.add(p)
    if re.fullmatch(r"GPIO[1-4]", s):
        notes[p] = "no pull-up on the board: PULLUP TRUE; low while pressed"
    if re.fullmatch(r"GPIO[5-8]", s):
        notes[p] = "bank 16; no pull-up on the board: PULLUP TRUE; low while pressed (read as LVCMOS18)"
        board.add(p)
    # What the workbench drove or read with a part answering, 2026-11-20.
    if s.startswith("MAC_") or s in ("PHY_PWRDWN", "I2C_SCL", "I2C_SDA", "RTC_32K") or s.startswith("AC97_RSTn") or s == "AC97_BCLK":
        board.add(p)
    if s in ("MAC_RXD0", "MAC_RXD1", "MAC_RXD2", "MAC_RXD3", "MAC_CRS", "MAC_COL", "MAC_RX_DV", "MAC_RX_ER", "MAC_TX_CLK"):
        notes[p] = "also a strap of the PHY, read after every load of the FPGA: no PULLUP on it"
    if s == "MAC_RX_CLK":
        notes[p] = "also a strap of the PHY, read after every load of the FPGA: a pull-down selects GPSI at 10 Mbit/s"
    if s.startswith("PHY_") and s != "PHY_PWRDWN":
        notes[p] = "U12 is not fitted" + ("; pulled up on the board" if s == "PHY_MDIO" else "")
    if r["pcb_symbol"] in ("U5", "U6"):
        # The USB parts: the footprints are empty (seen on the board, 2026-11-20), and the workbench agrees:
        # no clock on CLKOUT, every pin follows the FPGA's weak pull.
        notes[p] = r["pcb_symbol"] + " is not fitted"
    if s.startswith("7SEG"):
        notes[p] = "low lights the segment"
    if s.startswith("TRC_"):
        notes[p] = "shares the pin with " + next(o["manual_signal"] for o in manual if o["pin"] == p and o is not r)

def standard(r):
    s, std = r["manual_signal"], r["iostandard_assumed"]
    if re.fullmatch(r"DDR3_DQ\d+", s): return "SSTL15_T_DCI"
    if s.startswith("DDR3_DQS"): return "DIFF_SSTL15_T_DCI"
    if s.startswith("SYSCLK"): return "DIFF_SSTL15"
    if std.startswith("SW11"): return "LVCMOS33/25/18 (SW11)"
    return std

fields = ["pin", "bank", "bank_type", "pin_func", "manual_signal", "port_name", "iostandard", "checked",
          "pcb_symbol", "external_link", "manual_page", "note"]
with open(out + "/pins.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fields, lineterminator="\n")
    w.writeheader()
    for r in manual:
        pkg = package[r["pin"]]
        assert pkg["bank"] == r["bank"], (r, pkg)
        w.writerow({"pin": r["pin"], "bank": r["bank"], "bank_type": BANK_TYPE[r["bank"]], "pin_func": pkg["pin_func"],
                    "manual_signal": r["manual_signal"], "port_name": r["port_name"], "iostandard": standard(r),
                    "checked": "board" if r["pin"] in board and r["port_name"] or r["pin"] in ("L8", "N8", "R7", "R6") else "",
                    "pcb_symbol": r["pcb_symbol"].replace("Test Point /", "Test Point/"), "external_link": r["external_link"],
                    "manual_page": r["manual_page"], "note": notes.get(r["pin"], "")})

by_pin = {}
for r in manual:
    by_pin.setdefault(r["pin"], []).append(r["manual_signal"] or r["pcb_symbol"].replace("Test Point /", "Test Point/"))
def key(p):
    m = re.fullmatch(r"([A-Z]+)(\d+)", p)
    return (len(m[1]), m[1], int(m[2]))
with open(out + "/package-pins.csv", "w", newline="") as f:
    w = csv.writer(f, lineterminator="\n")
    w.writerow(["pin", "bank", "pin_func", "min_delay_ps", "max_delay_ps", "board"])
    for p in sorted(package, key=key):
        r = package[p]
        w.writerow([p, r["bank"], r["pin_func"], r["min_delay_ps"], r["max_delay_ps"], " / ".join(by_pin.get(p, []))])

# What the manual leaves out: I/O pins of the package with no row.
io = [p for p in sorted(package, key=key) if package[p]["pin_func"].startswith("IO_") and p not in by_pin]
print(len(package), "package pins;", len(by_pin), "in the manual; user I/O not in the manual:", len(io))
for p in io: print(" ", p, package[p]["bank"], package[p]["pin_func"])
mgt = [p for p in sorted(package, key=key) if package[p]["pin_func"].startswith("MGT") and p not in by_pin]
print("MGT pins not in the manual:", [(p, package[p]["pin_func"]) for p in mgt])
for name in ("G24", "D23", "H17", "AA10", "AB10", "U22", "U21", "H6", "H5", "D6", "D5", "U9", "V12", "AE16", "Y18", "AC16", "T18", "K18", "L17", "C23", "M17", "L18", "G7", "J7", "P6", "E20"):
    print(name, package[name]["bank"], package[name]["pin_func"])
