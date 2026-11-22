#!/usr/bin/env python3
#
# Makes the DTS of the NaxRiscv SoC for a boot from the SD card (litex/linux/README.md).
#
#   python3 mkdts.py csr.json > linux.dts && dtc -O dtb -o linux.dtb linux.dts
#
# Run it with the Python of the LiteX installation. It runs litex_json2dts_linux, replaces the
# bootargs (the generator gives a static ip= and an ext4 root), and adds the nodes that the
# generator does not know: the two seven-segment digits (adp-seven-seg.c), the AC97 controller
# (adp-ac97.c), and the PMU events of NaxRiscv. The AC97 driver has fixed CSR offsets; this script
# checks them.

import argparse
import json
import re
import subprocess
import sys

sys.dont_write_bytecode = True

BOOTARGS = ("console=liteuart earlycon=liteuart,0xf0001000 rootwait root=/dev/mmcblk0p2 "
            "rootfstype=f2fs rootflags=lazytime,compress_algorithm=lz4,compress_extension=*,"
            "compress_chksum,atgc,gc_merge rw")

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
    if "ac97" in bases:
        dts += ac97_node(csr)
    if "config_cpu_type_naxriscv" in csr["constants"]:
        dts += PMU
    if "seven_seg" in bases:
        dts += SEVEN_SEG.format(base=bases["seven_seg"], left=args.dp_left_trigger,
                                right=args.dp_right_trigger)
    sys.stdout.write(dts)


if __name__ == "__main__":
    main()
