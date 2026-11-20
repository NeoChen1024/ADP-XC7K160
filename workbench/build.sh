#!/usr/bin/env sh
# Builds the workbench for the ADP-XC7K160 with Vivado: one design, two bitstreams, which differ in
# the weak pull on the channels.
#
#   [VIVADO=/path/to/vivado] [WORK=dir] [GRADE=1] sh build.sh
#
# Everything is written under WORK, ./adp-workbench in the current directory by default:
# workbench-pullup.bit, workbench-pulldown.bit and the reports. About five minutes.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
renice -n 19 -p $$ >/dev/null 2>&1 || true
vivado="${VIVADO:-vivado}"
part="xc7k160tfbg676-${GRADE:-1}"
mkdir -p "${WORK:-adp-workbench}"
out="$(cd "${WORK:-adp-workbench}" && pwd)"
rm -f "$out"/workbench-pull*.bit

python3 "$here/board.py" "$here/../pins.csv" "$out"
cd "$out"
"$vivado" -mode batch -nojournal -log build.log -source "$here/build.tcl" \
    -tclargs "$part" "$here/workbench.xdc" "$out/pins.xdc" \
    "$out/workbench_pkg.sv" "$here/../example-ddr3-test/rtl/uart.sv" "$here/rtl/adp_xc7k160_workbench.sv" >build.out 2>&1 </dev/null || true
if [ ! -f workbench-pulldown.bit ]; then
    echo "Vivado failed; see $out/build.log" >&2
    grep -E '^ERROR' build.log | head -5 >&2 || true
    exit 1
fi

# Timing that is not met still makes a bitstream: say so, and let the caller decide.
awk '/WNS\(ns\) +TNS\(ns\)/ { getline; getline; printf "setup slack %s ns, hold slack %s ns, pulse width slack %s ns\n", $1, $5, $9; exit }' timing.rpt
if grep -q 'Timing constraints are not met' timing.rpt; then
    echo "timing is not met; see $out/timing.rpt" >&2
    exit 3
fi
echo "$out/workbench-pullup.bit"
echo "$out/workbench-pulldown.bit"
