#!/usr/bin/env python3
#
# Makes the DTS of the NaxRiscv SoC for a boot from the SD card (litex/linux/README.md).
#
#   python3 mkdts.py csr.json > linux.dts && dtc -O dtb -o linux.dtb linux.dts
#
# Run it with the Python of the LiteX installation. It runs litex_json2dts_linux, replaces the
# bootargs (the generator gives a static ip= and an ext4 root), and adds the nodes that the
# generator does not know: the two seven-segment digits (adp-seven-seg.c), the AC97 controller
# (adp-ac97.c), the Ethernet MAC with DMA (adp-ethmac.c), and the PMU events of NaxRiscv. The two
# drivers have fixed CSR offsets; this script checks them. It also lets the SD card run in high
# speed mode (at most 50 MHz).

import argparse
import json
import re
import subprocess
import sys

sys.dont_write_bytecode = True

BOOTARGS = ("console=liteuart earlycon=liteuart,0xf0001000 rootwait root=/dev/mmcblk0p2 "
            "rootfstype=f2fs rootflags=lazytime,compress_algorithm=lz4,compress_extension=*,"
            "compress_chksum,atgc,gc_merge rw")

# High speed mode: Linux switches the card to it, and asks for 50 MHz in place of 25 MHz. The
# divider of LiteSDCard gives the system clock divided by an even number, not more than that.
# litex/linux/README.md gives the test of 2026-11-22.
SD_HIGH_SPEED = """
&mmc0 {
    cap-sd-highspeed;
};
"""

SEVEN_SEG = """
&{{/soc}} {{
    seven_seg: seven-seg@{base:x} {{
        compatible = "adp-xc7k160,seven-seg";
        reg = <0x{base:x} 0x4>;
        #address-cells = <1>;
        #size-cells = <0>;

        led@0 {{
            reg = <0>;
            label = "adp:dp-left";
            linux,default-trigger = "{left}";
        }};

        led@1 {{
            reg = <1>;
            label = "adp:dp-right";
            linux,default-trigger = "{right}";
        }};
    }};
}};
"""


AC97 = """
&{{/soc}} {{
    ac97: ac97@{base:x} {{
        compatible = "adp-xc7k160,ac97";
        reg = <0x{base:x} 0x{size:x}>;
        interrupts = <{irq}>;
    }};
}};
"""

# The MAC address is the locally administered address of the workbench, as for LiteEth.
ETHMAC = """
&{{/soc}} {{
    ethmac: ethernet@{base:x} {{
        compatible = "adp-xc7k160,ethmac";
        reg = <0x{base:x} 0x{size:x}>{mdio_reg};
        interrupts = <{irq}>;
        local-mac-address = [02 ad b0 00 00 01];{mdio_node}
    }};
}};
"""

# The MDIO CSRs of LiteEth (ethphy_mdio_w, ethphy_mdio_r) are the second register range. The
# DM9161 answers at address 0 (docs/ethernet.md).
ETHMAC_MDIO_REG = ", <0x{mdio:x} 0x8>"
ETHMAC_MDIO_NODE = """
        phy-mode = "mii";
        phy-handle = <&ethphy0>;
        mdio {
            #address-cells = <1>;
            #size-cells = <0>;
            ethphy0: ethernet-phy@0 {
                reg = <0>;
            };
        };"""

# The hardware events of NaxRiscv (PerformanceCounterService in its Service.scala): 1 I$ refill,
# 2 D$ refill, 3 D$ writeback, 4 branch miss. mhpmcounter3 to 6 (mask 0x78) can count each of them.
# OpenSBI reads this node. Cycles and instructions must be mapped to mcycle and minstret, or perf
# does not count them. "perf stat -e branch-misses" uses event 4, and "r1" to "r7" are raw events.
# The raw mask must not be 0: OpenSBI then takes the raw row as an overlap of the other rows.
PMU = """
/ {
    pmu {
        compatible = "riscv,pmu";
        riscv,event-to-mhpmevent = <0x00006 0x0 0x4>;
        riscv,event-to-mhpmcounters = <0x00001 0x00001 0x01>,
                                      <0x00002 0x00002 0x04>,
                                      <0x00006 0x00006 0x78>;
        riscv,raw-event-to-mhpmcounters = <0x0 0x0 0xffffffff 0xfffffff8 0x78>;
    };
};
"""


# The CSR offsets that adp-ac97.c uses.
AC97_OFFSETS = {
    "ctrl": 0x00, "cmd": 0x04, "cmd_go": 0x08, "status": 0x0c, "reply": 0x10,
    "play_base": 0x14, "play_length": 0x1c, "play_enable": 0x20, "play_loop": 0x28, "play_offset": 0x2c,
    "rec_base": 0x34, "rec_length": 0x3c, "rec_enable": 0x40, "rec_loop": 0x48, "rec_offset": 0x4c,
    "play_period": 0x54, "rec_period": 0x58,
    "ev_status": 0x5c, "ev_pending": 0x60, "ev_enable": 0x64,
}


# The CSR offsets that adp-ethmac.c uses.
ETHMAC_OFFSETS = {
    "rx_buf": 0x00, "rx_bufs": 0x04, "rx_stat": 0x08, "rx_csum": 0x0c, "rx_pop": 0x10,
    "rx_dropped": 0x14, "tx_addr": 0x18, "tx_ctrl": 0x1c, "tx_room": 0x20, "tx_done": 0x24,
    "ev_status": 0x28, "ev_pending": 0x2c, "ev_enable": 0x30,
    "core_rx_datapath_preamble_errors": 0x38, "core_rx_datapath_crc_errors": 0x3c,
}

# The CSRs of the PTP clock, the TX stamps, the filter and the receive diagnostics, if the SoC has
# them. adp-ethmac.c finds them by the size of the register range.
ETHMAC_PTP_OFFSETS = {
    "ptp_ctrl": 0x40, "ptp_snap_sec_hi": 0x44, "ptp_snap_sec": 0x48, "ptp_snap_ns": 0x4c,
    "ptp_set_sec_hi": 0x50, "ptp_set_sec": 0x54, "ptp_set_ns": 0x58, "ptp_adj": 0x5c,
    "ptp_incr_hi": 0x60, "ptp_incr": 0x64, "ptp_incr_nom_hi": 0x68, "ptp_incr_nom": 0x6c,
    "txts_stat": 0x70, "txts_sec": 0x74, "txts_ns": 0x78, "txts_pop": 0x7c, "txts_lost": 0x80,
    "filt_ctrl": 0x84, **{f"filt_addr{i}_hi": 0x88 + 8 * i for i in range(4)},
    **{f"filt_addr{i}": 0x8c + 8 * i for i in range(4)},
    "filt_hash_hi": 0xa8, "filt_hash": 0xac, "filt_dropped": 0xb0,
    "rxdiag_no_sfd": 0xb4, "rxdiag_bad_pre": 0xb8, "rxdiag_dribble": 0xbc, "rxdiag_last": 0xc0,
}
ETHMAC_PTP_END = 0xc4


def checked_node(csr, name, offsets, driver, template, **extra):
    base = csr["csr_bases"][name]
    regs = csr["csr_registers"]
    for reg, offset in offsets.items():
        got = regs[f"{name}_{reg}"]["addr"] - base
        if got != offset:
            sys.exit(f"{name}_{reg} is at +0x{got:x}, and {driver} expects +0x{offset:x}")
    end = max(r["addr"] + 4 * r["size"] for n, r in regs.items() if n.startswith(f"{name}_"))
    return template.format(base=base, size=end - base, irq=csr["constants"][f"{name}_interrupt"],
                           **extra)


def ethmac_mdio(csr):
    """The MDIO range and the PHY node of the MAC with DMA, or nothing without MDIO."""
    regs = csr["csr_registers"]
    if "ethphy_mdio_w" not in regs:
        return {"mdio_reg": "", "mdio_node": ""}
    mdio = regs["ethphy_mdio_w"]["addr"]
    if regs["ethphy_mdio_r"]["addr"] != mdio + 4:
        sys.exit("ethphy_mdio_r does not follow ethphy_mdio_w, as adp-ethmac.c expects")
    return {"mdio_reg": ETHMAC_MDIO_REG.format(mdio=mdio), "mdio_node": ETHMAC_MDIO_NODE}


def ac97_node(csr):
    base = csr["csr_bases"]["ac97"]
    regs = csr["csr_registers"]
    for name, offset in AC97_OFFSETS.items():
        got = regs[f"ac97_{name}"]["addr"] - base
        if got != offset:
            sys.exit(f"ac97_{name} is at +0x{got:x}, and adp-ac97.c expects +0x{offset:x}")
    end = max(r["addr"] + 4 * r["size"] for n, r in regs.items() if n.startswith("ac97_"))
    return AC97.format(base=base, size=end - base, irq=csr["constants"]["ac97_interrupt"])


def main():
    parser = argparse.ArgumentParser(description="DTS of the NaxRiscv SoC for a boot from the SD card.")
    parser.add_argument("csr_json")
    parser.add_argument("--bootargs", default=BOOTARGS)
    parser.add_argument("--dp-left-trigger",  default="mmc0")
    parser.add_argument("--dp-right-trigger", default="heartbeat")
    args = parser.parse_args()

    dts = subprocess.run([sys.executable, "-m", "litex.tools.litex_json2dts_linux",
                          "--initrd", "disabled", "--root-device", "mmcblk0p2", args.csr_json],
                         check=True, capture_output=True, text=True).stdout
    dts, count = re.subn(r'bootargs = ".*";', lambda m: f'bootargs = "{args.bootargs}";', dts)
    if count != 1:
        sys.exit("no single bootargs line in the output of litex_json2dts_linux")

    with open(args.csr_json) as f:
        csr = json.load(f)
    bases = csr["csr_bases"]
    if "sdcard" in bases:
        dts += SD_HIGH_SPEED
    if "ac97" in bases:
        dts += ac97_node(csr)
    if "ethdma" in bases:
        offsets = dict(ETHMAC_OFFSETS)
        if "ethdma_ptp_ctrl" in csr["csr_registers"]:
            offsets.update(ETHMAC_PTP_OFFSETS)
            if csr["csr_registers"]["ethdma_rxdiag_last"]["addr"] + 4 - bases["ethdma"] != ETHMAC_PTP_END:
                sys.exit("the CSRs of ethdma do not end where adp-ethmac.c expects")
        dts += checked_node(csr, "ethdma", offsets, "adp-ethmac.c", ETHMAC, **ethmac_mdio(csr))
    if "config_cpu_type_naxriscv" in csr["constants"]:
        dts += PMU
    if "seven_seg" in bases:
        dts += SEVEN_SEG.format(base=bases["seven_seg"], left=args.dp_left_trigger,
                                right=args.dp_right_trigger)
    sys.stdout.write(dts)


if __name__ == "__main__":
    main()
