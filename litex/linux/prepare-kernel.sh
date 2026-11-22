#!/bin/sh
# Prepares a Linux 6.18 source tree for the NaxRiscv SoC (litex/linux/README.md): applies each patch
# of patches/, and copies the drivers of this directory into the tree, where the second patch gives
# them Kconfig options. Run it again after a change of a driver or a new patch. The tree records the
# patches that are in it in .adp-xc7k160-patches, because a later patch can change the lines of an
# earlier one.
#
#   sh prepare-kernel.sh /path/to/linux
set -eu
[ $# -eq 1 ] && [ -f "$1/Makefile" ] || { echo "usage: sh prepare-kernel.sh /path/to/linux" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
stamp="$1/.adp-xc7k160-patches"
touch "$stamp"
for p in "$here"/patches/*.patch; do
    name="$(basename "$p")"
    if grep -qxF "$name" "$stamp"; then
        echo "in already: $name"
    else
        patch -d "$1" -p1 -N -s < "$p"
        echo "$name" >> "$stamp"
        echo "applied:    $name"
    fi
done
install -m 644 "$here/adp-seven-seg.c"     "$1/drivers/auxdisplay/adp-seven-seg.c"
install -m 644 "$here/litex-xadc-hwmon.c"  "$1/drivers/hwmon/litex-xadc-hwmon.c"
install -m 644 "$here/adp-ac97.c"          "$1/sound/drivers/adp-ac97.c"
install -m 644 "$here/adp-ethmac.c"        "$1/drivers/net/ethernet/litex/adp-ethmac.c"
echo "copied the four drivers"
