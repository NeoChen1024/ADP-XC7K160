#!/usr/bin/env python3
#
# Makes the DTS of the NaxRiscv SoC for a boot from the SD card (README section 15).
#
#   python3 mkdts.py csr.json > linux.dts && dtc -O dtb -o linux.dtb linux.dts
#
# Run it with the Python of the LiteX installation. It runs litex_json2dts_linux, replaces the
# bootargs (the generator gives a static ip= and an ext4 root), and adds the node of the two
# seven-segment digits, which the generator does not know (adp-seven-seg.c).

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
        bases = json.load(f)["csr_bases"]
    if "seven_seg" in bases:
        dts += SEVEN_SEG.format(base=bases["seven_seg"], left=args.dp_left_trigger,
                                right=args.dp_right_trigger)
    sys.stdout.write(dts)


if __name__ == "__main__":
    main()
