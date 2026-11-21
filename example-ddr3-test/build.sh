#!/usr/bin/env sh
# Builds the DDR3 test for the ADP-XC7K160 with Vivado: MIG at one rate, and the tester over it.
#
#   [VIVADO=/path/to/vivado] [WORK=dir] [DDR3=1600] [GRADE=2] sh build.sh
#
# Everything is written under WORK, ./adp-ddr3-test in the current directory by default: MIG in
# WORK/mig-<rate>, generated once for each rate, and the reports and ddr3-test.bit in WORK/build.
# The part is built as speed grade -2 unless GRADE says otherwise: ../docs/fpga.md says why.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
renice -n 19 -p $$ >/dev/null 2>&1 || true
vivado="${VIVADO:-vivado}"
rate="${DDR3:-1600}"
part="xc7k160tfbg676-${GRADE:-2}"
mkdir -p "${WORK:-adp-ddr3-test}"
work="$(cd "${WORK:-adp-ddr3-test}" && pwd)"

user_design="$(VIVADO="$vivado" sh "$here/../mig/generate.sh" "$rate" "$work/mig-$rate")"

out="$work/build"
rm -rf "$out"
mkdir -p "$out"
cd "$out"
top=adp_xc7k160_ddr3_test
"$vivado" -mode batch -nojournal -log build.log -source "$here/build.tcl" \
    -tclargs "$top" "$part" "$user_design" "$here/ddr3-test.xdc" "$here"/rtl/*.sv >build.out 2>&1 </dev/null || true
if [ ! -f "$top.bit" ]; then
    echo "Vivado failed; see $out/build.log" >&2
    grep -E '^ERROR' build.log | head -5 >&2 || true
    exit 1
fi
mv "$top.bit" ddr3-test.bit

# Timing that is not met still makes a bitstream: say so, and let the caller decide.
awk '/WNS\(ns\) +TNS\(ns\)/ { getline; getline; printf "setup slack %s ns, hold slack %s ns, pulse width slack %s ns\n", $1, $5, $9; exit }' timing.rpt
if grep -q 'Timing constraints are not met' timing.rpt; then
    echo "timing is not met; see $out/timing.rpt" >&2
    exit 3
fi
echo "$out/ddr3-test.bit"
