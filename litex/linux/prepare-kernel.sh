#!/bin/sh
# Prepares a Linux 6.18 source tree for the NaxRiscv SoC (litex/linux/README.md): applies each patch
# of patches/, and copies the drivers of this directory into the tree, where the second patch gives
# them Kconfig options. Run it again after a change of a driver; a patch that is in already is kept.
#
#   sh prepare-kernel.sh /path/to/linux
set -eu
[ $# -eq 1 ] && [ -f "$1/Makefile" ] || { echo "usage: sh prepare-kernel.sh /path/to/linux" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
for p in "$here"/patches/*.patch; do
    if patch -d "$1" -p1 -R --dry-run -s -f < "$p" >/dev/null 2>&1; then
        echo "in already: $(basename "$p")"
    else
        patch -d "$1" -p1 -N -s < "$p"
        echo "applied:    $(basename "$p")"
    fi
done
install -m 644 "$here/adp-seven-seg.c"     "$1/drivers/auxdisplay/adp-seven-seg.c"
install -m 644 "$here/litex-xadc-hwmon.c"  "$1/drivers/hwmon/litex-xadc-hwmon.c"
install -m 644 "$here/adp-ac97.c"          "$1/sound/drivers/adp-ac97.c"
echo "copied the three drivers"
